#!/usr/bin/env bash
# init.sh – LocalWP Docker Compose initialisation script.
#
# Runs inside the wordpress container (as root) BEFORE the web server starts.
# On subsequent container restarts the heavy lifting is skipped via a lock file.

set -euo pipefail

WP_DIR=/var/www/html
IMPORT_DIR=/import
LOCK_FILE="${WP_DIR}/.localwp-docker-init-done"

LOCAL_URL="${LOCAL_URL:-http://localhost:8080}"
# Strip trailing slash
LOCAL_URL="${LOCAL_URL%/}"

log()  { echo "[localwp-init] $*"; }
warn() { echo "[localwp-init] WARNING: $*" >&2; }

# ── Wait for MySQL ─────────────────────────────────────────────────────────────
wait_for_mysql() {
    log "Waiting for MySQL …"
    local retries=60
    until mysqladmin ping -h db \
          -u"${WORDPRESS_DB_USER}" \
          -p"${WORDPRESS_DB_PASSWORD}" \
          --silent 2>/dev/null; do
        retries=$((retries - 1))
        [ "$retries" -le 0 ] && {
            warn "MySQL did not become ready in time. Aborting."
            exit 1
        }
        sleep 2
    done
    log "MySQL is ready."
}

# ── Install the auto-login mu-plugin ──────────────────────────────────────────
install_autologin_plugin() {
    local mu_dir="${WP_DIR}/wp-content/mu-plugins"
    mkdir -p "$mu_dir"
    cat > "${mu_dir}/localwp-autologin.php" <<'PHPEOF'
<?php
/**
 * LocalWP Docker – one-click admin login (development only).
 * Handles temporary single-use login tokens stored as WordPress transients.
 */
add_action( 'init', function () {
    $raw_key = isset( $_GET['localwp_autologin'] ) ? $_GET['localwp_autologin'] : '';
    if ( '' === $raw_key ) {
        return;
    }
    $key     = sanitize_key( $raw_key );
    $user_id = get_transient( 'localwp_autologin_' . $key );
    if ( false !== $user_id ) {
        delete_transient( 'localwp_autologin_' . $key );
        wp_set_auth_cookie( (int) $user_id, false );
        wp_safe_redirect( admin_url() );
        exit;
    }
} );
PHPEOF
}

# ── Generate a one-click admin login URL ──────────────────────────────────────
generate_admin_url() {
    local admin_user
    admin_user=$(wp --path="$WP_DIR" --allow-root \
        user list --role=administrator --field=ID --format=csv 2>/dev/null \
        | head -n1 || true)

    if [ -z "$admin_user" ]; then
        echo "${LOCAL_URL}/wp-admin/"
        return
    fi

    local token
    token=$(wp --path="$WP_DIR" --allow-root eval \
        "echo wp_generate_password(32,false);" 2>/dev/null || true)

    if [ -z "$token" ]; then
        echo "${LOCAL_URL}/wp-admin/"
        return
    fi

    # Store the token as a transient (expires in 1 hour)
    wp --path="$WP_DIR" --allow-root eval \
        "set_transient('localwp_autologin_${token}', ${admin_user}, HOUR_IN_SECONDS);" \
        2>/dev/null || true

    echo "${LOCAL_URL}/?localwp_autologin=${token}"
}

