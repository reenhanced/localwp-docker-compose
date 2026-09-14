# localwp-docker-compose

Run [LocalWP](https://localwp.com/)-compatible directories or zip files in Docker Compose — no LocalWP installation required. For local development only, not production.

## Install as a command (no `cd` required)

Install globally:

```bash
npm install -g git+https://github.com/reenhanced/localwp-docker-compose.git
```

Then run from any directory:

```bash
localwp-docker-compose --site /absolute/path/to/site.zip up
# or from an expanded LocalWP export root:
cd /path/to/site-folder
localwp-docker-compose up --build # initial setup or migration
# Later: localwp-docker-compose up (or up -d)
```

## Commands

Run `localwp-docker-compose` with no arguments (or `help`, `-h`, or `--help`)
for usage information. Help does not require Docker or a site.

```text
localwp-docker-compose [--site PATH] [--skip-setup] COMMAND [ARGS...]
```

`--site PATH` selects an expanded LocalWP export or a zip file and must precede
the command. The default is the current directory. Use the same site path for
subsequent commands so they address the same Compose project.

| Command | Behavior |
|---------|----------|
| `up [OPTIONS] [SERVICE...]` | Start and follow logs; on Ctrl+C, prompt to save before shutdown. Use `-d` / `--detach` for background operation without a prompt |
| `down [OPTIONS]` | Stop and remove containers; preserve data volumes unless `-v` is passed |
| `start`, `stop`, `restart` | Manage existing containers |
| `logs [OPTIONS] [SERVICE...]` | View logs; use `-f` to follow |
| `ps [OPTIONS]` | Show container status |
| `build`, `pull` | Build or pull images |
| `exec`, `run` | Execute commands in services |
| `config` | Show resolved Compose configuration |
| `setup` | Step through and save the selected site's configuration |
| `session [UP OPTIONS]` | Start detached, follow logs, prompt to save, then shut down |
| `save` | Directory: dump the running MySQL database to `app/sql/local.sql` only. Zip: replace the source archive with the running site's files and database |
| `export [OUTPUT.zip]` | Export the running site; defaults to `./site-export.zip` |
| `help [COMMAND]` | Show wrapper help or help for a Compose command |

### Setup on build

`build`, `up --build`, and `session --build` run an interactive setup before
Docker starts. The first `up` or `session` for a site also runs setup when its
`.env` does not exist. Use `localwp-docker-compose setup` at any time to change
settings. Directory sites save `.env` at the site root; zip sites save it beside
the zip. Site `.env` files are ignored by Git.

The wizard asks for every saved setting:

| Setting | Input |
|---------|-------|
| Web server | Arrow-key menu: Apache or nginx + PHP-FPM |
| PHP version | Arrow-key menu of supported PHP versions; an existing custom version remains selectable |
| Local URL, HTTP port, database name, database user | Editable text field prefilled from `.env` |
| Database password, root password | Hidden field; Enter preserves the saved value |

Existing `.env` values prefill the wizard. Press **Enter** to keep each value,
then confirm the final save. Saved values take precedence over built-in defaults;
shell variables still override them for Docker Compose and are identified by name
in the wizard. Changing database credentials does not modify an existing database
volume.

For CI or other noninteractive uses, add `--skip-setup` before the command or
after `build`, `up`, or `session`:

```bash
localwp-docker-compose up -d --build --skip-setup
localwp-docker-compose --skip-setup build
```

Skipping setup never creates or overwrites `.env`; the command uses saved values
when present and the normal defaults otherwise.

Other Compose commands and their arguments are forwarded to `docker compose`.
Use `COMMAND --help` to see Compose's options. Compose global CLI flags are not
accepted before the command; use environment variables such as `COMPOSE_PROFILES`
for configuration. `LOCALWP_PROJECT_NAME` overrides the generated project name.

```bash
localwp-docker-compose up -d --build
localwp-docker-compose logs -f
localwp-docker-compose exec wordpress php --version
localwp-docker-compose export /tmp/backup.zip
localwp-docker-compose save
localwp-docker-compose down
```

### What does “save” mean?

- **Directory input:** files are already live on disk in `app/public/`. Saving
  only dumps the running MySQL database to `app/sql/local.sql`; it never copies,
  deletes, or replaces `app/public/`.
- **Zip input:** runs as an isolated snapshot in Docker volumes. Saving replaces
  the original zip with the running site's files and a fresh database dump.
  Back up the original archive first; extract it and use directory mode for editor development.

Use `export [OUTPUT.zip]` to write a separate backup archive (an existing output
file will be overwritten).

Foreground `up` follows logs until you press Ctrl+C, then explains saving, shows
the destination, and asks for confirmation **while containers are still running**.
Answering Yes saves before shutdown. No (the default, also used on end-of-input)
only skips the database dump in directory mode: **it does not undo disk changes**.
In zip mode, No leaves the source archive unchanged. Both choices then run `down`,
preserving Docker data volumes; you can later start with `up -d` and run `save`.
The database persists until `down -v`, which resets database/init state (and
volume-backed zip files), but never deletes the bound directory's `app/public/`.

`up -d` / `up --detach` (and Compose's `up --wait`) run noninteractively and never
prompt or automatically shut down. Use `save` while the containers are running
and then `down` when finished. `up --no-start` also passes through without a prompt.

To allow saving before shutdown, foreground `up` uses detached startup followed
by log streaming, as `session` does. This differs from raw Compose attachment;
attached-only options such as `--abort-on-container-exit` and `--exit-code-from`
are not supported in this workflow. Use raw Docker Compose for those options.

Import archives are cached under
`${XDG_CACHE_HOME:-$HOME/.cache}/localwp-docker-compose/` so detached containers
retain their import mounts. This cache contains site files and database dumps;
remove it only when the associated containers have been removed. Docker data
volumes remain separate and are deleted only when requested (for example `down -v`).

**Migrating from the old CLI:** replace `localwp-docker-compose PATH` with
`localwp-docker-compose --site PATH session` to keep the interactive workflow,
or use `--site PATH up` for the same save-on-exit workflow (`up -d` for background operation). Directory-based project names are
unchanged; zip-based names now use the source path rather than a temporary
extraction path, so old zip sessions' volumes are not automatically reused.

## Features

- **One-command start**: `docker compose up` automatically imports your zip and starts WordPress.
- **One-click admin URL**: a temporary magic-link login token is printed in the startup logs.
- **Site URL & WP-Admin URL** printed on every startup.
- **Configurable PHP version** (7.4 – 8.3+).
- **Apache or nginx** — switch with a single setting.
- **Export** back to LocalWP-compatible zip with `localwp-docker-compose export`.
- **Override support** via `docker-compose.override.yml` (Traefik, custom labels, etc.).
- **phpMyAdmin** available out of the box at <http://localhost:8081>.

---

## Quick Start

### 1. Clone this repository

```bash
git clone https://github.com/reenhanced/localwp-docker-compose.git
cd localwp-docker-compose
```

### 2. Run against an expanded LocalWP site directory (recommended for git)

From the root of an expanded LocalWP export (directory containing `app/public`):

```bash
cd /path/to/site-folder
/path/to/localwp-docker-compose/run.sh up --build # initial setup or migration
# Subsequent starts:
/path/to/localwp-docker-compose/run.sh up # or up -d for background operation
# Edit app/public/wp-content/themes/... or app/public/wp-content/plugins/...
```

Directory mode bind-mounts `app/public/` at `/var/www/html`: read-write for Apache
and PHP-FPM, read-only for nginx. File creates, edits, and deletes are immediately
shared between your editor and the runtime, including WordPress uploads and plugin
changes. File edits need no rebuild or restart; usually refresh the browser.
Frontend builds and plugin/browser caches still apply; the generated OPcache ini
revalidates on every request.

Foreground `up` streams logs; Ctrl+C prompts to save **only the database** before
shutdown. `up -d` runs without a prompt; `session` provides the interactive workflow.

**Migrating an existing directory site:** file changes in the old `wp_data` volume
are not automatically migrated. **Before switching versions/mounts**, use the
previous version's `export /path/to/backup.zip` command to back up the running site,
then merge its files into your source as needed. The existing database and init
marker are reused, with `wp_data` mounted at `/localwp-state`. Run `up --build` to
rebuild images and recreate existing containers with the new initialization and
mounts; `restart` does not activate these changes.

Initialization patches `app/public/wp-config.php` and installs
`app/public/wp-content/mu-plugins/localwp-autologin.php` on your actual disk;
review your Git diff. On Linux, host IDs are applied to `www-data` to keep source
files writable. Run the CLI as your normal non-root account (the current entrypoint
requires nonzero UID/GID). The container entrypoint must start as root: do not
override `user`. Docker Desktop must share the source directory, and overrides
must not replace the generated `/var/www/html` mount.

### 3. Run against a LocalWP zip file directly

```bash
./run.sh --site /absolute/path/to/my-site.zip up -d
```

The zip is used as input. Save changes and stop the stack explicitly:

```bash
./run.sh --site /absolute/path/to/my-site.zip save
./run.sh --site /absolute/path/to/my-site.zip down
```

Use `up` (without `-d`) or `session` if you want a save prompt when log streaming ends.

### 4. Alternative legacy flow: add your LocalWP zip to `import/`

Export your site from LocalWP (**Right-click site → Export**), then copy the resulting
`.zip` into the `import/` directory:

```
import/
└── my-site.zip   ← place it here
```

The zip must follow the LocalWP export format:

```
my-site.zip
└── app/
    ├── public/      ← WordPress files
    └── sql/
        └── local.sql  ← database dump
```

### 5. Configure (optional)

The setup wizard creates this file during a build; rerun it with
`localwp-docker-compose setup` to change values. You can also edit `.env` directly:

| Variable           | Default                    | Description                                    |
|--------------------|----------------------------|------------------------------------------------|
| `COMPOSE_PROFILES` | `apache`                   | Web server: `apache` or `nginx`                |
| `PHP_VERSION`      | `8.2`                      | PHP version (e.g. `7.4`, `8.1`, `8.3`)        |
| `LOCAL_URL`        | `http://localhost:8080`    | URL where the site will be served              |
| `HTTP_PORT`        | `8080`                     | Host port for HTTP traffic                     |
| `MYSQL_DATABASE`   | `wordpress`                | WordPress database name                        |
| `MYSQL_USER`       | `wordpress`                | MySQL user                                     |
| `MYSQL_PASSWORD`   | `wordpress`                | MySQL password                                 |
| `MYSQL_ROOT_PASSWORD` | `rootpassword`          | MySQL root password                            |

### 6. Start the site (legacy raw Compose flow)

```bash
docker compose up
```

On the first run the following happens automatically:

1. MySQL starts and becomes healthy.
2. The WordPress files from `import/*.zip` are extracted into a persistent volume.
3. The SQL dump is imported into MySQL.
4. `wp-config.php` is patched with the container's database credentials.
5. `siteurl` and `home` in `wp_options` are updated to `LOCAL_URL`.
6. A one-click login URL is generated and printed to the console.

Subsequent `docker compose up` calls skip the import (idempotent lock file) and
just print the URLs before starting the web server.

---

## Startup Output

After a successful start you will see something like:

```
╔══════════════════════════════════════════════════════════════════╗
║         LocalWP Docker Compose – Site Information               ║
╠══════════════════════════════════════════════════════════════════╣
║  Site URL       : http://localhost:8080/                        ║
║  WP-Admin       : http://localhost:8080/wp-admin/               ║
║  One-click login: http://localhost:8080/?localwp_autologin=...  ║
╠══════════════════════════════════════════════════════════════════╣
║  phpMyAdmin     : http://localhost:8081/                        ║
╠══════════════════════════════════════════════════════════════════╣
║  Run './export.sh' to create an updated LocalWP-compatible zip  ║
╚══════════════════════════════════════════════════════════════════╝
```

Click the **One-click login** URL to access WP-Admin without entering credentials.
The token expires after one hour; restart the container to get a fresh URL.

---

## Selecting Apache or nginx

Edit `.env` and set:

```dotenv
# Apache (default)
COMPOSE_PROFILES=apache

# nginx + PHP-FPM
COMPOSE_PROFILES=nginx
```

Then rebuild and restart:

```bash
docker compose up --build
```

> **Note**: switching web servers for the first time requires `--build` so Docker
> creates the correct image. Subsequent starts with the same profile do not need `--build`.

---

## Selecting a PHP Version

Edit `.env`:

```dotenv
PHP_VERSION=8.3
```

Then rebuild:

```bash
docker compose up --build
```

Set `PHP_VERSION` to the numeric PHP version (for example, `8.2` or `8.3`).
The Dockerfile selects `wordpress:php8.2-apache` or `wordpress:php8.2-fpm`
for PHP 8.2, depending on the web server. Available versions depend on the
published [WordPress image tags](https://hub.docker.com/_/wordpress/tags).

---

## Using a Custom Local URL

Update `LOCAL_URL` in `.env` (and add the hostname to `/etc/hosts` if needed):

```dotenv
LOCAL_URL=http://mysite.test
```

If you are mapping a custom hostname, add it to your hosts file:

```
127.0.0.1  mysite.test
```

---

## Override File (Traefik, custom labels, etc.)

Copy the example and customise it:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
```

Place `docker-compose.override.yml` in the site directory (next to `app/`) and
the run script merges it automatically as the last compose file, so its values
win. When running `docker compose` by hand from this repository, Docker Compose
merges an override in this directory on its own. The file is excluded from
version control by `.gitignore`.

---

## Exporting the Site

Generate an updated LocalWP-compatible zip from the running site:

```bash
localwp-docker-compose export
# or specify a site and output path:
localwp-docker-compose --site /path/to/site.zip export /tmp/my-site-backup.zip
```

For the legacy flow started with `docker compose up` directly from this
repository, continue to use `./export.sh [OUTPUT.zip]` instead. That script
addresses the raw Compose project, not the CLI's site-specific project.

The resulting zip can be imported back into LocalWP or any other tool that
understands the `app/public` + `app/sql/local.sql` format.

---

## Stopping and Removing

```bash
# Stop (preserve data volumes)
localwp-docker-compose down

# Stop and delete volumes and local images (not bound app/public files)
localwp-docker-compose down -v --rmi local
```

---

For a zip or a site outside the current directory, pass `--site PATH` before
`down`. For the legacy repository flow, use `docker compose down` instead.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Stuck at “Waiting for MySQL” | Run `localwp-docker-compose up --build` to use the current client settings. Read the reported connection error; check database credentials if access is denied. Do not delete volumes to fix a connection error |
| No zip found on startup (legacy raw Compose) | Copy your `*.zip` into `import/` and run `docker compose up` |
| Site URL wrong after import (legacy raw Compose) | Update `LOCAL_URL` in `.env` and delete the lock file: `docker compose run --rm wordpress rm /var/www/html/.localwp-docker-init-done`, then restart |
| Plugin/theme updates fail | Directory mode: check source permissions, run the CLI as a non-root account, and ensure Docker Desktop shares the source directory. Legacy/zip mode: check `wp_data` write access for `www-data` |
| Directory edits not visible | Migrate with `up --build`, not `restart`; check the `/var/www/html` mount, frontend build output, and plugin/browser caches |
| One-click URL not working | The token expired (1 hour); restart the container to generate a new one |
| Rebuild not picking up PHP version change | Run `docker compose up --build` |

The initialization client accepts the bundled MySQL server's self-signed TLS
certificate on the local Compose network. TLS remains enabled, but certificate
verification is skipped; this setting is for local development, not production.
