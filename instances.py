"""
    RDS Master - instances: several encoders from one install.

    Copyright (C) 2026 Rían Mac Guinneál (FMDX.ie)

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.


One install, several encoders. A station running two services wants two of
these, not two installs: one program directory, one tray icon, and an encoder
per service with its own configuration, its own port and its own soundcard.

An instance is a name, a data directory and a port. The encoder already takes
its data directory from RDS_DATA_DIR or --data-dir and its port from the
datasets.json inside it, so an instance needs nothing from the encoder itself -
only somewhere to write down which ones exist. That is this registry, kept
beside the default instance's configuration so the tray, the installer and the
encoder all read the same list.

Nothing here imports app.py: the tray is a separate program and the installer
calls this through the encoder's command line.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

REGISTRY_NAME = "instances.json"
DEFAULT_NAME = "Main"
DEFAULT_PORT = 5000
# No limit on how many instances there can be: an instance is a directory and
# a port, and what runs out is the machine rather than anything here. Past this
# many the installer asks whether that is really meant, because each one is a
# full encoder with its own soundcard and its own share of the CPU.
MANY_INSTANCES = 8

# The scheduled task the installer makes for the first instance. It is kept
# under that exact name so an upgrade recognises it; the others are suffixed.
WINDOWS_TASK = "RDS Master encoder"


# ---------------------------------------------------------------------------
# Where things live
# ---------------------------------------------------------------------------

def running_installed() -> bool:
    return getattr(sys, "frozen", False)


def default_data_directory() -> str:
    """The data directory of the first instance - the one an install creates.

    The same rule the encoder uses, so a single-instance install that has never
    heard of this file carries on exactly as before.
    """
    override = os.environ.get("RDS_DATA_DIR")
    if override:
        return override
    if not running_installed():
        return os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        base = os.environ.get("PROGRAMDATA") or os.path.expanduser("~")
        return os.path.join(base, "RDS Master")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/RDS Master")
    return os.path.expanduser("~/.config/rds-master")


def registry_root() -> str:
    """Where the list of instances is kept.

    Always the default instance's directory, even when this process is running
    as another instance - otherwise each one would keep its own list and none of
    them would know about the others. RDS_DATA_DIR is deliberately ignored here.
    """
    if not running_installed():
        return os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        base = os.environ.get("PROGRAMDATA") or os.path.expanduser("~")
        return os.path.join(base, "RDS Master")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/RDS Master")
    return os.path.expanduser("~/.config/rds-master")


def registry_path() -> str:
    return os.path.join(registry_root(), REGISTRY_NAME)


def slug(name: str) -> str:
    """A directory-safe form of an instance name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name).strip()).strip("-._")
    return cleaned or "instance"


def data_directory_for(name: str) -> str:
    """Where a new instance's configuration goes.

    The first instance keeps the plain directory, so an existing install is
    untouched; the others get one named after them beside it.
    """
    if slug(name).lower() == slug(DEFAULT_NAME).lower():
        return default_data_directory()
    return os.path.join(registry_root(), "instances", slug(name))


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def _write_json(path: str, data) -> str:
    """Write JSON through a temporary file. Returns '' or the reason it failed."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temporary, path)
        return ""
    except OSError as exc:
        return f"cannot write {path}: {exc}"


def load() -> list:
    """Every instance, as a list of dicts. Never empty.

    An install that predates all this has no registry, so the instance it does
    have is described from what is on disk. Writing the file is left until
    something actually changes it.
    """
    path = registry_path()
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            data = data.get("instances", [])
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict) and r.get("name")]
    except (OSError, ValueError):
        rows = []

    if not rows:
        rows = [{"name": DEFAULT_NAME,
                 "data_dir": default_data_directory(),
                 "autostart": True}]

    out, seen = [], set()
    for row in rows:
        name = str(row.get("name") or DEFAULT_NAME)
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        directory = str(row.get("data_dir") or data_directory_for(name))
        out.append({"name": name,
                    "data_dir": directory,
                    "autostart": bool(row.get("autostart", True)),
                    "port": port_of(directory)})
    return out


def save(rows: list) -> str:
    """Store the list. Returns '' or why it could not be written."""
    keep = [{"name": r["name"], "data_dir": r["data_dir"],
             "autostart": bool(r.get("autostart", True))}
            for r in rows]
    return _write_json(registry_path(), {"instances": keep})


def find(name: str):
    """One instance by name, case-insensitively, or None."""
    for row in load():
        if row["name"].lower() == str(name).strip().lower():
            return row
    return None


# ---------------------------------------------------------------------------
# An instance's own configuration
# ---------------------------------------------------------------------------

def config_path(data_dir: str) -> str:
    return os.path.join(data_dir, "datasets.json")


def _read_config(data_dir: str) -> dict:
    try:
        with open(config_path(data_dir), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def port_of(data_dir: str) -> int:
    try:
        return int(_read_config(data_dir).get("http_port", DEFAULT_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def host_of(data_dir: str) -> str:
    return str(_read_config(data_dir).get("http_host", "0.0.0.0") or "0.0.0.0")


def site_of(data_dir: str) -> str:
    return str(_read_config(data_dir).get("site_name", "") or "").strip()


def set_port(data_dir: str, port: int, host: str | None = None) -> str:
    """Change an instance's port, and its bind address when one is given."""
    port = int(port)
    if not 1 <= port <= 65535:
        return f"port {port} is out of range - use 1 to 65535"
    data = _read_config(data_dir)
    data["http_port"] = port
    if host is not None:
        data["http_host"] = host
    return _write_json(config_path(data_dir), data)


