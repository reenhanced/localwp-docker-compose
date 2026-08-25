# Place your LocalWP-compatible zip file here.
#
# The zip should follow the LocalWP export format:
#   app/
#     public/   ← WordPress files (wp-config.php, wp-content/, etc.)
#     sql/
#       local.sql  ← full MySQL database dump
#
# How to export a site from LocalWP:
#   1. Open LocalWP
#   2. Right-click the site → Export
#   3. Choose a destination and wait for the zip to be created
#   4. Copy the resulting .zip file into this directory
#
# Only one zip file should be present.  Once the site has been imported
# (i.e. after the first `docker compose up`), the zip file is no longer
# needed and can be removed.
