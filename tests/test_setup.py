"""Stdlib-only setup tests; no Docker or shell evaluation required."""
import contextlib
import importlib.util
import io
import itertools
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("localwp_setup", ROOT / "setup.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class DotenvTests(unittest.TestCase):
    def test_literal_round_trip(self):
        for value in ("", " spaces # hash ", "a'b", 'a"b', "a\\b\\", "a\\'b",
                      chr(36) + "HOME " + chr(36) + "{SECRET}", "tab\tvalue", "café"):
            with self.subTest(value=value):
                saved, _ = setup.parse_env(setup.serialize({"MYSQL_PASSWORD": value}))
                self.assertEqual(saved["MYSQL_PASSWORD"], value)

    def test_compose_backslash_and_dollar_escaping(self):
        self.assertEqual(setup.quote_value("a\\\\b"), "'a\\\\b'")
        self.assertEqual(setup.parse_value("'a\\\\b'")[0], "a\\\\b")
        self.assertEqual(setup.parse_value("unquoted\\")[0], "unquoted\\")
        self.assertEqual(setup.parse_value('"\\$HOME $$HOME"')[0], "$HOME $HOME")
        self.assertEqual(setup.parse_value("'$HOME $$HOME'")[0], "$HOME $$HOME")
        self.assertEqual(setup.quote_value("$HOME\\"), '"$$HOME\\\\"')
        alphabet = ("a", "\\", "'", '"', "$", "#")
        for length in range(5):
            for chars in itertools.product(alphabet, repeat=length):
                value = "".join(chars)
                with self.subTest(value=value):
                    self.assertEqual(setup.parse_value(setup.quote_value(value))[0], value)

    def test_common_quotes_comments_export(self):
        text = ('export MYSQL_USER = "some user" # comment\n'
                "MYSQL_PASSWORD='a\\'b # literal'\n"
                "MYSQL_DATABASE=word#press # comment\n"
                'UNKNOWN="a\\tb\\\\c\\q"\n')
        values, records = setup.parse_env(text)
        self.assertEqual(values["MYSQL_USER"], "some user")
        self.assertEqual(values["MYSQL_PASSWORD"], "a'b # literal")
        self.assertEqual(values["MYSQL_DATABASE"], "word#press")
        self.assertEqual(values["UNKNOWN"], "a\tb\\c\\q")
        self.assertEqual(setup.serialize({}, records), text)

    def test_preserve_unknown_comments_order_duplicates_and_crlf(self):
        text = ("# heading\r\nexport MYSQL_USER = old # keep\r\n"
                "OTHER = 'untouched\\thing'  # exact\r\n\r\nMYSQL_USER=last")
        saved, records = setup.parse_env(text)
        self.assertEqual(saved["MYSQL_USER"], "last")
        result = setup.serialize({"MYSQL_USER": "new", "HTTP_PORT": "80"}, records)
        self.assertEqual(result, "# heading\r\nexport MYSQL_USER = 'new' # keep\r\n"
                         "OTHER = 'untouched\\thing'  # exact\r\n\r\n"
                         "MYSQL_USER='new'\nHTTP_PORT='80'\n")

    def test_reject_malformed_without_echoing_secrets(self):
        for text in ("MYSQL_PASSWORD='secret\ncontinued'\n", 'X="secret',
                     "X='secret' garbage", "source secret", "X=secret\\\ncontinued",
                     'X="secret\\nline"', "X=secret\0"):
            with self.subTest(text=text), self.assertRaises(ValueError) as error:
                setup.parse_env(text)
            self.assertNotIn("secret", str(error.exception))

    def test_precedence_including_saved_empty(self):
        values = setup.initial_values({"MYSQL_PASSWORD": "", "PHP_VERSION": "custom"},
                                      {"MYSQL_PASSWORD": "shell", "HTTP_PORT": "9000"})
        self.assertEqual(values["MYSQL_PASSWORD"], "")
        self.assertEqual(values["PHP_VERSION"], "custom")
        self.assertEqual(values["HTTP_PORT"], "9000")
        self.assertEqual(values["MYSQL_USER"], "wordpress")

    def test_validation(self):
        for value in ("1", "8080", "65535"):
            setup.validate("HTTP_PORT", value)
        for value in ("0", "65536", "-1", "1.5", "abc", ""):
            with self.assertRaises(ValueError):
                setup.validate("HTTP_PORT", value)
        for value in ("http://localhost:8080", "https://example.test/path", "http://[::1]:80"):
            setup.validate("LOCAL_URL", value)
        for value in ("ftp://example.test", "http://", "localhost", "http://host:bad", "http://a b"):
            with self.assertRaises(ValueError):
                setup.validate("LOCAL_URL", value)


class WizardTests(unittest.TestCase):
    def test_editable_prefill_and_cleanup(self):
        import readline
        def answer(_):
            hook.call_args_list[0].args[0]()
            return ""
        with patch.object(readline, "set_startup_hook") as hook, \
                patch.object(readline, "insert_text") as insert, \
                patch("builtins.input", side_effect=answer):
            self.assertEqual(setup.text_prompt("MYSQL_USER", "saved"), "saved")
        insert.assert_called_once_with("saved")
        self.assertIsNone(hook.call_args.args[0])
        with patch.object(readline, "set_startup_hook") as hook, \
                patch("builtins.input", side_effect=EOFError), self.assertRaises(EOFError):
            setup.text_prompt("MYSQL_USER", "saved")
        self.assertIsNone(hook.call_args.args[0])

    def test_secret_enter_never_shows_current(self):
        with patch.object(setup.getpass, "getpass", return_value="") as prompt:
            self.assertEqual(setup.secret_prompt("MYSQL_PASSWORD", "top-secret"), "top-secret")
        self.assertNotIn("top-secret", prompt.call_args.args[0])

    def test_saved_custom_php_and_enter(self):
        values = dict(setup.DEFAULTS, PHP_VERSION="8.6-custom")
        with patch.object(setup, "menu", side_effect=lambda name, choices, current: current) as menu, \
                patch.object(setup, "text_prompt", side_effect=lambda name, current: current), \
                patch.object(setup, "secret_prompt", side_effect=lambda name, current: current):
            self.assertEqual(setup.wizard(values), values)
        php_choices = menu.call_args_list[1].args[1]
        self.assertEqual([v for v, _ in php_choices], list(setup.PHP_VERSIONS) + ["8.6-custom"])
        self.assertEqual(menu.call_args_list[0].args[1], [("apache", "Apache"), ("nginx", "nginx + PHP-FPM")])

    def test_invalid_port_reprompts(self):
        with patch.object(setup, "text_prompt", side_effect=["0", "65536", "9000"]) as prompt, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.wizard({"HTTP_PORT": "8080"}), {"HTTP_PORT": "9000"})
        self.assertEqual(prompt.call_count, 3)


class MainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / ".env"
        self.original = b"# preserve\nMYSQL_PASSWORD='saved-secret'\nEXTRA=literal\n"
        self.path.write_bytes(self.original)

    def interactive(self, answer="yes", failure=None):
        output = io.StringIO()
        with patch.object(sys.stdin, "isatty", return_value=True), \
                patch.dict(os.environ, {"MYSQL_PASSWORD": "shell-secret"}, clear=True), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                patch.object(output, "isatty", return_value=True), \
                patch.object(setup, "wizard", side_effect=failure or (lambda values: values)), \
                patch("builtins.input", return_value=answer):
            result = setup.main([str(self.path)])
        return result, output.getvalue()

    def test_confirm_save_and_warnings_without_secrets(self):
        result, output = self.interactive()
        self.assertEqual(result, 0)
        self.assertIn("MYSQL_PASSWORD", output)
        self.assertIn("database volumes", output)
        self.assertNotIn("saved-secret", output)
        self.assertNotIn("shell-secret", output)
        values, _ = setup.parse_env(self.path.read_text())
        self.assertEqual(values["MYSQL_PASSWORD"], "saved-secret")
        self.assertEqual(set(setup.DEFAULTS) - values.keys(), set())
        self.assertIn("EXTRA=literal\n", self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_cancellations_leave_bytes_unchanged(self):
        for answer, failure in (("no", None), ("", None), ("yes", KeyboardInterrupt), ("yes", EOFError)):
            with self.subTest(answer=answer, failure=failure):
                self.assertNotEqual(self.interactive(answer, failure)[0], 0)
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_malformed_file_unchanged(self):
        self.path.write_text("MYSQL_PASSWORD='private\nunfinished")
        before = self.path.read_bytes()
        result, output = self.interactive()
        self.assertNotEqual(result, 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn("private", output)

    def test_no_tty_and_defaults_cli(self):
        command = [sys.executable, str(ROOT / "setup.py")]
        result = subprocess.run(command + [str(self.path)], input="", capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--skip-setup", result.stderr)
        self.assertEqual(self.path.read_bytes(), self.original)
        result = subprocess.run(command + ["--defaults", str(self.path)], input="", capture_output=True,
                                text=True, timeout=5, env=dict(os.environ, PHP_VERSION="wrong"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(setup.parse_env(self.path.read_text())[0], setup.DEFAULTS)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_atomic_failure_keeps_original_and_removes_temporary(self):
        with patch.object(setup.os, "replace", side_effect=OSError("failure")), self.assertRaises(OSError):
            setup.atomic_write(self.path, "replacement")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


@unittest.skipUnless(os.name == "posix", "POSIX terminal required")
class TerminalTests(unittest.TestCase):
    def test_arrow_keys_enter_and_cancellation_restore_terminal(self):
        import pty
        import termios
        for keys, expected in ((b"\r", "8.2"), (b"\x1b[A\r", "8.1"),
                               (b"\x1b[B\r", "8.3"), (b"\x03", "CANCELLED"),
                               (b"\x04", "CANCELLED")):
            with self.subTest(keys=keys):
                master, slave = pty.openpty()
                original = termios.tcgetattr(slave)
                code = ("import setup\ntry:\n print('RESULT=' + setup.menu('PHP_VERSION', "
                        "[(v,v) for v in setup.PHP_VERSIONS], '8.2'))\n"
                        "except (EOFError, KeyboardInterrupt):\n print('RESULT=CANCELLED')\n")
                process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT,
                                           stdin=slave, stdout=slave, stderr=slave)
                output = b""
                try:
                    deadline = time.monotonic() + 5
                    while b"> 8.2" not in output and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            output += os.read(master, 4096)
                    self.assertIn(b"> 8.2", output)
                    os.write(master, keys)
                    process.wait(timeout=5)
                    while select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 4096)
                    self.assertIn(("RESULT=" + expected).encode(), output)
                    self.assertEqual(termios.tcgetattr(slave), original)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    os.close(master)
                    os.close(slave)


if __name__ == "__main__":
    unittest.main()
