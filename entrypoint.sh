#!/usr/bin/env bash
# entrypoint.sh – wrapper around the official WordPress Docker entrypoint.
#
# Initializes the site and starts its server. Live mounts use host-matched
# worker IDs and bypass upstream file seeding; zip sites retain upstream setup.

set -euo pipefail

# Replacing the base image's ENTRYPOINT clears its inherited CMD. Select the
# installed server when Docker has not supplied an explicit command.
if [ "$#" -eq 0 ]; then
    if command -v apache2-foreground >/dev/null 2>&1; then
        set -- apache2-foreground
    else
        set -- php-fpm
    fi
fi

if [ "${LOCALWP_LIVE_FILES:-0}" = 1 ]; then
    if [ "$(id -u)" != 0 ]; then
        echo "[localwp-entrypoint] Live files require a root entrypoint." >&2
        exit 1
    fi
    for host_id in "${LOCALWP_HOST_UID:-}" "${LOCALWP_HOST_GID:-}"; do
        if [[ ! "$host_id" =~ ^[0-9]+$ ]] || [[ ! "$host_id" =~ [1-9] ]]; then
            echo "[localwp-entrypoint] Live files require nonzero numeric LOCALWP_HOST_UID/GID." >&2
            exit 1
        fi
    done

    # usermod can recursively chown a user's home when changing its UID.
    # Temporarily detach /var/www so the live source is never traversed.
    www_home=$(getent passwd www-data | cut -d: -f6)
    restore_www_home() { usermod -d "$www_home" www-data; }
    usermod -d /nonexistent www-data
    trap restore_www_home EXIT
    groupmod -o -g "$LOCALWP_HOST_GID" www-data
    usermod -o -u "$LOCALWP_HOST_UID" -g "$LOCALWP_HOST_GID" www-data
    restore_www_home
    trap - EXIT

    # Apache envvars honor these; FPM pools use the www-data account by name.
    export APACHE_RUN_USER=www-data APACHE_RUN_GROUP=www-data
fi

# Run the init (import zip, configure DB, rewrite URLs)
/usr/local/bin/localwp-init

if [ "${LOCALWP_LIVE_FILES:-0}" = 1 ]; then
    # The upstream entrypoint may seed missing core files and chown the tree.
    # Live source is authoritative; init above handles its configuration.
    exec "$@"
fi

# Hand off to the official WordPress entrypoint, which sets up wp-config.php
# from environment variables and then starts Apache / php-fpm.
# We preserve all arguments so the default CMD still works.
exec docker-entrypoint.sh "$@"
