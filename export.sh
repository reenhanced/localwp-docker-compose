#!/usr/bin/env bash
# export.sh – create a LocalWP-compatible zip from the running site.
#
# Usage (from the project root on the HOST):
#   ./export.sh [output-file.zip]
#
# The zip has the structure expected by LocalWP's import feature:
#   app/
#     public/   ← WordPress files
#     sql/
#       local.sql  ← full database dump

set -euo pipefail

COMPOSE_PROJECT=${COMPOSE_PROJECT_NAME:-$(basename "$(pwd)")}
OUTPUT="${1:-site-export-$(date +%Y%m%d-%H%M%S).zip}"

log()  { echo "[export] $*"; }
die()  { echo "[export] ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is not installed."
command -v zip    >/dev/null 2>&1 || die "zip is not installed."

# ── Identify running containers ───────────────────────────────────────────────
WP_CONTAINER=$(docker compose ps -q wordpress 2>/dev/null | head -n1)
DB_CONTAINER=$(docker compose ps -q db        2>/dev/null | head -n1)

[ -n "$WP_CONTAINER" ] || die "The 'wordpress' service is not running. Start it with 'docker compose up -d'."
[ -n "$DB_CONTAINER" ] || die "The 'db' service is not running."

# ── Temporary staging directory ───────────────────────────────────────────────
TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/app/public" "$TMPDIR/app/sql"

# ── 1. Copy WordPress files ───────────────────────────────────────────────────
log "Copying WordPress files …"
docker cp "${WP_CONTAINER}:/var/www/html/." "$TMPDIR/app/public/"
# Remove compiled/cache artefacts that shouldn't be in the export
rm -rf \
    "$TMPDIR/app/public/wp-content/cache" \
    "$TMPDIR/app/public/.localwp-docker-init-done"

# ── 2. Dump the database ──────────────────────────────────────────────────────
log "Dumping database …"
# Read DB credentials from the running container's environment
DB_NAME=$(docker exec "$DB_CONTAINER" printenv MYSQL_DATABASE)
DB_USER=$(docker exec "$DB_CONTAINER" printenv MYSQL_USER)
DB_PASS=$(docker exec "$DB_CONTAINER" printenv MYSQL_PASSWORD)

docker exec "$DB_CONTAINER" \
    mysqldump -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" \
    > "$TMPDIR/app/sql/local.sql"

# ── 3. Zip everything ─────────────────────────────────────────────────────────
log "Creating zip: $OUTPUT …"
(cd "$TMPDIR" && zip -qr - app) > "$OUTPUT"

rm -rf "$TMPDIR"

log "Done!  Export saved to: $OUTPUT"
log "You can import this file into LocalWP using File → Import Site."
