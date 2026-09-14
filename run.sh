#!/usr/bin/env bash
set -euo pipefail


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
    local output_path="$2"
    shift 2

    local compose_cmd=(docker compose --project-name "$compose_project")
    local f
    for f in "$@"; do
        compose_cmd+=(-f "$f")
    done

    local wp_container
    wp_container=$("${compose_cmd[@]}" ps -q wordpress 2>/dev/null | head -n1 || true)
    if [ -z "$wp_container" ]; then
        wp_container=$("${compose_cmd[@]}" ps -q wordpress-fpm 2>/dev/null | head -n1 || true)
    fi

    [ -n "$wp_container" ] || die "Could not find a running WordPress container."

    local tmpdir
    tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/app/public" "$tmpdir/app/sql"

    docker cp "${wp_container}:/var/www/html/." "$tmpdir/app/public/"
    rm -rf "$tmpdir/app/public/.localwp-docker-init-done" "$tmpdir/app/public/wp-content/cache"

    dump_database "$tmpdir/app/sql/local.sql"

    (cd "$tmpdir" && zip -qr "$output_path" app)
    rm -rf "$tmpdir"
}

dump_database() {
    local output_path="$1"
    local db_container db_name db_user db_pass
    db_container=$("${compose_cmd[@]}" ps -q db 2>/dev/null | head -n1 || true)
    [ -n "$db_container" ] || die "Could not find a running database container."
    db_name=$(docker exec "$db_container" printenv MYSQL_DATABASE)
    db_user=$(docker exec "$db_container" printenv MYSQL_USER)
    db_pass=$(docker exec "$db_container" printenv MYSQL_PASSWORD)
    docker exec "$db_container" mysqldump -u"$db_user" -p"$db_pass" "$db_name" > "$output_path"
}

prompt_save() {
    local reply=""
    echo
    if [ "$MODE" = "zip" ]; then
        echo "Saving copies the running WordPress files (themes, plugins, and uploads)"
        echo "and a fresh database dump back to your original LocalWP export."
        echo "This will replace the source zip: $SOURCE_ZIP"
        echo "Any edits in that zip will be overwritten. Back them up first if needed."
        echo "Choosing No leaves the zip unchanged; changes remain in Docker volumes."
    else
        echo "Your WordPress files are already live on disk in: $SOURCE_DIR/app/public/"
        echo "Editor changes and changes made by WordPress are saved there immediately."
        echo "Saving writes a fresh database dump to: $SOURCE_DIR/app/sql/local.sql"
        echo "The previous SQL dump will be overwritten; your live files will not be replaced."
        echo "Choosing No skips the database dump. It does NOT undo any filesystem changes."
        echo "Database changes remain in Docker volumes."
    fi
    echo "Deleting Docker volumes (for example, with down -v) discards their data."
    printf 'Save changes to this source before shutting down? [y/N] '
    read -r reply || true
    case "${reply,,}" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

usage() {
    cat <<'HELP'
Usage: localwp-docker-compose [--site PATH] COMMAND [ARGS...]

Run a LocalWP site with Docker Compose. PATH is an expanded export directory
or a LocalWP zip file; it defaults to the current directory.

Compose commands:
  up [OPTIONS] [SERVICE...]    Start, follow logs, prompt to save, then shut down
                              With -d / --detach: run in background without a prompt
  down [OPTIONS]              Stop and remove containers (keep volumes by default)
  start / stop / restart      Manage existing containers
  logs [OPTIONS] [SERVICE...] View logs; -f follows output
  ps [OPTIONS]               Show container status
  build / pull               Build or pull service images
  exec / run                 Run a command in a service
  config                     Show the resolved Compose configuration
  Other Compose commands are also forwarded to docker compose.

LocalWP commands:
  session [UP OPTIONS]        Explicit interactive start/logs/save/shutdown workflow
  save                        Directory: dump database; zip: replace files + database
  export [OUTPUT.zip]         Export live data (default: ./site-export.zip)
  help                        Show this help; help COMMAND shows Compose help

Options:
  --site PATH                 Select the site (before COMMAND)
  -h, --help                  Show this help without requiring Docker

Examples:
  localwp-docker-compose up
  localwp-docker-compose up -d --build
  localwp-docker-compose logs -f wordpress
  localwp-docker-compose --site /path/to/site.zip up -d
  localwp-docker-compose --site /path/to/site.zip save
  localwp-docker-compose down

Use COMMAND --help for Compose command options. Compose global options must
be configured through environment variables (for example COMPOSE_PROFILES).
Set LOCALWP_PROJECT_NAME to override the automatically generated project name.
Foreground up keeps containers running until you answer the save prompt after
Ctrl+C. Directory sites use live app/public bind mounts: editor and WordPress
file changes are immediate. Save only updates app/sql/local.sql; No skips that
database dump and does not undo file edits. Zip sites use isolated Docker files;
save replaces the source zip with files + database, and No leaves it unchanged.
Extract a zip to a directory first if you want live editor development.
Both choices then shut down containers, preserving Docker data volumes.
Detached up (-d / --detach, or --wait) does not prompt or shut down automatically.
Foreground up uses detached startup plus logs; attached-only Compose flags such
as --abort-on-container-exit and --exit-code-from are not supported.
HELP
}

TARGET_INPUT="$PWD"
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --site)
            [ "$#" -ge 2 ] && [ -n "$2" ] || die "--site requires a path."
            TARGET_INPUT="$2"
            shift 2
            ;;
        --site=*)
            TARGET_INPUT="${1#--site=}"
            [ -n "$TARGET_INPUT" ] || die "--site requires a path."
            shift
            ;;
        -*) die "Unknown option: $1. Use --help for usage." ;;
        *) break ;;
    esac
