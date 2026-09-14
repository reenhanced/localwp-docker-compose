#!/usr/bin/env python3
"""Interactive, literal Compose dotenv setup; no shell evaluation."""
import argparse
import getpass
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

DEFAULTS = dict(COMPOSE_PROFILES="apache", PHP_VERSION="8.2",
                LOCAL_URL="http://localhost:8080", HTTP_PORT="8080",
                MYSQL_DATABASE="wordpress", MYSQL_USER="wordpress",
                MYSQL_PASSWORD="wordpress", MYSQL_ROOT_PASSWORD="rootpassword")
PHP_VERSIONS = ("7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5")
ASSIGNMENT = re.compile(r"^(\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*)(.*)$")


def parse_value(text):
    """Return a literal value and its trailing whitespace/comment, without expansion."""
    if not text.startswith(("'", '"')):
        match = re.search(r"(?:^|\s+)#", text)
        end = match.start() if match else len(text)
        value = text[:end].rstrip()

        return value, text[len(value):]
    quote, value, i = text[0], [], 1
    escapes = {"n": "\n", "r": "\r", "t": "\t", "$": "$"} if quote == '"' else {}
    while i < len(text):
        char = text[i]
        if char == quote:
            suffix = text[i + 1:]
            if suffix.strip() and not suffix.lstrip().startswith("#"):
                raise ValueError("Malformed quoted dotenv value")
            result = "".join(value)
            if any(c in result for c in "\r\n\0"):
                raise ValueError("Unsupported multiline or NUL dotenv value")
            return result, suffix
        if quote == '"' and text[i:i + 2] == "$$":
            value.append("$")
            i += 2
            continue
        if char == "\\" and i + 1 < len(text):
            following = text[i + 1]
            if following in (quote, "\\") or following in escapes:
                value.append("\\\\" if quote == "'" and following == "\\"
                             else escapes.get(following, following))
                i += 2
                continue
        value.append(char)
        i += 1
    raise ValueError("Unsupported multiline or unterminated dotenv quote")


def parse_env(text):
    """Return saved values and lossless (raw, key, prefix, suffix, newline) records."""
    values, records = {}, []
    for number, raw in enumerate(text.splitlines(keepends=True), 1):
        line = raw.rstrip("\r\n")
        newline = raw[len(line):]
        if "\0" in line:
            raise ValueError(f"Invalid dotenv at line {number}")
        if not line.strip() or line.lstrip().startswith("#"):
            records.append((raw, None, "", "", newline))
            continue
        match = ASSIGNMENT.fullmatch(line)
        if not match:
            raise ValueError(f"Unsupported dotenv syntax at line {number}")
        prefix, key, source = match.groups()
        try:
            value, suffix = parse_value(source)
        except ValueError:
            raise ValueError(f"Malformed or unsupported dotenv at line {number}") from None
        values[key] = value
        records.append((raw, key, prefix, suffix, newline))
    return values, records


def quote_value(value):
    if any(c in value for c in "\r\n\0"):
        raise ValueError("Multiline and NUL values are not supported")
    # Compose preserves paired backslashes inside single quotes. Odd runs before
    # a quote or the terminator need double quotes, with interpolation escaped.
    if any(len(match.group()) % 2 for match in re.finditer(r"\\+(?='|$)", value)):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'
    return "'" + value.replace("'", "\\'") + "'"


def serialize(values, records=()):
    parts, seen = [], set()
    for raw, key, prefix, suffix, newline in records:
        if key in values:
            parts.append(prefix + quote_value(values[key]) + suffix + newline)
            seen.add(key)
        else:
            parts.append(raw)
    missing = [key for key in values if key not in seen]
    if missing and parts and not parts[-1].endswith("\n"):
        parts.append("\n")
    parts.extend(f"{key}={quote_value(values[key])}\n" for key in missing)
    return "".join(parts)


def atomic_write(path, text):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initial_values(saved, environ):
    return {key: saved.get(key, environ.get(key, default))
            for key, default in DEFAULTS.items()}


def text_prompt(name, current):
    import readline
    readline.set_startup_hook(lambda: readline.insert_text(current))
    try:
        return input(name + ": ") or current
    finally:
        readline.set_startup_hook(None)


def secret_prompt(name, current):
    return getpass.getpass(name + " (hidden; Enter keeps current): ") or current