def set_password(data_dir: str, user: str | None, password: str | None) -> str:
    """Change the web interface's user name or password for one instance.

    The encoder reads these when it starts, so the caller restarts it
    afterwards - this only writes them down.
    """
    data = _read_config(data_dir)
    auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    if user:
        auth["user"] = str(user).strip()
    if password:
        auth["pass"] = str(password)
    if not auth:
        return "nothing to change"
    auth.setdefault("user", "admin")
    auth.setdefault("pass", "admin")
    data["auth"] = auth
    return _write_json(config_path(data_dir), data)


def ports_in_use() -> set:
    """Every port the known instances are configured for."""
    return {row["port"] for row in load()}


def free_port(start: int = DEFAULT_PORT) -> int:
    """The next port no instance is using, counting up from start."""
    taken = ports_in_use()                  # read once, however many there are
    port = max(1, int(start))
    while port in taken and port < 65535:
        port += 1
    return port


# ---------------------------------------------------------------------------
# Starting and stopping
# ---------------------------------------------------------------------------

def task_name(name: str) -> str:
    """The scheduled task for an instance.

    The first one keeps the original name so an upgrade finds the task it made
    before instances existed.
    """
    if name.lower() == DEFAULT_NAME.lower():
        return WINDOWS_TASK
    return f"{WINDOWS_TASK} ({name})"


def encoder_executable() -> str:
    """The encoder program, wherever this is being run from."""
    if not running_installed():
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    name = "rds-master.exe" if sys.platform == "win32" else "rds-master"
    here = os.path.dirname(os.path.abspath(sys.executable))
    for folder in (here, os.path.dirname(here), "/opt/rds-master"):
        candidate = os.path.join(folder, name)
        if os.path.exists(candidate):
            return candidate
    return ""


def launch_command(data_dir: str) -> list:
    """How to start an encoder for this data directory."""
    executable = encoder_executable()
    if not executable:
        return []
    if executable.endswith(".py"):
        return [sys.executable, executable, "--data-dir", data_dir]
    return [executable, "--data-dir", data_dir]


