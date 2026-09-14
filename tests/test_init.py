"""Isolated shell tests; no Docker, database, root, or host ownership changes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("bash"), "bash is required")
class InitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.wp = self.base / "public"
        self.state = self.base / "state"
        self.imports = self.base / "import"
        self.bin = self.base / "bin"
        for directory in (self.wp, self.state, self.imports, self.bin):
            directory.mkdir()
        self.calls = self.base / "calls"
        self.calls.touch()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CALLS=str(self.calls), LOCALWP_LIVE_FILES="1",
                        LOCALWP_HOST_UID="1000", LOCALWP_HOST_GID="1001",
                        WORDPRESS_DB_USER="wordpress", WORDPRESS_DB_PASSWORD="secret",
                        WORDPRESS_DB_NAME="wordpress")
        for name in ("chown", "chmod", "mysqladmin", "groupmod", "usermod"):
            self.mock(name, 'printf "%s\\n" "' + name + ' $*" >> "$CALLS"')
        self.mock("mysql", '''echo "mysql $*" >> "$CALLS"
case " $* " in
    *" --skip-ssl-verify-server-cert "*) ;;
    *) echo "TLS/SSL error: self-signed certificate in certificate chain" >&2; exit 1 ;;
esac
case "$*" in
    *"SELECT 1"*)
        if [ "${MYSQL_WAIT_FAIL:-0}" = 1 ]; then
            echo "Access denied for user wordpress" >&2
            exit 1
        fi
        if [ "${MYSQL_WAIT_ONCE:-0}" = 1 ] && [ ! -f "$CALLS.ready" ]; then
            touch "$CALLS.ready"
            echo "Cannot connect to MySQL server" >&2
            exit 1
        fi
        exit 0 ;;
    *"UPDATE "*) ;;
    *) cat >> "$CALLS" ;;
esac
exit "${MYSQL_FAIL:-0}"
''')
        self.mock("sleep", ':')
        self.mock("wp", 'echo "wp $*" >> "$CALLS"; case "$*" in *"option get siteurl"*) echo http://localhost:8080;; esac')
        self.mock("id", 'echo 0')
        self.mock("getent", 'echo "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin"')
        self.mock("localwp-init", 'echo init >> "$CALLS"')
        self.mock("docker-entrypoint.sh", 'echo upstream >> "$CALLS"')
        self.mock("apache2-foreground", 'echo "apache $APACHE_RUN_USER $APACHE_RUN_GROUP" >> "$CALLS"')
        self.mock("php-fpm", 'echo fpm >> "$CALLS"')
        self.config = "<?php\n" + "\n".join(
            "define('%s', 'old');" % key for key in
            ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")) + "\n"
        (self.wp / "wp-config-sample.php").write_text(self.config)
        (self.wp / "index.php").write_text("host version")
        with zipfile.ZipFile(self.imports / "site.zip", "w") as archive:
            archive.writestr("app/public/index.php", "snapshot version")
            archive.writestr("app/public/deleted.php", "do not resurrect")
            archive.writestr("app/public/wp-config-sample.php", self.config)
            archive.writestr("app/sql/local.sql", "IMPORT_SENTINEL;\n")
        script = (ROOT / "init.sh").read_text().replace(
            "WP_DIR=/var/www/html", 'WP_DIR="%s"' % self.wp).replace(
            "IMPORT_DIR=/import", 'IMPORT_DIR="%s"' % self.imports).replace(
            "/localwp-state/", str(self.state) + "/")
        self.script = self.base / "init.sh"
        self.script.write_text(script)

    def mock(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\n" + body + "\n")
        path.chmod(0o755)

    def run_init(self, success=True):
        result = subprocess.run(["bash", str(self.script)], env=self.env,
                                text=True, capture_output=True, timeout=10)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return self.calls.read_text()

    def test_live_import_restart_and_state_reset(self):
        calls = self.run_init()
        marker = self.state / ".localwp-docker-init-done"
        self.assertTrue(marker.exists())
        self.assertFalse((self.wp / marker.name).exists())
        self.assertEqual((self.wp / "index.php").read_text(), "host version")
        self.assertFalse((self.wp / "deleted.php").exists())
        self.assertEqual(calls.count("IMPORT_SENTINEL"), 1)
        self.assertIn("define('DB_HOST', 'db')", (self.wp / "wp-config.php").read_text())
        for path in (self.wp / "wp-config.php", self.wp / "wp-content",
                     self.wp / "wp-content/mu-plugins",
                     self.wp / "wp-content/mu-plugins/localwp-autologin.php"):
            self.assertIn("chown 1000:1001 " + str(path) + "\n", calls)
        self.assertNotIn("chown -R", calls)
        (self.wp / "index.php").unlink()
        self.assertEqual(self.run_init().count("IMPORT_SENTINEL"), 1)
        self.assertFalse((self.wp / "index.php").exists())
        marker.unlink()  # Models removing the named state volume with down -v.
        self.assertEqual(self.run_init().count("IMPORT_SENTINEL"), 2)
        self.assertFalse((self.wp / "index.php").exists())

    def test_banner_recommends_cli_export(self):
        result = subprocess.run(["bash", str(self.script)], env=self.env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("localwp-docker-compose export", result.stdout)
        self.assertNotIn("./export.sh", result.stdout)

    def test_legacy_volume_marker_skips_import_but_patches_live_config(self):
        (self.state / ".localwp-docker-init-done").touch()
        (self.wp / "wp-config.php").write_text(self.config)
        calls = self.run_init()
        self.assertNotIn("IMPORT_SENTINEL", calls)
        self.assertIn("define('DB_HOST', 'db')", (self.wp / "wp-config.php").read_text())

    def test_source_marker_does_not_skip_new_database_import(self):
        (self.wp / ".localwp-docker-init-done").touch()
        self.assertIn("IMPORT_SENTINEL", self.run_init())

    def test_readiness_and_import_share_local_tls_options(self):
        calls = self.run_init()
        queries = [line for line in calls.splitlines() if line.startswith("mysql ")]
        self.assertEqual(len(queries), 2)
        for query in queries:
            self.assertIn("--skip-ssl-verify-server-cert --connect-timeout=5", query)
            self.assertIn("-h db -uwordpress -psecret wordpress", query)
        self.assertIn("SELECT 1", queries[0])
        self.assertNotIn("mysqladmin", calls)

    def test_readiness_reports_errors_and_recovers(self):
        self.env["MYSQL_WAIT_ONCE"] = "1"
        result = subprocess.run(["bash", str(self.script)], env=self.env,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cannot connect to MySQL server", result.stderr)
        self.assertIn("MySQL is ready.", result.stdout)
        self.assertEqual(self.calls.read_text().count("SELECT 1"), 2)

    def test_readiness_auth_failure_is_bounded_and_reported(self):
        self.env["MYSQL_WAIT_FAIL"] = "1"
        result = subprocess.run(["bash", str(self.script)], env=self.env,
                                text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Last connection error: Access denied", result.stderr)
        self.assertEqual(self.calls.read_text().count("SELECT 1"), 60)
        self.assertNotIn("IMPORT_SENTINEL", self.calls.read_text())
        self.assertFalse((self.state / ".localwp-docker-init-done").exists())

    def test_failed_import_does_not_create_marker(self):
        self.env["MYSQL_FAIL"] = "1"
        self.run_init(success=False)
        self.assertFalse((self.state / ".localwp-docker-init-done").exists())

    def test_missing_zip_configures_live_files_without_marking_import_done(self):
        (self.imports / "site.zip").unlink()
        calls = self.run_init()
        self.assertNotIn("IMPORT_SENTINEL", calls)
        self.assertIn("define('DB_HOST', 'db')", (self.wp / "wp-config.php").read_text())
        self.assertTrue((self.wp / "wp-content/mu-plugins/localwp-autologin.php").exists())
        self.assertFalse((self.state / ".localwp-docker-init-done").exists())

    def test_existing_source_directories_are_not_chowned(self):
        (self.wp / "wp-content/mu-plugins").mkdir(parents=True)
        calls = self.run_init()
        self.assertNotIn("chown 1000:1001 " + str(self.wp / "wp-content") + "\n", calls)
        self.assertNotIn("chown 1000:1001 " + str(self.wp / "wp-content/mu-plugins") + "\n", calls)

    def test_zip_mode_retains_copy_and_source_marker(self):
        self.env.pop("LOCALWP_LIVE_FILES")
        calls = self.run_init()
        self.assertTrue((self.wp / "deleted.php").exists())
        self.assertTrue((self.wp / ".localwp-docker-init-done").exists())
        self.assertFalse((self.state / ".localwp-docker-init-done").exists())
        self.assertNotIn("chown", calls)
        self.assertEqual(self.run_init().count("IMPORT_SENTINEL"), 1)

    def run_entrypoint(self, server=None):
        script = self.base / "entrypoint.sh"
        script.write_text((ROOT / "entrypoint.sh").read_text().replace(
            "/usr/local/bin/localwp-init", str(self.bin / "localwp-init")))
        args = ["bash", str(script)] + ([server] if server else [])
        return subprocess.run(args, env=self.env,
                              text=True, capture_output=True, timeout=10)

    def test_live_workers_bypass_upstream_and_detach_home_before_uid_change(self):
        for server in ("apache2-foreground", "php-fpm"):
            with self.subTest(server=server):
                self.calls.write_text("")
                result = self.run_entrypoint(server)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls.read_text()
                self.assertNotIn("upstream", calls)
                self.assertIn("groupmod -o -g 1001 www-data", calls)
                self.assertLess(calls.index("usermod -d /nonexistent"),
                                calls.index("usermod -o -u 1000 -g 1001"))
                self.assertLess(calls.index("usermod -d /var/www"), calls.index("init\n"))
                self.assertIn("apache www-data www-data" if server.startswith("apache") else "fpm", calls)

    def test_entrypoint_selects_installed_server_without_inherited_cmd(self):
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertIn("apache www-data www-data", self.calls.read_text())
        (self.bin / "apache2-foreground").unlink()
        self.calls.write_text("")
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertIn("fpm", self.calls.read_text())

    def test_invalid_or_root_host_ids_rejected(self):
        for value in ("", "0", "000", "-1", "abc"):
            with self.subTest(value=value):
                self.env["LOCALWP_HOST_UID"] = value
                self.assertNotEqual(self.run_entrypoint("php-fpm").returncode, 0)
        self.assertEqual(self.calls.read_text(), "")

    def test_zip_entrypoint_preserves_upstream(self):
        self.env.pop("LOCALWP_LIVE_FILES")
        self.assertEqual(self.run_entrypoint("php-fpm").returncode, 0)
        self.assertEqual(self.calls.read_text(), "init\nupstream\n")


class DockerfileTests(unittest.TestCase):
    def test_base_image_uses_php_variant_tags(self):
        from string import Template

        dockerfile = (ROOT / "Dockerfile").read_text()
        image = next(line.split()[1] for line in dockerfile.splitlines()
                     if line.startswith("FROM "))
        for version in ("8.2", "8.3"):
            for server in ("apache", "fpm"):
                with self.subTest(version=version, server=server):
                    self.assertEqual(
                        Template(image).substitute(PHP_VERSION=version, WEB_SERVER=server),
                        "wordpress:php" + version + "-" + server,
                    )


if __name__ == "__main__":
    unittest.main()
