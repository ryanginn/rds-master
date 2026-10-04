"""The RDS Master tray icon.

This is deliberately not the encoder. It is a small separate process that
watches and controls whichever copy is running - a system service, a user
service, or one started by hand - so quitting the tray never takes a station
off air.

It talks to the encoder over the web interface it already has, and to the
operating system for starting and stopping the service.

    python rdsm_tray.py

Needs pystray and Pillow.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

APP_NAME = "RDS Master"
NEWLINE = chr(10)
SERVICE_WINDOWS = "RDSMaster"
SERVICE_SYSTEMD = "rds-master"
POLL_SECONDS = 5.0
# While something is starting, look more often: a frozen build takes about
# six seconds to unpack and import, and five-second polling makes that feel
# like twice as long.
POLL_STARTING = 1.0
STARTING_GRACE = 30.0
HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Where the encoder is, and what it is doing
# ---------------------------------------------------------------------------

def data_directory() -> str:
    """The same directory the encoder keeps its configuration in."""
    override = os.environ.get("RDS_DATA_DIR")
    if override:
        return override
    if sys.platform == "win32":
        return os.path.join(os.environ.get("PROGRAMDATA") or
                            os.path.expanduser("~"), "RDS Master")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/RDS Master")
    for candidate in ("/var/lib/rds-master", os.path.expanduser("~/.config/rds-master")):
        if os.path.isdir(candidate):
            return candidate
    return os.path.expanduser("~/.config/rds-master")


def read_settings() -> dict:
    """The bind address and port the encoder is configured for.

    Read straight from its configuration file rather than asked over the
    network, because the whole point of these two settings is the times when
    the network side is not answering.
    """
    path = os.path.join(data_directory(), "datasets.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"host": "0.0.0.0", "port": 5000, "site": "",
                "path": path, "readable": False}
    return {
        "host": str(data.get("http_host", "0.0.0.0") or "0.0.0.0"),
        "port": int(data.get("http_port", 5000) or 5000),
        "site": str(data.get("site_name", "") or "").strip(),
        "path": path,
        "readable": True,
    }


def write_settings(host: str, port: int) -> str:
    """Change the bind address and port. Returns '' or why it could not."""
    path = os.path.join(data_directory(), "datasets.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return f"cannot read {path}: {exc}"
    data["http_host"] = host
    data["http_port"] = int(port)
    try:
        # Written beside the original and moved into place, so an interrupted
        # write cannot leave a station with no configuration at all.
        temporary = path + ".tray-tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temporary, path)
    except OSError as exc:
        return f"cannot write {path}: {exc}"
    return ""


def local_url(settings: dict) -> str:
    host = settings["host"]
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{settings['port']}/"


def reachable_addresses(port: int) -> list:
    """Every address this machine can be reached on, for troubleshooting."""
    found = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            address = info[4][0]
            if ":" not in address:                 # IPv4 only, for legibility
                found.add(address)
    except OSError:
        pass
    try:
        # The address a packet to the outside would leave from, which is the
        # one that matters on a studio network with several interfaces.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))            # TEST-NET-1, never routed
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return [f"http://{a}:{port}/" for a in sorted(found)]


def port_in_use(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.6)
        return probe.connect_ex((target, port)) == 0


def encoder_status(settings: dict) -> dict:
    """What the encoder says about itself, or why it did not answer."""
    url = local_url(settings) + "healthz"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return {"up": True, "detail": json.loads(response.read() or b"{}")}
    except urllib.error.HTTPError as exc:
        # Anything that answers at all is running; the login page redirects.
        return {"up": True, "detail": {"http": exc.code}}
    except Exception as exc:
        return {"up": False, "detail": {"error": str(exc)}}


# ---------------------------------------------------------------------------
# Starting and stopping it
# ---------------------------------------------------------------------------

def _run(command: list) -> tuple:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return done.returncode, (done.stdout + done.stderr).strip()
    except Exception as exc:
        return 1, str(exc)


WINDOWS_TASK = "RDS Master encoder"


def encoder_executable() -> str:
    """The encoder, which sits beside this one in the same install."""
    name = "rds-master.exe" if sys.platform == "win32" else "rds-master"
    here = os.path.dirname(os.path.abspath(sys.executable
                                           if getattr(sys, "frozen", False)
                                           else __file__))
    for folder in (here, os.path.dirname(here), "/opt/rds-master"):
        candidate = os.path.join(folder, name)
        if os.path.exists(candidate):
            return candidate
    return ""


def service_kind() -> str:
    """How this copy is run: 'windows', 'task', 'system', 'user' or ''.

    Most Windows installs are session mode - a scheduled task at logon rather
    than a service - because a service cannot open an MME or WASAPI device. The
    tray has to drive that the same way it drives a service, or Start and Stop
    do nothing on the commonest setup there is.
    """
    if sys.platform == "win32":
        code, _ = _run(["sc", "query", SERVICE_WINDOWS])
        if code == 0:
            return "windows"
        code, out = _run(["schtasks", "/Query", "/TN", WINDOWS_TASK])
        if code == 0:
            return "task"
        return ""
    if sys.platform == "darwin":
        return "launchagent"
    code, out = _run(["systemctl", "--user", "is-enabled", SERVICE_SYSTEMD])
    if code == 0 or "disabled" in out:
        return "user"
    code, out = _run(["systemctl", "is-enabled", SERVICE_SYSTEMD])
    if code == 0 or "disabled" in out:
        return "system"
    return ""


def service_command(action: str) -> tuple:
    """Run start, stop or restart against however this copy was installed."""
    kind = service_kind()
    if kind == "windows":
        if action == "restart":
            _run(["sc", "stop", SERVICE_WINDOWS])
            time.sleep(2)
            return _run(["sc", "start", SERVICE_WINDOWS])
        return _run(["sc", action, SERVICE_WINDOWS])
    if kind == "user":
        return _run(["systemctl", "--user", action, SERVICE_SYSTEMD])
    if kind == "system":
        # pkexec asks for the password through the desktop rather than needing
        # a terminal; sudo is the fallback where it is not installed.
        helper = "pkexec" if _run(["which", "pkexec"])[0] == 0 else "sudo"
        return _run([helper, "systemctl", action, SERVICE_SYSTEMD])
    if kind == "task":
        if action in ("stop", "restart"):
            _run(["taskkill", "/F", "/IM", "rds-master.exe"])
            if action == "stop":
                return 0, "stopped"
            time.sleep(1.5)
        return _run(["schtasks", "/Run", "/TN", WINDOWS_TASK])
    if kind == "launchagent":
        label = "ie.fmdx.rdsmaster"
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        if action == "stop":
            return _run(["launchctl", "unload", plist])
        if action == "start":
            return _run(["launchctl", "load", plist])
        _run(["launchctl", "unload", plist])
        time.sleep(1)
        return _run(["launchctl", "load", plist])
    # Nothing registered: run the encoder beside us directly, which is what
    # someone who unpacked the zip rather than running an installer will have.
    executable = encoder_executable()
    if not executable:
        return 1, ("The encoder was not found next to this icon, and no service "
                   "or scheduled task is registered.")
    if action in ("stop", "restart"):
        if sys.platform == "win32":
            _run(["taskkill", "/F", "/IM", os.path.basename(executable)])
        else:
            _run(["pkill", "-f", executable])
        if action == "stop":
            return 0, "stopped"
        time.sleep(1.5)
    try:
        creation = 0x08000000 if sys.platform == "win32" else 0   # no console
        subprocess.Popen([executable], creationflags=creation,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0, "started"
    except OSError as exc:
        return 1, str(exc)


# ---------------------------------------------------------------------------
# The icon itself
# ---------------------------------------------------------------------------

def load_icon():
    from PIL import Image

    for name in ("tray.png", "rdsm-64.png", "rdsm-128.png"):
        for folder in (os.path.join(HERE, "icons"),
                       os.path.join(os.path.dirname(HERE), "icons"),
                       HERE):
            candidate = os.path.join(folder, name)
            if os.path.exists(candidate):
                return Image.open(candidate).convert("RGBA")
    # Nothing to load: a blank square beats refusing to start.
    return Image.new("RGBA", (64, 64), (200, 30, 90, 255))


def tint(image, up: bool):
    """A grey icon while the encoder is not answering, full colour when it is."""
    if up:
        return image
    grey = image.convert("LA").convert("RGBA")
    grey.putalpha(image.getchannel("A"))
    return grey


class Tray:
    def __init__(self):
        self.settings = read_settings()
        self.status = {"up": False, "detail": {}}
        self.message = ""
        self.icon = None
        # When a start was asked for, so the menu can say "starting..." rather
        # than "not answering" during the several seconds a frozen build takes
        # to unpack itself and import numpy.
        # Assume a start is in progress when the icon first appears: it is
        # launched by the installer and at logon, both times alongside the
        # encoder, so "not answering" would be wrong for the first seconds.
        self.starting_since = time.time()
        self.base_image = load_icon()

    # -- what the menu says --------------------------------------------

    def title(self) -> str:
        """The tooltip. The site name first, so several machines are telling
        apart at a glance from the notification area."""
        site = self.settings.get("site") or ""
        name = f"{APP_NAME} - {site}" if site else APP_NAME
        where = f"{self.settings['host']}:{self.settings['port']}"
        return f"{name}{NEWLINE}{'Running' if self.status['up'] else 'Not running'} - {where}"

    def status_line(self, _item=None) -> str:
        site = self.settings.get("site") or ""
        if self.status["up"]:
            where = local_url(self.settings)
            return f"{site} - running on {where}" if site else f"Running on {where}"
        if self.starting_since and time.time() - self.starting_since < STARTING_GRACE:
            return f"{site} - starting..." if site else "Starting..."
        return "Not answering - use Start, or check the port below"

    def settings_line(self, _item=None) -> str:
        return f"Listening on {self.settings['host']}:{self.settings['port']}"

    def message_line(self, _item=None) -> str:
        return self.message or "Ready"

    # -- what the menu does --------------------------------------------

    def open_ui(self, *_):
        webbrowser.open(local_url(self.settings))

    def do(self, action: str):
        """Start, stop or restart - on a thread of its own.

        Doing this inline would call icon.update_menu() from inside a menu
        callback, and on the Windows backend that is how a menu item ends up
        doing nothing visible at all.
        """
        def work():
            if action in ("start", "restart"):
                self.starting_since = time.time()
            self.message = f"{action.title()}ing..."
            code, output = service_command(action)
            self.message = (f"{action.title()} done" if code == 0
                            else (output.splitlines() or ["failed"])[0][:70])
            self.poll_once()

        def handler(*_):
            threading.Thread(target=work, daemon=True).start()
        return handler

    def show_addresses(self, *_):
        threading.Thread(target=self._show_addresses, daemon=True).start()

    def _show_addresses(self):
        lines = reachable_addresses(self.settings["port"])
        busy = port_in_use(self.settings["host"], self.settings["port"])
        body = ["Reachable at:"] + ["   " + line for line in lines]
        body.append("")
        body.append(f"Port {self.settings['port']} is "
                    + ("open and answering" if busy else "not answering"))
        body.append(f"Configuration: {self.settings['path']}")
        self.notify("\n".join(body))

    def edit_network(self, *_):
        threading.Thread(target=self._edit_network, daemon=True).start()

    def _edit_network(self):
        """Ask for a bind address and port, and write them back.

        A dialog rather than a menu of guesses, because this is the thing an
        engineer reaches for when the web interface is exactly what they cannot
        get to.
        """
        current = self.settings
        answer = ask_text(
            f"{APP_NAME} - network settings",
            "Address to listen on, then the port, separated by a space.\n"
            "0.0.0.0 means every interface.\n\n"
            f"Currently: {current['host']} {current['port']}",
            f"{current['host']} {current['port']}")
        if not answer:
            return
        parts = answer.split()
        host = parts[0] if parts else current["host"]
        try:
            port = int(parts[1]) if len(parts) > 1 else current["port"]
        except ValueError:
            self.notify("That port is not a number.")
            return
        if not 1 <= port <= 65535:
            self.notify("A port must be between 1 and 65535.")
            return
        problem = write_settings(host, port)
        if problem:
            self.notify(problem)
            return
        self.settings = read_settings()
        self.notify(f"Saved. Restart {APP_NAME} for it to listen on "
                    f"{host}:{port}.")
        self.refresh()

    def notify(self, text: str):
        self.message = text.splitlines()[0][:70]
        show_message(f"{APP_NAME}", text)
        self.refresh()

    def quit(self, *_):
        """Stop the encoder and close the icon.

        All of it on its own thread: asking, stopping and closing the icon were
        all happening inside the menu callback, which is why pressing Yes
        appeared to do nothing.
        """
        threading.Thread(target=self._quit, daemon=True).start()

    def _quit(self):
        answer = ask_yes_no(
            APP_NAME,
            "Stop RDS Master completely?" + NEWLINE + NEWLINE
            + "This takes the encoder off air and closes this icon."
            + NEWLINE
            + "Leave it running and only close the icon? Choose No.")
        if answer is None:
            return
        if answer:
            self.message = "Stopping..."
            service_command("stop")
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        # The icon's own loop may already have gone; make sure this process
        # really ends rather than lingering with no icon to click.
        time.sleep(1.0)
        os._exit(0)

    # -- keeping up to date ---------------------------------------------

    def poll_once(self):
        self.settings = read_settings()
        self.status = encoder_status(self.settings)
        if self.status["up"]:
            self.starting_since = 0.0
        self.refresh()

    def refresh(self):
        if not self.icon:
            return
        self.icon.icon = tint(self.base_image, self.status["up"])
        self.icon.title = self.title()
        self.icon.update_menu()

    def supervise(self):
        """Start the encoder if nothing is answering shortly after we appear.

        The tray is started by the installer and again at every logon, so it is
        the natural thing to notice that the encoder is not there. Relying on
        the installer alone left people with nothing running after a fresh
        install, and a scheduled task that fails leaves the same hole.
        """
        time.sleep(10.0)          # give whatever else was started time to bind
        if self.status.get("up"):
            return
        if not self.settings.get("readable"):
            return                # no configuration yet: nothing to start into
        self.message = "Starting the encoder..."
        self.starting_since = time.time()
        code, output = service_command("start")
        if code == 0:
            self.message = "Started"
        else:
            self.message = (output.splitlines() or ["could not start"])[0][:70]
        self.poll_once()

    def poll_forever(self):
        while True:
            try:
                self.poll_once()
            except Exception:
                pass
            starting = (self.starting_since
                        and time.time() - self.starting_since < STARTING_GRACE)
            time.sleep(POLL_STARTING if starting else POLL_SECONDS)

    def run(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem(self.status_line, self.open_ui, default=True),
            pystray.MenuItem(self.settings_line, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open web interface", self.open_ui),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start", self.do("start")),
            pystray.MenuItem("Stop", self.do("stop")),
            pystray.MenuItem("Restart", self.do("restart")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Network settings...", self.edit_network),
            pystray.MenuItem("Where can I reach it?", self.show_addresses),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self.message_line, None, enabled=False),
            pystray.MenuItem("Quit RDS Master", self.quit),
        )
        self.icon = pystray.Icon("rdsmaster", tint(self.base_image, False),
                                 self.title(), menu)
        threading.Thread(target=self.poll_forever, daemon=True).start()
        threading.Thread(target=self.supervise, daemon=True).start()
        self.icon.run()


# ---------------------------------------------------------------------------
# Small dialogs, without dragging in a whole toolkit
# ---------------------------------------------------------------------------

def _tk_in_its_own_thread(build):
    """Run a Tk dialog on a thread that owns it, and return what it gave back.

    Tk is bound to the thread that created it, and pystray calls menu handlers
    from its own thread. A dialog built there draws itself but never pumps its
    event queue, so the buttons do nothing and the close box is ignored. Giving
    it a thread with a real mainloop is what makes it behave.
    """
    import queue
    import threading

    answers = queue.Queue()

    def run():
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                answers.put(build(root, tkinter))
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        except Exception as exc:
            answers.put(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=300)
    try:
        result = answers.get_nowait()
    except queue.Empty:
        return None
    if isinstance(result, Exception):
        return None
    return result


def show_message(title: str, text: str) -> None:
    """A plain message. Native on Windows, where it cannot go wrong."""
    if sys.platform == "win32":
        try:
            import ctypes

            # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST
            ctypes.windll.user32.MessageBoxW(0, text, title,
                                             0x00 | 0x40 | 0x10000 | 0x40000)
            return
        except Exception:
            pass

    def build(root, tkinter):
        from tkinter import messagebox
        return messagebox.showinfo(title, text, parent=root)

    if _tk_in_its_own_thread(build) is None:
        print(f"{title}: {text}")


def ask_yes_no(title: str, text: str):
    """True, False, or None when the dialog was dismissed."""
    if sys.platform == "win32":
        try:
            import ctypes

            # MB_YESNOCANCEL | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST
            answer = ctypes.windll.user32.MessageBoxW(
                0, text, title, 0x03 | 0x20 | 0x10000 | 0x40000)
            return {6: True, 7: False}.get(answer)      # IDYES, IDNO
        except Exception:
            pass

    def build(root, tkinter):
        from tkinter import messagebox
        return messagebox.askyesnocancel(title, text, parent=root)

    return _tk_in_its_own_thread(build)


def ask_text(title: str, prompt: str, initial: str) -> str:
    """Ask for a line of text. Tk, but on a thread that owns it."""
    def build(root, tkinter):
        from tkinter import simpledialog
        return simpledialog.askstring(title, prompt, initialvalue=initial,
                                      parent=root)

    answer = _tk_in_its_own_thread(build)
    if answer is None:
        return ""
    return answer


def wait_then_open(timeout: float = 25.0) -> int:
    """Wait for the encoder to answer, then open it in the browser.

    Run by the installer as its last step. The encoder has only just been
    started and takes a few seconds to bind, so opening the browser at once
    would land on a refused connection - which looks exactly like a failed
    install.
    """
    settings = read_settings()
    url = local_url(settings)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if encoder_status(settings)["up"]:
            webbrowser.open(url)
            print(f"opened {url}")
            return 0
        time.sleep(1.0)
        settings = read_settings()
    # Open it anyway: a browser showing a connection error at the right address
    # tells the operator more than nothing happening at all.
    webbrowser.open(url)
    print(f"{url} did not answer within {timeout:.0f}s; opened it regardless")
    return 0


def main() -> int:
    if "--open" in sys.argv:
        return wait_then_open()
    if "--start" in sys.argv:
        code, output = service_command("start")
        print(output)
        return code
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        print("The tray icon needs pystray and Pillow:")
        print("    pip install pystray pillow")
        return 1
    Tray().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
