"""CLI regression tests: real shell/archive tools, no Docker daemon."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
TOOLS = (
    "cat", "find", "head", "dirname", "mkdir", "zip", "unzip", "mktemp",
    "basename", "tr", "sha1sum", "cut", "rm", "mv", "cp",
)
MOCK_DOCKER = r'''import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open(os.environ["DOCKER_RECORD"], "a", encoding="utf-8") as record:
    record.write(json.dumps(args) + "\n")
command = args
if args[0] == "compose":
    command = args[1:]
    while command and command[0] in ("--project-name", "--env-file", "-f"):
        command = command[2:]
    if command[:2] == ["ps", "-q"]:
        print({"wordpress": "mock-wp", "db": "mock-db"}.get(command[2], ""))
elif args[0] == "cp":
    destination = Path(args[2])
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "index.php").write_text("live wordpress")
    (destination / ".localwp-docker-init-done").touch()
    cache = destination / "wp-content" / "cache"
    cache.mkdir(parents=True)
    (cache / "cached.txt").write_text("stale cache")
elif args[:3] == ["exec", "mock-db", "printenv"]:
    print({"MYSQL_DATABASE": "local", "MYSQL_USER": "user",
           "MYSQL_PASSWORD": "password"}[args[3]])
elif args[:3] == ["exec", "mock-db", "mysqldump"]:
    print("-- mock database dump")
elif args[:3] == ["exec", "mock-wp", "wp"]:
    print("https://site.test\nhttps://site.test/wp-admin/\nhttps://site.test/?localwp_autologin=token")
if command[0] == "logs" and os.environ.get("MOCK_INTERRUPT_LOGS"):
    import signal
    os.kill(os.getppid(), signal.SIGINT)
    sys.exit(130)
sys.exit(json.loads(os.environ.get("DOCKER_EXIT_CODES", "{}")).get(command[0], 0))
'''


@unittest.skipUnless(BASH, "bash is required to run run.sh")
class CLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="localwp cli tests ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        # Whitelist executables so the real Docker can never be reached.
        for tool in TOOLS:
            executable = shutil.which(tool)
            if executable:
                (self.bin / tool).symlink_to(executable)
        (self.bin / "python3").symlink_to(sys.executable)
        docker = self.bin / "docker"
        docker.write_text("#!" + sys.executable + "\n" + MOCK_DOCKER)
        docker.chmod(0o755)
        self.record = self.base / "docker.jsonl"
        self.site = self.base / "My Site"
        (self.site / "app" / "public").mkdir(parents=True)
        (self.site / "app" / "sql").mkdir()
        (self.site / "app" / "public" / "index.php").write_text("source wordpress")
        (self.site / "app" / "sql" / "local.sql").write_text("source sql")
        (self.site / ".env").write_text("COMPOSE_PROFILES=apache\nPHP_VERSION=8.2\n")
        self.cwd = self.base / "working directory"
        self.cwd.mkdir()
        self.cache = self.base / "cache"
        self.tmp = self.base / "tmp"
        self.tmp.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("LOCALWP_", "COMPOSE_", "DOCKER_"))
                    and key not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")}
        self.env.update(PATH=str(self.bin), HOME=str(self.base),
                        XDG_CACHE_HOME=str(self.cache), TMPDIR=str(self.tmp),
                        DOCKER_RECORD=str(self.record), LC_ALL="C")

    def require_tools(self, *tools):
        missing = [tool for tool in tools if not (self.bin / tool).exists()]
        if missing:
            self.skipTest("Missing tools: " + ", ".join(missing))

    def invoke(self, *args, cwd=None, stdin="", env=None):
        return subprocess.run(
            [BASH, str(ROOT / "run.sh"), *map(str, args)],
            cwd=cwd or self.cwd, env=dict(self.env, **(env or {})),
            input=stdin, text=True, capture_output=True, timeout=15,
        )

    def calls(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def clear_calls(self):
        self.record.unlink(missing_ok=True)

    def runtime(self, source):
        digest = hashlib.sha1(str(source).encode()).hexdigest()[:8]
        return self.cache / "localwp-docker-compose" / digest

    def compose(self, source, *args):
        digest = hashlib.sha1(str(source).encode()).hexdigest()[:8]
        # basename adds a newline before tr converts punctuation to hyphens.
        name = re.sub("[^a-z0-9]+", "-", source.name.lower() + "\n")
        runtime = self.runtime(source)
        env_file = source / ".env" if source.is_dir() else source.parent / ".env"
        command = ["compose", "--project-name", "localwp-" + name + "-" + digest,
                   "--env-file", str(runtime / "defaults.env")]
        if env_file.exists():
            command += ["--env-file", str(env_file)]
        return [*command, "-f", str(ROOT / "docker-compose.yml"),
                "-f", str(runtime / "docker-compose.run.yml"), *args]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def make_zip(self):
        self.require_tools("zip", "unzip")
        archive = self.base / "Export Site.zip"
        (archive.parent / ".env").write_text("COMPOSE_PROFILES=apache\nPHP_VERSION=8.2\n")
        subprocess.run([str(self.bin / "zip"), "-qr", str(archive), "app"],
                       cwd=self.site, check=True, capture_output=True, timeout=15)
        return archive

    def test_local_help_needs_no_docker_python_or_archive_tools(self):
        minimal = self.base / "help-bin"
        minimal.mkdir()
        self.require_tools("cat")
        (minimal / "cat").symlink_to(self.bin / "cat")
        for args in ((), ("help",), ("-h",), ("--help",),
                     ("--site", "missing", "--help")):
            with self.subTest(args=args):
                result = self.invoke(*args, env={"PATH": str(minimal)})
                self.assert_success(result)
                self.assertIn("Usage: localwp-docker-compose [--site PATH]", result.stdout)
                self.assertEqual(result.stderr, "")
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.cache.exists())

    def test_argument_errors_precede_dependency_checks(self):
        cases = [(("--site",), "--site requires a path"),
                 (("--site", ""), "--site requires a path"),
                 (("--site=",), "--site requires a path"),
                 (("--unknown",), "Unknown option"),
                 (("save", "extra"), "save"),
                 (("export", "one.zip", "two.zip"), "export [OUTPUT.zip]")]
        empty = self.base / "empty-bin"
        empty.mkdir()
        for args, message in cases:
            with self.subTest(args=args):
                result = self.invoke(*args, env={"PATH": str(empty)})
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertNotIn("required but not installed", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_compose_help_and_version_do_not_need_a_site(self):
        for args in (("help", "up"), ("up", "--help"),
                     ("exec", "-h"), ("version", "--short")):
            with self.subTest(args=args):
                self.clear_calls()
                self.assert_success(self.invoke(*args))
                self.assertEqual(self.calls(), [["compose", *args]])
        self.assertFalse(self.cache.exists())

    def test_invalid_site_is_rejected_without_calling_docker(self):
        for site in (self.cwd, self.base / "missing"):
            with self.subTest(site=site):
                result = self.invoke("--site", site, "ps")
                self.assertEqual(result.returncode, 1)
                self.assertIn("ERROR:", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_foreground_up_decline_or_eof_explains_save_and_preserves_source(self):
        self.require_tools("zip")
        for reply in ("n\n", "\n", ""):
            with self.subTest(reply=reply):
                self.clear_calls()
                result = self.invoke("--site", self.site, "up", stdin=reply)
                self.assert_success(result)
                self.assertEqual(self.calls(), [self.compose(self.site, "up", "-d"),
                                               self.compose(self.site, "logs", "-f"),
                                               self.compose(self.site, "down")])
                self.assertIn("fresh database dump", result.stdout)
                self.assertIn(str(self.site / "app") + "/", result.stdout)
                self.assertIn("app/sql/local.sql", result.stdout)
                self.assertIn("overwritten", result.stdout)
                self.assertIn("[y/N]", result.stdout)
                self.assertIn("database changes remain in Docker volumes", result.stdout)
                self.assertIn("does NOT undo any filesystem changes", result.stdout)
                self.assertEqual((self.site / "app/public/index.php").read_text(), "source wordpress")

    def test_foreground_up_saves_directory_or_zip_before_shutdown(self):
        self.require_tools("zip", "unzip")
        archive = self.make_zip()
        for source in (self.site, archive):
            with self.subTest(source=source):
                self.clear_calls()
                result = self.invoke("--site", source, "--skip-setup", "up", "--build", stdin="yes\n")
                self.assert_success(result)
                calls = self.calls()
                self.assertEqual(calls[:2], [self.compose(source, "up", "-d", "--build"),
                                            self.compose(source, "logs", "-f")])
                self.assertIn(["exec", "mock-db", "mysqldump", "-uuser", "-ppassword", "local"], calls[2:-1])
                self.assertEqual(calls[-1], self.compose(source, "down"))
                if source == archive:
                    self.assertIn("replace the source zip: " + str(archive), result.stdout)
                    self.assert_live_archive(archive)
                else:
                    self.assertEqual((self.site / "app/public/index.php").read_text(), "source wordpress")
                    self.assertEqual((self.site / "app/sql/local.sql").read_text(), "-- mock database dump\n")

    def test_foreground_up_failure_never_prompts_or_shuts_down(self):
        self.require_tools("zip")
        result = self.invoke("--site", self.site, "up", stdin="y\n",
                             env={"DOCKER_EXIT_CODES": '{"up": 17}'})
        self.assertEqual(result.returncode, 17)
        self.assertEqual(self.calls(), [self.compose(self.site, "up", "-d")])
        self.assertNotIn("[y/N]", result.stdout)

    def test_ctrl_c_during_foreground_logs_still_prompts(self):
        self.require_tools("zip")
        result = self.invoke("--site", self.site, "up", stdin="n\n",
                             env={"MOCK_INTERRUPT_LOGS": "1"})
        self.assert_success(result)
        self.assertIn("[y/N]", result.stdout)
        self.assertEqual(self.calls()[-1], self.compose(self.site, "down"))

    def test_skip_setup_is_not_forwarded_and_never_creates_site_env(self):
        self.require_tools("zip")
        (self.site / ".env").unlink()
        result = self.invoke("--site", self.site, "up", "-d", "--skip-setup")
        self.assert_success(result)
        self.assertFalse((self.site / ".env").exists())
        self.assertEqual(self.calls(), [self.compose(self.site, "up", "-d")])
        self.assertNotIn("Configuring site environment", result.stdout)

    def test_build_requires_setup_without_a_tty_unless_skipped(self):
        self.require_tools("zip")
        result = self.invoke("--site", self.site, "build")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Configuring site environment", result.stdout)
        self.assertIn("--skip-setup", result.stderr)
        self.assertEqual(self.calls(), [])
        self.clear_calls()
        result = self.invoke("--site", self.site, "build", "--skip-setup", "wordpress")
        self.assert_success(result)
        self.assertEqual(self.calls(), [self.compose(self.site, "build", "wordpress")])

    def test_detached_up_arguments_are_unchanged(self):
        self.require_tools("zip")
        for flag in ("-d", "--detach", "--detach=true", "--wait", "--no-start"):
            with self.subTest(flag=flag):
                self.clear_calls()
                args = ("up", flag, "--build", "--scale", "wordpress=2", "wordpress")
                result = self.invoke("--site=" + str(self.site), "--skip-setup", *args, stdin="y\n")
                self.assert_success(result)
                self.assertEqual(self.calls(), [self.compose(self.site, *args)])
                self.assertNotIn("[y/N]", result.stdout)

    def test_info_prints_fresh_one_click_admin_url(self):
        result = self.invoke("--site", self.site, "info")
        self.assert_success(result)
        calls = self.calls()
        self.assertEqual(calls[0], self.compose(self.site, "ps", "-q", "wordpress"))
        self.assertEqual(calls[1][:-1], ["exec", "mock-wp", "wp", "--path=/var/www/html",
                                         "--allow-root", "eval"])
        self.assertIn("localwp_autologin_", calls[1][-1])
        self.assertIn("Site URL: https://site.test", result.stdout)
        self.assertIn("WP-Admin: https://site.test/wp-admin/", result.stdout)
        self.assertIn("One-click admin: https://site.test/?localwp_autologin=token",
                      result.stdout)
        self.assertIn("phpMyAdmin: http://localhost:8081/", result.stdout)

    def test_wp_cli_runs_in_the_active_wordpress_container(self):
        result = self.invoke("--site", self.site, "wp-cli", "option", "get", "home")
        self.assert_success(result)
        self.assertEqual(self.calls(), [self.compose(self.site, "ps", "-q", "wordpress"),
                                        ["exec", "-i", "mock-wp", "wp", "--path=/var/www/html",
                                         "--allow-root", "option", "get", "home"]])

    def test_wp_cli_requires_a_command(self):
        result = self.invoke("--site", self.site, "wp-cli")
        self.assertEqual(result.returncode, 1)
        self.assertIn("wp-cli COMMAND [ARGS...]", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_passthrough_preserves_arguments_and_exit_status(self):
        cases = [("down", "--volumes", "--remove-orphans"),
                 ("logs", "-f", "--tail", "12", "wordpress"),
                 ("exec", "-T", "wordpress", "sh", "-c", 'printf "%s" "a b"',
                  "", "*.php", "semi;colon", "quote'word"),
                 ("restart", "wordpress"), ("ps", "--all"),
                 ("config", "--services"), ("custom-command", "a b")]
        for args in cases:
            with self.subTest(args=args):
                self.clear_calls()
                result = self.invoke("--site", self.site, *args,
                                     env={"DOCKER_EXIT_CODES": json.dumps({args[0]: 37})})
                self.assertEqual(result.returncode, 37, result.stderr)
                self.assertEqual(self.calls(), [self.compose(self.site, *args)])

    def test_default_site_is_cwd_and_relative_site_resolves_from_cwd(self):
        self.assert_success(self.invoke("ps", cwd=self.site))
        self.assertEqual(self.calls(), [self.compose(self.site, "ps")])
        self.clear_calls()
        relative = os.path.relpath(self.site, self.cwd)
        self.assert_success(self.invoke("--site", relative, "down"))
        self.assertEqual(self.calls(), [self.compose(self.site, "down")])

    def test_override_in_selected_directory_is_passed_to_compose(self):
        project = self.base / "Customer Service"
        site = project / "localwp-export"
        (site / "app" / "public").mkdir(parents=True)
        (site / "app" / "sql").mkdir()
        (project / "docker-compose.override.yml").write_text(
            "services:\n  wordpress:\n    labels:\n      traefik.enable: 'true'\n")

        result = self.invoke("config", cwd=project)
        self.assert_success(result)

        expected = self.compose(site, "config")
        expected.insert(-1, "-f")
        expected.insert(-1, str(project / "docker-compose.override.yml"))
        self.assertEqual(self.calls(), [expected])
        self.assertIn("Using site override: " + str(project / "docker-compose.override.yml"),
                      result.stdout)

    def test_zip_identity_is_stable_and_import_survives_commands(self):
        archive = self.make_zip()
        original = archive.read_bytes()
        self.assert_success(self.invoke("--site", archive, "up", "-d"))
        runtime = self.runtime(archive)
        imported = runtime / "import/site.zip"
        override = runtime / "docker-compose.run.yml"
        self.assertTrue(imported.is_file())
        services = json.loads(override.read_text())["services"]
        self.assertEqual(set(services), {"wordpress", "wordpress-fpm"})
        for service in services.values():
            self.assertEqual(service["volumes"], [{"type": "bind", "source": str(runtime / "import"),
                                                  "target": "/import", "read_only": True,
                                                  "bind": {"create_host_path": False}}])
            self.assertNotIn("environment", service)
        with zipfile.ZipFile(imported) as contents:
            self.assertEqual(contents.read("app/public/index.php"), b"source wordpress")
        imported_bytes = imported.read_bytes()
        self.assertEqual(list(self.tmp.iterdir()), [])
        for command in ("logs", "down", "ps"):
            self.assert_success(self.invoke("--site", archive, command))
            self.assertEqual(imported.read_bytes(), imported_bytes)
            self.assertTrue(override.is_file())
            self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertEqual(self.calls(), [self.compose(archive, "up", "-d"),
                                       *[self.compose(archive, cmd) for cmd in ("logs", "down", "ps")]])
        self.assertEqual(archive.read_bytes(), original)

    def test_new_up_rebuilds_import_without_deleted_files(self):
        self.require_tools("zip")
        removed = self.site / "app/public/removed.txt"
        removed.write_text("old")
        self.assert_success(self.invoke("--site", self.site, "up", "-d"))
        removed.unlink()
        self.assert_success(self.invoke("--site", self.site, "up", "-d"))
        with zipfile.ZipFile(self.runtime(self.site) / "import/site.zip") as archive:
            self.assertNotIn("app/public/removed.txt", archive.namelist())

    def test_session_decline_runs_up_logs_down_without_saving(self):
        self.require_tools("zip")
        for logs_status, expected in ((0, 0), (130, 0), (23, 23)):
            with self.subTest(logs_status=logs_status):
                self.clear_calls()
                result = self.invoke("--site", self.site, "--skip-setup", "session", "--build", "wordpress",
                                     stdin="n\n", env={"DOCKER_EXIT_CODES": json.dumps({"logs": logs_status})})
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(self.calls(), [self.compose(self.site, "up", "-d", "--build", "wordpress"),
                                               self.compose(self.site, "logs", "-f"),
                                               self.compose(self.site, "down")])
                self.assertIn("Live file changes remain on disk; database changes remain in Docker volumes", result.stdout)
                self.assertEqual((self.site / "app/public/index.php").read_text(), "source wordpress")

    def assert_live_archive(self, path):
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.read("app/public/index.php"), b"live wordpress")
            self.assertEqual(archive.read("app/sql/local.sql"), b"-- mock database dump\n")
            self.assertFalse(any("cache" in name or ".localwp-docker-init-done" in name
                                 for name in archive.namelist()))

    def test_export_default_and_explicit_output_leave_source_unchanged(self):
        self.require_tools("zip")
        for output in (None, "custom export.zip"):
            with self.subTest(output=output):
                args = () if output is None else (output,)
                self.assert_success(self.invoke("--site", self.site, "export", *args))
                self.assert_live_archive(self.cwd / (output or "site-export.zip"))
                self.assertEqual((self.site / "app/public/index.php").read_text(), "source wordpress")
                self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertIn(["exec", "mock-db", "mysqldump", "-uuser", "-ppassword", "local"], self.calls())

    def test_save_directory_only_dumps_database_and_preserves_live_files(self):
        public = self.site / "app/public"
        (public / "editor-change.php").write_text("work in progress")
        inode = public.stat().st_ino
        self.assert_success(self.invoke("--site", self.site, "save"))
        self.assertEqual((public / "index.php").read_text(), "source wordpress")
        self.assertEqual((public / "editor-change.php").read_text(), "work in progress")
        self.assertEqual(public.stat().st_ino, inode)
        self.assertEqual((self.site / "app/sql/local.sql").read_text(), "-- mock database dump\n")
        self.assertFalse(any(call[0] == "cp" for call in self.calls()))
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_failed_database_save_preserves_previous_sql_and_live_files(self):
        result = self.invoke("--site", self.site, "save",
                             env={"DOCKER_EXIT_CODES": '{"exec": 19}'})
        self.assertEqual(result.returncode, 19)
        self.assertEqual((self.site / "app/sql/local.sql").read_text(), "source sql")
        self.assertEqual((self.site / "app/public/index.php").read_text(), "source wordpress")

    def test_directory_mounts_are_live_for_apache_fpm_and_nginx(self):
        self.assert_success(self.invoke("--site", self.site, "config"))
        runtime = self.runtime(self.site)
        services = json.loads((runtime / "docker-compose.run.yml").read_text())["services"]
        public = self.site / "app/public"
        for name in ("wordpress", "wordpress-fpm", "nginx"):
            with self.subTest(service=name):
                service = services[name]
                mounts = {mount["target"]: mount for mount in service["volumes"]}
                self.assertEqual(mounts["/var/www/html"], {
                    "type": "bind", "source": str(public), "target": "/var/www/html",
                    "read_only": name == "nginx", "bind": {"create_host_path": False}})
                if name != "nginx":
                    self.assertEqual(service["environment"], {
                        "LOCALWP_LIVE_FILES": "1", "LOCALWP_HOST_UID": str(os.getuid()),
                        "LOCALWP_HOST_GID": str(os.getgid())})
                    self.assertEqual(mounts["/localwp-state"]["source"], "wp_data")
                    self.assertTrue(mounts["/localwp-state"]["volume"]["nocopy"])
                    ini = mounts["/usr/local/etc/php/conf.d/zzz-localwp-development.ini"]
                    self.assertTrue(ini["read_only"])
                    self.assertEqual(Path(ini["source"]).read_text(),
                                     "opcache.validate_timestamps=1\nopcache.revalidate_freq=0\n")

    def test_mount_paths_escape_compose_interpolation(self):
        special = self.base / "Site $NAME: test"
        (special / "app/public").mkdir(parents=True)
        self.assert_success(self.invoke("--site", special, "config"))
        services = json.loads((self.runtime(special) / "docker-compose.run.yml").read_text())["services"]
        mounts = services["wordpress"]["volumes"]
        mount = next(item for item in mounts if item["target"] == "/var/www/html")
        self.assertEqual(mount["source"], str(special / "app/public").replace("$", "$$"))

    def test_save_overwrites_source_zip_with_live_data(self):
        archive = self.make_zip()
        self.assert_success(self.invoke("--site", archive, "save"))
        self.assert_live_archive(archive)
        self.assertEqual(list(self.tmp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
