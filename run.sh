#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[run] $*"; }
die() { echo "[run] ERROR: $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed."
}

resolve_abs_path() {
    local path="$1"
    python3 - "$path" <<'PY'
import os, sys
print(os.path.abspath(sys.argv[1]))
PY
}

find_site_root() {
    local base="$1"

    if [ -d "$base/app/public" ]; then
        echo "$base"
        return 0
    fi

    local app_dir
    app_dir=$(find "$base" -maxdepth 3 -type d -path '*/app/public' 2>/dev/null | head -n1 || true)
    if [ -n "$app_dir" ]; then
        dirname "$(dirname "$app_dir")"
        return 0
    fi

    return 1
}

build_import_zip() {
    local site_root="$1"
    local out_zip="$2"

    [ -d "$site_root/app/public" ] || die "Expected '$site_root/app/public' to exist."
    [ -d "$site_root/app/sql" ] || mkdir -p "$site_root/app/sql"

    (cd "$site_root" && zip -qr "$out_zip" app)
}

export_site_state() {
    local compose_project="$1"
    local compose_files=("$2" "$3")
    local output_path="$4"

    local compose_cmd=(docker compose --project-name "$compose_project" -f "${compose_files[0]}" -f "${compose_files[1]}")

    local wp_container
    wp_container=$("${compose_cmd[@]}" ps -q wordpress 2>/dev/null | head -n1 || true)
    if [ -z "$wp_container" ]; then
        wp_container=$("${compose_cmd[@]}" ps -q wordpress-fpm 2>/dev/null | head -n1 || true)
    fi

    local db_container
    db_container=$("${compose_cmd[@]}" ps -q db 2>/dev/null | head -n1 || true)

    [ -n "$wp_container" ] || die "Could not find a running WordPress container."
    [ -n "$db_container" ] || die "Could not find a running database container."

    local tmpdir
    tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/app/public" "$tmpdir/app/sql"

    docker cp "${wp_container}:/var/www/html/." "$tmpdir/app/public/"
    rm -rf "$tmpdir/app/public/.localwp-docker-init-done" "$tmpdir/app/public/wp-content/cache"

    local db_name db_user db_pass
    db_name=$(docker exec "$db_container" printenv MYSQL_DATABASE)
    db_user=$(docker exec "$db_container" printenv MYSQL_USER)
    db_pass=$(docker exec "$db_container" printenv MYSQL_PASSWORD)

    docker exec "$db_container" mysqldump -u"$db_user" -p"$db_pass" "$db_name" > "$tmpdir/app/sql/local.sql"

    (cd "$tmpdir" && zip -qr "$output_path" app)
    rm -rf "$tmpdir"
}

sync_zip_to_dir() {
    local zip_path="$1"
    local target_dir="$2"

    local tmpdir
    tmpdir=$(mktemp -d)
    unzip -q "$zip_path" -d "$tmpdir"

    [ -d "$tmpdir/app" ] || die "Export archive did not contain app/ directory."

    rm -rf "$target_dir/app"
    cp -R "$tmpdir/app" "$target_dir/app"
    rm -rf "$tmpdir"
}

prompt_save() {
    local reply
    read -r -p "Save site changes back to the source target? [y/N] " reply || true
    case "${reply,,}" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

require_cmd docker
require_cmd zip
require_cmd unzip
require_cmd python3

TARGET_INPUT="$PWD"
UP_ARGS=()

if [ "$#" -gt 0 ]; then
    if [[ "$1" == -* ]]; then
        UP_ARGS=("$@")
    else
        TARGET_INPUT="$1"
        shift
        UP_ARGS=("$@")
    fi
fi

TARGET_INPUT=$(resolve_abs_path "$TARGET_INPUT")

MODE=""
SITE_ROOT=""
SOURCE_ZIP=""
SOURCE_DIR=""

TEMP_SITE_DIR=""
if [ -f "$TARGET_INPUT" ] && [[ "$TARGET_INPUT" == *.zip ]]; then
    MODE="zip"
    SOURCE_ZIP="$TARGET_INPUT"
    TEMP_SITE_DIR=$(mktemp -d)
    unzip -q "$SOURCE_ZIP" -d "$TEMP_SITE_DIR"
    SITE_ROOT=$(find_site_root "$TEMP_SITE_DIR") || die "Could not locate app/public inside zip: $SOURCE_ZIP"
elif [ -d "$TARGET_INPUT" ]; then
    MODE="dir"
    SOURCE_DIR="$TARGET_INPUT"
    SITE_ROOT=$(find_site_root "$SOURCE_DIR") || die "Could not locate app/public under directory: $SOURCE_DIR"
else
    die "Target must be a LocalWP zip file or a directory containing app/public."
fi

safe_name=$(basename "$SITE_ROOT" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')
path_hash=$(printf '%s' "$SITE_ROOT" | sha1sum | cut -c1-8)
PROJECT_NAME="${LOCALWP_PROJECT_NAME:-localwp-${safe_name}-${path_hash}}"

RUNTIME_DIR=$(mktemp -d)
IMPORT_DIR="$RUNTIME_DIR/import"
IMPORT_ZIP="$IMPORT_DIR/site.zip"
OVERRIDE_FILE="$RUNTIME_DIR/docker-compose.run.yml"

mkdir -p "$IMPORT_DIR"
build_import_zip "$SITE_ROOT" "$IMPORT_ZIP"

cat > "$OVERRIDE_FILE" <<OVERRIDE
services:
  wordpress:
    volumes:
      - $IMPORT_DIR:/import:ro
  wordpress-fpm:
    volumes:
      - $IMPORT_DIR:/import:ro
OVERRIDE

compose_cmd=(docker compose --project-name "$PROJECT_NAME" -f "$SCRIPT_DIR/docker-compose.yml" -f "$OVERRIDE_FILE")

cleanup() {
    rm -rf "$RUNTIME_DIR"
    if [ -n "$TEMP_SITE_DIR" ]; then
        rm -rf "$TEMP_SITE_DIR"
    fi
}
trap cleanup EXIT

log "Using compose project: $PROJECT_NAME"
if [ "$MODE" = "zip" ]; then
    log "Source zip: $SOURCE_ZIP"
else
    log "Source directory: $SOURCE_DIR"
fi

"${compose_cmd[@]}" up -d "${UP_ARGS[@]}"
log "Containers are running. Streaming logs (Ctrl+C to stop and continue)."

set +e
"${compose_cmd[@]}" logs -f
log_exit_code=$?
set -e

saved=false
if prompt_save; then
    tmp_export_zip="$RUNTIME_DIR/export.zip"
    log "Exporting current site state …"
    export_site_state "$PROJECT_NAME" "$SCRIPT_DIR/docker-compose.yml" "$OVERRIDE_FILE" "$tmp_export_zip"

    if [ "$MODE" = "zip" ]; then
        cp "$tmp_export_zip" "$SOURCE_ZIP"
        log "Updated zip written to $SOURCE_ZIP"
    else
        sync_zip_to_dir "$tmp_export_zip" "$SOURCE_DIR"
        log "Updated files written to $SOURCE_DIR/app"
    fi
    saved=true
else
    log "Changes were not saved."
fi

log "Stopping containers …"
"${compose_cmd[@]}" down >/dev/null

if [ "$saved" = true ]; then
    log "Done."
fi

if [ "$log_exit_code" -ne 0 ] && [ "$log_exit_code" -ne 130 ]; then
    exit "$log_exit_code"
fi