def _run(command: list) -> tuple:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=45)
        return done.returncode, (done.stdout + done.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


def register_autostart(row: dict) -> tuple:
    """Have the system start this instance at logon. Windows only, for now.

    Elsewhere the tray starts what is registered as a service, and generating a
    systemd unit per instance is a separate piece of work - so this reports
    what it did rather than pretending.
    """
    if sys.platform != "win32":
        return 1, "automatic start per instance is only set up on Windows so far"
    command = launch_command(row["data_dir"])
    if not command:
        return 1, "the encoder was not found next to this program"
    quoted = " ".join(f'\\"{part}\\"' for part in command)
    return _run(["schtasks", "/Create", "/F", "/TN", task_name(row["name"]),
                 "/TR", quoted, "/SC", "ONLOGON", "/RL", "HIGHEST"])


def unregister_autostart(row: dict) -> tuple:
    if sys.platform != "win32":
        return 0, ""
    return _run(["schtasks", "/Delete", "/F", "/TN", task_name(row["name"])])


def is_up(row: dict, timeout: float = 1.5) -> bool:
    """Whether this instance answers on its port."""
    import socket

    host = row.get("host") or host_of(row["data_dir"])
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    try:
        with socket.create_connection((host, row["port"]), timeout=timeout):
            return True
    except OSError:
        return False


def statuses(rows: list, timeout: float = 0.8) -> dict:
    """Ask every instance at once whether it is answering: {name: True/False}.

    One at a time meant waiting out the timeout for each stopped instance in
    turn, which is fine for one and useless for twenty. The work is all waiting
    on a socket, so threads are exactly the right tool and the whole sweep
    takes about as long as the slowest single answer.
    """
    if not rows:
        return {}
    results = {}
    if len(rows) == 1:
        results[rows[0]["name"]] = is_up(rows[0], timeout=timeout)
        return results

    import threading

    def probe(row):
        results[row["name"]] = is_up(row, timeout=timeout)

    threads = [threading.Thread(target=probe, args=(row,), daemon=True)
               for row in rows]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout + 1.0)
    return {row["name"]: results.get(row["name"], False) for row in rows}


def start(row: dict) -> tuple:
    """Start one instance, by its scheduled task where there is one."""
    if sys.platform == "win32":
        code, out = _run(["schtasks", "/Query", "/TN", task_name(row["name"])])
        if code == 0:
            return _run(["schtasks", "/Run", "/TN", task_name(row["name"])])
    command = launch_command(row["data_dir"])
    if not command:
        return 1, "the encoder was not found next to this program"
    try:
        creation = 0x08000000 if sys.platform == "win32" else 0   # no console
        subprocess.Popen(command, creationflags=creation,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True)
        return 0, "started"
    except OSError as exc:
        return 1, str(exc)


def stop(row: dict) -> tuple:
    """Stop one instance, and only that one.

    Several encoders run from the same executable, so stopping by image name
    would take them all down. The one to stop is the process holding this
    instance's port.
    """
    pids = _pids_on_port(row["port"])
    if not pids:
        return 0, "not running"
    failures = []
    for pid in pids:
        if sys.platform == "win32":
            code, out = _run(["taskkill", "/F", "/PID", str(pid)])
        else:
            code, out = _run(["kill", str(pid)])
        if code != 0:
            failures.append(out or f"could not stop {pid}")
    return (1, "; ".join(failures)) if failures else (0, "stopped")


def restart(row: dict) -> tuple:
    stop(row)
    for _ in range(20):
        if not is_up(row, timeout=0.4):
            break
        time.sleep(0.25)
    return start(row)


def _pids_on_port(port: int) -> list:
    """Process ids listening on a port. Empty when nothing is."""
    pids = []
    if sys.platform == "win32":
        code, out = _run(["netstat", "-ano", "-p", "TCP"])
        if code != 0:
            return []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" \
                    and parts[3].upper() == "LISTENING" \
                    and parts[1].rsplit(":", 1)[-1] == str(port):
                try:
                    pids.append(int(parts[4]))
                except ValueError:
                    pass
    else:
        code, out = _run(["ss", "-lptnH", f"sport = :{port}"])
        if code != 0:
            code, out = _run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"])
            if code == 0:
                for line in out.split():
                    try:
                        pids.append(int(line))
                    except ValueError:
                        pass
            return sorted(set(pids))
        for match in re.finditer(r"pid=(\d+)", out):
            pids.append(int(match.group(1)))
    return sorted(set(pids))


# ---------------------------------------------------------------------------
# Adding and removing
# ---------------------------------------------------------------------------

