# localwp-docker-compose

Run [LocalWP](https://localwp.com/)-compatible zip files in Docker Compose — no LocalWP installation required.

## Features

- **One-command start**: `docker compose up` automatically imports your zip and starts WordPress.
- **One-click admin URL**: a temporary magic-link login token is printed in the startup logs.
- **Site URL & WP-Admin URL** printed on every startup.
- **Configurable PHP version** (7.4 – 8.3+).
- **Apache or nginx** — switch with a single setting.
- **Export** back to LocalWP-compatible zip with `./export.sh`.
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
/path/to/localwp-docker-compose/run.sh .
```

The command starts the stack, streams logs, and keeps your terminal attached.
When you exit logs (`Ctrl+C`), it asks whether to save changes back into that
directory (including a fresh `app/sql/local.sql` dump).

### 3. Run against a LocalWP zip file directly

```bash
./run.sh /absolute/path/to/my-site.zip
```

The zip is used as input for the run, and when the session ends you can choose
to write all changes back into the same zip file.

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

Edit `.env` to set your preferences:

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

### 6. Start the site

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

Supported tags mirror those on [Docker Hub for the `wordpress` image](https://hub.docker.com/_/wordpress/tags):
`7.4`, `8.0`, `8.1`, `8.2`, `8.3`, etc.

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

`docker-compose.override.yml` is automatically merged by Docker Compose and is
excluded from version control by `.gitignore`.

---

## Exporting the Site

Generate an updated LocalWP-compatible zip from the running site:

```bash
./export.sh
# or specify an output path:
./export.sh /tmp/my-site-backup.zip
```

The resulting zip can be imported back into LocalWP or any other tool that
understands the `app/public` + `app/sql/local.sql` format.

---

## Stopping and Removing

```bash
# Stop (preserve data volumes)
docker compose down

# Stop and delete all data (volumes, images)
docker compose down -v --rmi local
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No zip found on startup | Copy your `*.zip` into `import/` and run `docker compose up` |
| Site URL wrong after import | Update `LOCAL_URL` in `.env` and delete the lock file: `docker compose run --rm wordpress rm /var/www/html/.localwp-docker-init-done`, then restart |
| Plugin/theme updates fail | Ensure the `wp_data` volume has write access (runs as `www-data`) |
| One-click URL not working | The token expired (1 hour); restart the container to generate a new one |
| Rebuild not picking up PHP version change | Run `docker compose up --build` |
