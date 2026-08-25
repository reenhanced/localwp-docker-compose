ARG PHP_VERSION=8.2
ARG WEB_SERVER=apache

FROM wordpress:${PHP_VERSION}-${WEB_SERVER}

# Install MySQL client (needed by init.sh and export.sh inside the container),
# unzip, and WP-CLI.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        default-mysql-client \
        unzip \
        less \
 && rm -rf /var/lib/apt/lists/* \
 && curl -sL https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar \
        -o /usr/local/bin/wp \
 && chmod +x /usr/local/bin/wp

# Copy helper scripts into the image so they are always available
COPY init.sh          /usr/local/bin/localwp-init
COPY entrypoint.sh    /usr/local/bin/localwp-entrypoint

RUN chmod +x \
        /usr/local/bin/localwp-init \
        /usr/local/bin/localwp-entrypoint

ENTRYPOINT ["/usr/local/bin/localwp-entrypoint"]