def add(name: str, port: int | None = None, autostart: bool = True,
        seed_from: str | None = None) -> tuple:
    """Create an instance. Returns (row or None, message).

    The new instance starts with a configuration of its own holding nothing but
    its port and its bind address: no profiles are copied unless asked for, so
    one station's PI and RadioText cannot quietly become another's.
    """
    name = str(name).strip()
    if not name:
        return None, "an instance needs a name"
    if len(name) > 40:
        return None, "that name is too long - 40 characters at most"
    rows = load()
    if any(r["name"].lower() == name.lower() for r in rows):
        return None, f"there is already an instance called {name!r}"

    directory = data_directory_for(name)
    if any(os.path.normcase(os.path.abspath(r["data_dir"]))
           == os.path.normcase(os.path.abspath(directory)) for r in rows):
        return None, f"{directory} is already another instance's"

    chosen = free_port(port or DEFAULT_PORT + len(rows))
    if port and chosen != int(port):
        return None, f"port {port} is already taken by another instance"

    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        return None, f"cannot create {directory}: {exc}"

    if seed_from and os.path.isfile(config_path(seed_from)):
        try:
            with open(config_path(seed_from), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        data.pop("instances", None)
    else:
        data = {}
    data["http_port"] = chosen
    data.setdefault("http_host", "0.0.0.0")
    data.setdefault("site_name", name)
    problem = _write_json(config_path(directory), data)
    if problem:
        return None, problem

    row = {"name": name, "data_dir": directory, "autostart": bool(autostart),
           "port": chosen}
    rows.append(row)
    problem = save(rows)
    if problem:
        return None, problem
    if autostart:
        register_autostart(row)
    return row, f"{name} created on port {chosen}"


def remove(name: str, delete_data: bool = False) -> tuple:
    """Take an instance out of the list. Returns (ok, message).

    Its configuration is left on disk unless asked for, because a profile with
    a station's identity in it is not something to delete on a menu click.
    """
    rows = load()
    if len(rows) <= 1:
        return False, "the last instance cannot be removed"
    row = find(name)
    if row is None:
        return False, f"there is no instance called {name!r}"
    stop(row)
    unregister_autostart(row)
    remaining = [r for r in rows if r["name"].lower() != row["name"].lower()]
    problem = save(remaining)
    if problem:
        return False, problem
    if delete_data:
        import shutil

        try:
            shutil.rmtree(row["data_dir"])
        except OSError as exc:
            return True, f"{row['name']} removed, but {row['data_dir']}: {exc}"
        return True, f"{row['name']} removed and its configuration deleted"
    return True, f"{row['name']} removed - its configuration is still in {row['data_dir']}"


def provision(count: int, base_port: int = DEFAULT_PORT,
              autostart: bool = True) -> list:
    """Make sure `count` instances exist, for the installer to call.

    The first is whatever is already there, so running this on an existing
    install adds to it rather than starting again.
    """
    messages = []
    rows = load()
    if rows and not os.path.exists(registry_path()):
        # An install that predates the registry: write what it already has, so
        # the first instance is in the list before anything is added beside it.
        if not set_port_if_unset(rows[0]["data_dir"], base_port):
            rows[0]["port"] = port_of(rows[0]["data_dir"])
        save(rows)
        messages.append(f"{rows[0]['name']} on port {rows[0]['port']}")
    want = max(1, int(count))
    while len(load()) < want:
        index = len(load()) + 1
        row, message = add(f"Instance {index}", port=free_port(base_port),
                           autostart=autostart)
        messages.append(message)
        if row is None:
            break
    return messages


def set_port_if_unset(data_dir: str, port: int) -> bool:
    """Give a configuration a port only if it has not got one. True if written."""
    data = _read_config(data_dir)
    if data.get("http_port"):
        return False
    return not set_port(data_dir, port)


def describe() -> str:
    """The list as text, for the command line."""
    lines = []
    for row in load():
        state = "running" if is_up(row) else "stopped"
        lines.append(f"{row['name']:<20} port {row['port']:<6} {state:<8} "
                     f"{row['data_dir']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

COMMANDS = ("--list-instances", "--add-instance", "--remove-instance",
            "--instance-port", "--instance-password", "--provision")


def wanted(argv: list) -> bool:
    """Whether these arguments are for the registry rather than the encoder."""
    return any(str(a).split("=", 1)[0] in COMMANDS for a in argv)


def _value(argv: list, flag: str, default=None):
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(flag + "="):
            return item.split("=", 1)[1]
    return default


def cli(argv: list) -> int:
    """Run one registry command. Returns the exit status.

    Used by the installer, which has no business reimplementing any of this,
    and by anyone at a prompt:

        rds-master --list-instances
        rds-master --add-instance "Studio 2" --port 5001
        rds-master --instance-port "Studio 2" --port 5002
        rds-master --instance-password "Studio 2" --user admin --password x
        rds-master --remove-instance "Studio 2"
        rds-master --provision 3 --base-port 5000
    """
    if "--list-instances" in argv:
        print(describe())
        return 0

    name = _value(argv, "--add-instance")
    if name:
        port = _value(argv, "--port")
        row, message = add(name, port=int(port) if port else None,
                           autostart="--no-autostart" not in argv,
                           seed_from=_value(argv, "--copy-from"))
        print(message)
        if row and "--start" in argv:
            code, out = start(row)
            print(out or ("started" if code == 0 else "could not start"))
        return 0 if row else 1

    name = _value(argv, "--remove-instance")
    if name:
        ok, message = remove(name, delete_data="--delete-data" in argv)
        print(message)
        return 0 if ok else 1

    name = _value(argv, "--instance-port")
    if name:
        row = find(name)
        if row is None:
            print(f"there is no instance called {name!r}")
            return 1
        port = _value(argv, "--port")
        if not port:
            print("--port is needed")
            return 1
        problem = set_port(row["data_dir"], int(port), _value(argv, "--host"))
        print(problem or f"{row['name']} will use port {port} when it restarts")
        return 1 if problem else 0

    name = _value(argv, "--instance-password")
    if name:
        row = find(name)
        if row is None:
            print(f"there is no instance called {name!r}")
            return 1
        problem = set_password(row["data_dir"], _value(argv, "--user"),
                               _value(argv, "--password"))
        print(problem or f"{row['name']}: sign-in details changed, "
                         "and take effect when it restarts")
        return 1 if problem else 0

    count = _value(argv, "--provision")
    if count:
        base = _value(argv, "--base-port", str(DEFAULT_PORT))
        for line in provision(int(count), int(base),
                              autostart="--no-autostart" not in argv):
            print(line)
        return 0

    print("nothing to do - try --list-instances")
    return 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))



# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

COMMANDS = ("--list-instances", "--add-instance", "--remove-instance",
            "--instance-port", "--instance-password", "--provision")


def wanted(argv: list) -> bool:
    """Whether these arguments are for the registry rather than the encoder."""
    return any(str(a).split("=", 1)[0] in COMMANDS for a in argv)


def _value(argv: list, flag: str, default=None):
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(flag + "="):
            return item.split("=", 1)[1]
    return default


def cli(argv: list) -> int:
    """Run one registry command. Returns the exit status.

    Used by the installer, which has no business reimplementing any of this,
    and by anyone at a prompt:

        rds-master --list-instances
        rds-master --add-instance "Studio 2" --port 5001
        rds-master --instance-port "Studio 2" --port 5002
        rds-master --instance-password "Studio 2" --user admin --password x
        rds-master --remove-instance "Studio 2"
        rds-master --provision 3 --base-port 5000
    """
    if "--list-instances" in argv:
        print(describe())
        return 0

    name = _value(argv, "--add-instance")
    if name:
        port = _value(argv, "--port")
        row, message = add(name, port=int(port) if port else None,
                           autostart="--no-autostart" not in argv,
                           seed_from=_value(argv, "--copy-from"))
        print(message)
        if row and "--start" in argv:
            code, out = start(row)
            print(out or ("started" if code == 0 else "could not start"))
        return 0 if row else 1

    name = _value(argv, "--remove-instance")
    if name:
        ok, message = remove(name, delete_data="--delete-data" in argv)
        print(message)
        return 0 if ok else 1

    name = _value(argv, "--instance-port")
    if name:
        row = find(name)
        if row is None:
            print(f"there is no instance called {name!r}")
            return 1
        port = _value(argv, "--port")
        if not port:
            print("--port is needed")
            return 1
        problem = set_port(row["data_dir"], int(port), _value(argv, "--host"))
        print(problem or f"{row['name']} will use port {port} when it restarts")
        return 1 if problem else 0

    name = _value(argv, "--instance-password")
    if name:
        row = find(name)
        if row is None:
            print(f"there is no instance called {name!r}")
            return 1
        problem = set_password(row["data_dir"], _value(argv, "--user"),
                               _value(argv, "--password"))
        print(problem or f"{row['name']}: sign-in details changed, "
                         "and take effect when it restarts")
        return 1 if problem else 0

    count = _value(argv, "--provision")
    if count:
        base = _value(argv, "--base-port", str(DEFAULT_PORT))
        for line in provision(int(count), int(base),
                              autostart="--no-autostart" not in argv):
            print(line)
        return 0

    print("nothing to do - try --list-instances")
    return 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))