# ── Print the welcome banner ───────────────────────────────────────────────────
print_banner() {
    local admin_url="$1"
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║         LocalWP Docker Compose – Site Information               ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    printf  "║  Site URL       : %-47s ║\n" "${LOCAL_URL}/"
    printf  "║  WP-Admin       : %-47s ║\n" "${LOCAL_URL}/wp-admin/"
    printf  "║  One-click login: %-47s ║\n" "${admin_url}"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  phpMyAdmin     : http://localhost:8081/                        ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  Run './export.sh' to create an updated LocalWP-compatible zip  ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if [ -f "$LOCK_FILE" ]; then
    log "Site already initialised – skipping import."
    install_autologin_plugin
    wait_for_mysql
    ADMIN_URL=$(generate_admin_url)
    print_banner "$ADMIN_URL"
    exit 0
fi

# ── 1. Locate the zip ─────────────────────────────────────────────────────────
ZIP_FILE=$(find "$IMPORT_DIR" -maxdepth 1 -name "*.zip" 2>/dev/null | head -n1 || true)

if [ -z "$ZIP_FILE" ]; then
    log "No zip file found in ${IMPORT_DIR}/."
    log "Place a LocalWP-compatible zip file in the 'import/' directory and restart."
    log "The site will start with a default WordPress installation."
    wait_for_mysql
    print_banner "${LOCAL_URL}/wp-admin/"
    exit 0
fi

log "Found zip: ${ZIP_FILE}"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# ── 2. Extract WordPress files ────────────────────────────────────────────────
log "Extracting ${ZIP_FILE} …"
unzip -q "$ZIP_FILE" -d "$TMPDIR"

# LocalWP puts WordPress files in  app/public/
# Some exporters may use  public/  or just  /
WP_SOURCE=""
for candidate in \
    "${TMPDIR}/app/public" \
    "${TMPDIR}/public" \
    "${TMPDIR}"; do
    if [ -f "${candidate}/wp-login.php" ] || [ -f "${candidate}/index.php" ]; then
        WP_SOURCE="$candidate"
        break
    fi
done

if [ -z "$WP_SOURCE" ]; then
    warn "Could not locate WordPress files inside the zip (expected app/public/index.php)."
else
    log "Copying WordPress files from ${WP_SOURCE} …"
    # Use rsync-style copy without overwriting existing files in the volume
    cp -rn "${WP_SOURCE}/." "${WP_DIR}/"
    log "WordPress files copied."
fi

# ── 3. Import SQL dump ────────────────────────────────────────────────────────
SQL_FILE=""

# Try canonical LocalWP location first, then scan the archive
for candidate in \
    "${TMPDIR}/app/sql/local.sql" \
    $(find "$TMPDIR" -maxdepth 3 -name "*.sql" 2>/dev/null | head -n3); do
    if [ -f "$candidate" ]; then
        SQL_FILE="$candidate"
        break
    fi
done

wait_for_mysql

if [ -n "$SQL_FILE" ]; then
    log "Importing SQL dump: ${SQL_FILE} …"
    mysql -h db \
          -u"${WORDPRESS_DB_USER}" \
          -p"${WORDPRESS_DB_PASSWORD}" \
          "${WORDPRESS_DB_NAME}" < "$SQL_FILE"
    log "SQL import complete."
else
    warn "No SQL dump found in the zip – the database will be empty."
fi

# ── 4. Patch wp-config.php ────────────────────────────────────────────────────
if [ ! -f "${WP_DIR}/wp-config.php" ] && [ -f "${WP_DIR}/wp-config-sample.php" ]; then
    log "Generating wp-config.php from sample …"
    cp "${WP_DIR}/wp-config-sample.php" "${WP_DIR}/wp-config.php"
fi

if [ -f "${WP_DIR}/wp-config.php" ]; then
    log "Patching database credentials in wp-config.php …"
    sed -i "s|define[[:space:]]*([[:space:]]*'DB_NAME'[^)]*)|define('DB_NAME', '${WORDPRESS_DB_NAME}')|" \
        "${WP_DIR}/wp-config.php"
    sed -i "s|define[[:space:]]*([[:space:]]*'DB_USER'[^)]*)|define('DB_USER', '${WORDPRESS_DB_USER}')|" \
        "${WP_DIR}/wp-config.php"
    sed -i "s|define[[:space:]]*([[:space:]]*'DB_PASSWORD'[^)]*)|define('DB_PASSWORD', '${WORDPRESS_DB_PASSWORD}')|" \
        "${WP_DIR}/wp-config.php"
    sed -i "s|define[[:space:]]*([[:space:]]*'DB_HOST'[^)]*)|define('DB_HOST', 'db')|" \
        "${WP_DIR}/wp-config.php"
fi

# ── 5. Rewrite all URLs (handles serialized data and non-standard table prefix) ─
OLD_URL=""
if command -v wp >/dev/null 2>&1; then
    OLD_URL=$(wp --path="$WP_DIR" --allow-root option get siteurl 2>/dev/null || true)
fi

if [ -n "$OLD_URL" ] && [ "$OLD_URL" != "$LOCAL_URL" ]; then
    log "Rewriting URLs: ${OLD_URL} → ${LOCAL_URL} …"
    wp --path="$WP_DIR" --allow-root search-replace \
        "$OLD_URL" "$LOCAL_URL" \
        --all-tables \
        --skip-columns=guid \
        2>/dev/null \
    || warn "wp search-replace failed; falling back to direct option update."
    # Ensure siteurl and home are set even if search-replace had issues
    wp --path="$WP_DIR" --allow-root option update siteurl "${LOCAL_URL}" 2>/dev/null || true
    wp --path="$WP_DIR" --allow-root option update home   "${LOCAL_URL}" 2>/dev/null || true
elif [ -z "$OLD_URL" ]; then
    # WP-CLI not available or WordPress not installed – fall back to raw SQL
    # Read table_prefix from wp-config.php (defaults to wp_)
    TABLE_PREFIX=$(grep -oP "(?<=table_prefix\s=\s')[^']+" "${WP_DIR}/wp-config.php" 2>/dev/null || echo "wp_")
    log "Updating siteurl and home in ${TABLE_PREFIX}options …"
    mysql -h db \
          -u"${WORDPRESS_DB_USER}" \
          -p"${WORDPRESS_DB_PASSWORD}" \
          "${WORDPRESS_DB_NAME}" \
          -e "UPDATE \`${TABLE_PREFIX}options\`
              SET option_value='${LOCAL_URL}'
              WHERE option_name IN ('siteurl','home');" 2>/dev/null \
        || warn "Could not update siteurl/home."
else
    log "Site URL already set to ${LOCAL_URL} – no rewrite needed."
fi

# ── 6. Install mu-plugin & generate admin URL ─────────────────────────────────
install_autologin_plugin
ADMIN_URL=$(generate_admin_url)

touch "$LOCK_FILE"
log "Initialisation complete."

print_banner "$ADMIN_URL"