done

if [ "$#" -eq 0 ]; then
    usage
    exit 0
fi
COMMAND="$1"
shift
case "$COMMAND" in
    help)
        if [ "$#" -eq 0 ]; then usage; exit 0; fi
        require_cmd docker
        exec docker compose help "$@"
        ;;
    save) [ "$#" -eq 0 ] || die "Usage: localwp-docker-compose [--site PATH] save" ;;
    export) [ "$#" -le 1 ] || die "Usage: localwp-docker-compose [--site PATH] export [OUTPUT.zip]" ;;
    session) ;;
    *)
        # Compose help and version do not need a valid site or import tools.
        if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
            require_cmd docker
            exec docker compose "$COMMAND" "$@"
        fi
        if [ "$COMMAND" = "version" ]; then
            require_cmd docker
            exec docker compose version "$@"
        fi
        ;;
esac

require_cmd docker
require_cmd python3

# npm installs the command as a symlink outside the package directory.
SCRIPT_DIR=$(python3 - "${BASH_SOURCE[0]}" <<'PY'
import os, sys
print(os.path.dirname(os.path.realpath(sys.argv[1])))
PY
)

TARGET_INPUT=$(resolve_abs_path "$TARGET_INPUT")

MODE=""
SITE_ROOT=""
SOURCE_ZIP=""
SOURCE_DIR=""

TEMP_SITE_DIR=""
TEMP_EXPORT_DIR=""
cleanup() {
    if [ -n "$TEMP_SITE_DIR" ]; then rm -rf "$TEMP_SITE_DIR"; fi
    if [ -n "$TEMP_EXPORT_DIR" ]; then rm -rf "$TEMP_EXPORT_DIR"; fi
}
trap cleanup EXIT

if [ -f "$TARGET_INPUT" ] && [[ "$TARGET_INPUT" == *.zip ]]; then
    MODE="zip"
    SOURCE_ZIP="$TARGET_INPUT"
    require_cmd unzip
    TEMP_SITE_DIR=$(mktemp -d)
    unzip -q "$SOURCE_ZIP" -d "$TEMP_SITE_DIR"
    SITE_ROOT=$(find_site_root "$TEMP_SITE_DIR") || die "Could not locate app/public inside zip: $SOURCE_ZIP"
