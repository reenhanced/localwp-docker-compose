#!/usr/bin/env bash
# entrypoint.sh – wrapper around the official WordPress Docker entrypoint.
#
# This script:
#   1. Runs the LocalWP import/init logic (init.sh)
#   2. Starts the web server via the upstream docker-entrypoint.sh
#   3. After the server is running, generates the admin login URL

set -euo pipefail

# Run the init (import zip, configure DB, rewrite URLs)
/usr/local/bin/localwp-init

# Hand off to the official WordPress entrypoint, which sets up wp-config.php
# from environment variables and then starts Apache / php-fpm.
# We preserve all arguments so the default CMD still works.
exec docker-entrypoint.sh "$@"