def menu(name, choices, current):
    """Choices are (value, label) pairs. Never allow arbitrary typed values."""
    index = next((i for i, item in enumerate(choices) if item[0] == current), 0)
    try:
        import termios
        import tty
    except ImportError:
        for i, (_, label) in enumerate(choices, 1):
            print(f"{i}. {label}")
        while True:
            answer = input(f"{name} [{index + 1}]: ")
            if not answer:
                return choices[index][0]
            if answer.isdigit() and 1 <= int(answer) <= len(choices):
                return choices[int(answer) - 1][0]
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    print(name + " (Up/Down, Enter)")
    try:
        tty.setraw(fd)
        while True:
            # Labels can include saved custom versions; do not emit terminal controls.
            label = choices[index][1]
            label = "".join(c if c.isprintable() else "?" for c in label)
            sys.stdout.write("\r\033[2K> " + label)
            sys.stdout.flush()
            key = os.read(fd, 1)
            if key in (b"", b"\x04"):
                raise EOFError
            if key == b"\x03":
                raise KeyboardInterrupt
            if key in (b"\r", b"\n"):
                return choices[index][0]
            if key == b"\x1b":
                # Read escape sequences incrementally so Ctrl-C/EOF still cancel.
                import select
                sequence = b""
                for _ in range(2):
                    if not select.select([fd], [], [], 0.2)[0]:
                        break
                    char = os.read(fd, 1)
                    if char in (b"", b"\x04"):
                        raise EOFError
                    if char == b"\x03":
                        raise KeyboardInterrupt
                    sequence += char
                if sequence in (b"[A", b"OA"):
                    index = (index - 1) % len(choices)
                elif sequence in (b"[B", b"OB"):
                    index = (index + 1) % len(choices)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
        print()


def validate(name, value):
    quote_value(value)
    if name == "HTTP_PORT" and not (re.fullmatch(r"[0-9]+", value)
                                    and 1 <= int(value) <= 65535):
        raise ValueError("HTTP_PORT must be an integer from 1 to 65535")
    if name == "LOCAL_URL":
        try:
            url = urlsplit(value)
            valid = (url.scheme in ("http", "https") and url.hostname
                     and not any(c.isspace() or ord(c) < 32 for c in value))
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("LOCAL_URL must be an http(s) URL with a host")


def wizard(values):
    values = dict(values)
    for name, current in values.items():
        if name == "COMPOSE_PROFILES":
            values[name] = menu(name, [("apache", "Apache"),
                                       ("nginx", "nginx + PHP-FPM")], current)
        elif name == "PHP_VERSION":
            choices = [(v, v) for v in PHP_VERSIONS]
            if current not in PHP_VERSIONS:
                choices.append((current, current + " (existing)"))
            values[name] = menu(name, choices, current)
        else:
            prompt = secret_prompt if "PASSWORD" in name else text_prompt
            while True:
                value = prompt(name, current)
                try:
                    validate(name, value)
                except ValueError as error:
                    print(error)
                    continue
                values[name] = value
                break
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--defaults", action="store_true", help="write built-in defaults without prompts")
    parser.add_argument("env_file", metavar="ENV_FILE")
    args = parser.parse_args(argv)
    path = Path(args.env_file)
    try:
        if args.defaults:
            atomic_write(path, serialize(DEFAULTS))
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("Setup requires a terminal; use --skip-setup in the launcher "
                  "or setup.py --defaults OUTPUT_FILE.", file=sys.stderr)
            return 1
        text = ""
        if path.exists():
            with path.open(encoding="utf-8", newline="") as stream:
                text = stream.read()
        saved, records = parse_env(text)
        overrides = [key for key in DEFAULTS if key in os.environ]
        if overrides:
            print("Warning: shell environment overrides saved fields in Compose: " + ", ".join(overrides))
        print("Warning: changing database credentials will not update existing database volumes.")
        values = wizard(initial_values(saved, os.environ))
        while True:
            answer = input("Save configuration? [y/N]: ").strip().lower()
            if answer in ("", "n", "no"):
                print("Cancelled; no file changed.")
                return 1
            if answer in ("y", "yes"):
                break
        atomic_write(path, serialize(values, records))
        print("Configuration saved.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; no file changed.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        # Never echo file contents or secret values in diagnostics.
        reason = ("invalid or unsupported dotenv syntax/value (multiline values are not supported)"
                  if isinstance(error, ValueError) else "could not read or atomically write ENV_FILE")
        print("Setup failed; no configuration saved: " + reason, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