elif [ -d "$TARGET_INPUT" ]; then
    MODE="dir"
    SOURCE_DIR="$TARGET_INPUT"
    SITE_ROOT=$(find_site_root "$SOURCE_DIR") || die "Could not locate app/public under directory: $SOURCE_DIR"
    SOURCE_DIR="$SITE_ROOT"
else
    die "Target must be a LocalWP zip file or a directory containing app/public."
fi

# Zip extraction paths are temporary; identify the project by its source instead.
PROJECT_SOURCE="${SOURCE_ZIP:-$SOURCE_DIR}"
safe_name=$(basename "$PROJECT_SOURCE" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')
path_hash=$(printf '%s' "$PROJECT_SOURCE" | sha1sum | cut -c1-8)
PROJECT_NAME="${LOCALWP_PROJECT_NAME:-localwp-${safe_name}-${path_hash}}"

# Bind mounts must survive detached up and be reusable by later commands.
RUNTIME_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/localwp-docker-compose/$path_hash"
IMPORT_DIR="$RUNTIME_DIR/import"
IMPORT_ZIP="$IMPORT_DIR/site.zip"
OVERRIDE_FILE="$RUNTIME_DIR/docker-compose.run.yml"

mkdir -p "$IMPORT_DIR"
if [ "$COMMAND" = "up" ] || [ "$COMMAND" = "session" ] || [ "$COMMAND" = "run" ]; then
    require_cmd zip
    # Build a fresh archive so removed source files do not linger in the zip.
    rm -f "$RUNTIME_DIR/site.next.zip"
    build_import_zip "$SITE_ROOT" "$RUNTIME_DIR/site.next.zip"
    mv "$RUNTIME_DIR/site.next.zip" "$IMPORT_ZIP"
fi

# JSON is valid YAML and safely quotes host paths (including spaces and colons).
python3 - "$MODE" "$SITE_ROOT" "$RUNTIME_DIR" "$OVERRIDE_FILE" <<'PY'
import json, os, sys
from pathlib import Path

mode, site, runtime, output = sys.argv[1:]
def bind(source, target, read_only=False):
    return {"type": "bind", "source": str(source).replace("$", "$$"),
            "target": target, "read_only": read_only,
            "bind": {"create_host_path": False}}

services = {}
for name in ("wordpress", "wordpress-fpm"):
    services[name] = {"volumes": [bind(Path(runtime) / "import", "/import", True)]}
if mode == "dir":
    public = Path(site) / "app/public"
    ini = Path(runtime) / "localwp-development.ini"
    ini.write_text("opcache.validate_timestamps=1\nopcache.revalidate_freq=0\n")
    for service in services.values():
        service["environment"] = {"LOCALWP_LIVE_FILES": "1",
                                  "LOCALWP_HOST_UID": str(os.getuid()),
                                  "LOCALWP_HOST_GID": str(os.getgid())}
        service["volumes"] += [
            bind(public, "/var/www/html"),
            {"type": "volume", "source": "wp_data", "target": "/localwp-state",
             "volume": {"nocopy": True}},
            bind(ini, "/usr/local/etc/php/conf.d/zzz-localwp-development.ini", True),
        ]
    services["nginx"] = {"volumes": [bind(public, "/var/www/html", True)]}
Path(output).write_text(json.dumps({"services": services}, indent=2) + "\n")
PY

# Compose only auto-merges docker-compose.override.yml when it discovers the
# files itself; explicit -f flags disable that, so add the site's override here.
COMPOSE_FILES=("$SCRIPT_DIR/docker-compose.yml" "$OVERRIDE_FILE")
SITE_OVERRIDE="$SITE_ROOT/docker-compose.override.yml"
if [ -f "$SITE_OVERRIDE" ]; then
    log "Using site override: $SITE_OVERRIDE"
    if [ "$MODE" = "zip" ]; then
        cp "$SITE_OVERRIDE" "$RUNTIME_DIR/docker-compose.override.yml"
        SITE_OVERRIDE="$RUNTIME_DIR/docker-compose.override.yml"
    fi
    COMPOSE_FILES+=("$SITE_OVERRIDE")
fi

compose_cmd=(docker compose --project-name "$PROJECT_NAME")
for compose_file in "${COMPOSE_FILES[@]}"; do
    compose_cmd+=(-f "$compose_file")
done

save_source() {
    TEMP_EXPORT_DIR=$(mktemp -d)
    if [ "$MODE" = "zip" ]; then
        require_cmd zip
        local tmp_export_zip="$TEMP_EXPORT_DIR/export.zip"
        log "Exporting current site state …"
        export_site_state "$PROJECT_NAME" "$tmp_export_zip" "${COMPOSE_FILES[@]}"
        cp "$tmp_export_zip" "$SOURCE_ZIP"
        log "Updated zip written to $SOURCE_ZIP"
    else
        # Never replace the live bind directory: doing so could lose concurrent
        # editor changes and leave running containers mounted on a deleted inode.
        log "Files are already live on disk. Exporting the database …"
        dump_database "$TEMP_EXPORT_DIR/local.sql"
        mkdir -p "$SOURCE_DIR/app/sql"
        cp "$TEMP_EXPORT_DIR/local.sql" "$SOURCE_DIR/app/sql/local.sql"
        log "Updated database dump written to $SOURCE_DIR/app/sql/local.sql"
    fi
}

log "Using compose project: $PROJECT_NAME"
if [ "$MODE" = "zip" ]; then
    log "Source zip: $SOURCE_ZIP"
else
    log "Source directory: $SOURCE_DIR"
    log "Live files: $SOURCE_DIR/app/public (editor and WordPress changes are shared immediately)."
fi

case "$COMMAND" in
    save) save_source; exit 0 ;;
    export)
        require_cmd zip
        output_path=$(resolve_abs_path "${1:-site-export.zip}")
        TEMP_EXPORT_DIR=$(mktemp -d)
        export_site_state "$PROJECT_NAME" "$TEMP_EXPORT_DIR/export.zip" "${COMPOSE_FILES[@]}"
        cp "$TEMP_EXPORT_DIR/export.zip" "$output_path"
        log "Export saved to $output_path"
        exit 0
        ;;
    up)
        detached=false
        background_only=false
        foreground_args=()
        for arg in "$@"; do
            case "$arg" in
                -d|--detach|-d=true|--detach=true) detached=true ;;
                -d=false|--detach=false) detached=false ;;
                --wait|--wait=true|--no-start|--no-start=true) background_only=true ;;
            esac
            case "$arg" in
                -d|--detach|-d=true|--detach=true|-d=false|--detach=false) ;;
                *) foreground_args+=("$arg") ;;
            esac
        done
        if [ "$detached" = true ] || [ "$background_only" = true ]; then
            "${compose_cmd[@]}" up "$@"
            exit $?
        fi
        set -- "${foreground_args[@]}"
        ;;
    session) ;;
    *)
        "${compose_cmd[@]}" "$COMMAND" "$@"
        exit $?
        ;;
esac

"${compose_cmd[@]}" up -d "$@"
log "Containers are running. Streaming logs (Ctrl+C to choose whether to save, then shut down)."

# Ctrl+C reaches both Compose and this shell. Keep the shell alive so export
# can run while the detached containers are still available.
trap ':' INT
set +e
"${compose_cmd[@]}" logs -f
log_exit_code=$?
set -e
trap - INT

saved=false
if prompt_save; then
    save_source
    saved=true
else
    if [ "$MODE" = "zip" ]; then
        log "Source zip unchanged. Changes remain in Docker volumes."
    else
        log "Database dump skipped. Live file changes remain on disk; database changes remain in Docker volumes."
    fi
fi

log "Stopping containers …"
"${compose_cmd[@]}" down >/dev/null

if [ "$saved" = true ]; then
    log "Done."
fi

if [ "$log_exit_code" -ne 0 ] && [ "$log_exit_code" -ne 130 ]; then
    exit "$log_exit_code"
fi
