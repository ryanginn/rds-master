"""Build an installable RDS Master for this machine's platform.

    python packaging/build.py              # whatever this machine is
    python packaging/build.py --target deb
    python packaging/build.py --skip-freeze # repackage without rebuilding

These cannot be cross compiled: PyInstaller bundles this machine's Python and
its native libraries, so a Debian package has to be built on Debian and a
Windows installer on Windows. The Debian package itself is assembled here in
Python rather than with dpkg-deb, so a .deb can at least be produced from a CI
runner that has not got the Debian tools.
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import shutil
import subprocess
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(ROOT, "dist", "installers")
FROZEN = os.path.join(DIST, "rds-master")

PACKAGE = "rds-master"
MAINTAINER = "FMDX.ie <rds@fmdx.ie>"
HOMEPAGE = "https://github.com/fmdx-ie/rds-master"


def version() -> str:
    """The version app.py reports, so the package matches what is installed."""
    import re
    source = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read(40000)
    found = re.search(r'^VERSION\s*=\s*["\']v?([^"\']+)["\']', source, re.M)
    return found.group(1) if found else "0.0.0"


def say(text: str) -> None:
    print(f"  {text}", flush=True)


# ---------------------------------------------------------------------------
# Step one: freeze
# ---------------------------------------------------------------------------

def freeze() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller is not installed:  pip install pyinstaller")
    icons = os.path.join(HERE, "icons", "rdsm.ico")
    if not os.path.exists(icons):
        say("icons are missing, generating them first")
        subprocess.run([sys.executable, os.path.join(HERE, "make_icons.py")],
                       check=True)
    say("running PyInstaller (this takes a few minutes)")
    subprocess.run([sys.executable, "-m", "PyInstaller",
                    os.path.join(HERE, "rds-master.spec"),
                    "--noconfirm",
                    "--distpath", DIST,
                    "--workpath", os.path.join(ROOT, "build")],
                   check=True, cwd=ROOT)
    if not os.path.isdir(FROZEN):
        raise SystemExit(f"PyInstaller did not produce {FROZEN}")
    say(f"frozen build is in {FROZEN}")


# ---------------------------------------------------------------------------
# Debian
# ---------------------------------------------------------------------------

def _tar_add_tree(tar: tarfile.TarFile, source: str, prefix: str, mode=None):
    for folder, _dirs, files in os.walk(source):
        for name in sorted(files):
            full = os.path.join(folder, name)
            rel = os.path.relpath(full, source).replace(os.sep, "/")
            info = tar.gettarinfo(full, f"{prefix}/{rel}")
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if mode is not None:
                info.mode = mode
            elif os.access(full, os.X_OK) or name.startswith("rds-master"):
                info.mode = 0o755
            else:
                info.mode = 0o644
            with open(full, "rb") as handle:
                tar.addfile(info, handle)


def _tar_add_bytes(tar: tarfile.TarFile, path: str, data: bytes, mode=0o644):
    info = tarfile.TarInfo(path)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(time.time())
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


def build_deb() -> str:
    """Assemble a .deb. Uses dpkg-deb when it is there, builds it by hand if not."""
    ver = version()
    arch = {"x86_64": "amd64", "AMD64": "amd64",
            "aarch64": "arm64", "armv7l": "armhf"}.get(os.uname().machine
                                                       if hasattr(os, "uname")
                                                       else "x86_64", "amd64")
    staging = os.path.join(DIST, "deb-root")
    shutil.rmtree(staging, ignore_errors=True)

    opt = os.path.join(staging, "opt", "rds-master")
    os.makedirs(opt)
    say("copying the frozen build into /opt/rds-master")
    shutil.copytree(FROZEN, opt, dirs_exist_ok=True)

    # systemd units, the desktop entry and the icons
    units = os.path.join(staging, "lib", "systemd", "system")
    os.makedirs(units)
    shutil.copy(os.path.join(HERE, "debian", "rds-master.service"),
                os.path.join(units, "rds-master.service"))
    user_units = os.path.join(staging, "usr", "lib", "systemd", "user")
    os.makedirs(user_units)
    shutil.copy(os.path.join(HERE, "debian", "rds-master-user.service"),
                os.path.join(user_units, "rds-master.service"))

    apps = os.path.join(staging, "usr", "share", "applications")
    os.makedirs(apps)
    shutil.copy(os.path.join(HERE, "debian", "rds-master-tray.desktop"),
                os.path.join(apps, "rds-master-tray.desktop"))

    for size in (16, 22, 24, 32, 48, 64, 128, 256, 512):
        source = os.path.join(HERE, "icons", f"rdsm-{size}.png")
        if not os.path.exists(source):
            continue
        target = os.path.join(staging, "usr", "share", "icons", "hicolor",
                              f"{size}x{size}", "apps")
        os.makedirs(target, exist_ok=True)
        shutil.copy(source, os.path.join(target, "rds-master.png"))

    # Symlinks would be neater, but a plain shim works on every filesystem.
    binaries = os.path.join(staging, "usr", "bin")
    os.makedirs(binaries)
    for name in ("rds-master", "rds-master-tray"):
        with open(os.path.join(binaries, name), "w", newline="\n") as handle:
            handle.write(f'#!/bin/sh\nexec /opt/rds-master/{name} "$@"\n')
        os.chmod(os.path.join(binaries, name), 0o755)

    installed_kb = sum(
        os.path.getsize(os.path.join(f, n))
        for f, _d, ns in os.walk(staging) for n in ns) // 1024

    control = (
        f"Package: {PACKAGE}\n"
        f"Version: {ver}\n"
        f"Section: sound\n"
        f"Priority: optional\n"
        f"Architecture: {arch}\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Installed-Size: {installed_kb}\n"
        f"Depends: libasound2, libportaudio2\n"
        f"Recommends: pipewire | pulseaudio\n"
        f"Homepage: {HOMEPAGE}\n"
        f"Description: RDS and RBDS encoder with a web interface\n"
        f" Generates the RDS subcarrier and its groups, with a browser based\n"
        f" interface, RT+ tagging, EON, and UECP input over TCP or WebSocket.\n"
        f" Runs machine-wide for ALSA devices, or in a user session where the\n"
        f" soundcard is reached through PipeWire or PulseAudio.\n")

    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"{PACKAGE}_{ver}_{arch}.deb")

    if shutil.which("dpkg-deb"):
        debian = os.path.join(staging, "DEBIAN")
        os.makedirs(debian)
        open(os.path.join(debian, "control"), "w", newline="\n").write(control)
        for script in ("postinst", "prerm", "postrm"):
            shutil.copy(os.path.join(HERE, "debian", script),
                        os.path.join(debian, script))
            os.chmod(os.path.join(debian, script), 0o755)
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                        staging, out], check=True)
        say("built with dpkg-deb")
        return out

    say("dpkg-deb is not here, assembling the archive directly")
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        for top in ("opt", "usr", "lib"):
            full = os.path.join(staging, top)
            if os.path.isdir(full):
                _tar_add_tree(tar, full, f"./{top}")

    meta = io.BytesIO()
    with tarfile.open(fileobj=meta, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        _tar_add_bytes(tar, "./control", control.encode())
        for script in ("postinst", "prerm", "postrm"):
            body = open(os.path.join(HERE, "debian", script),
                        encoding="utf-8").read().replace("\r\n", "\n")
            _tar_add_bytes(tar, f"./{script}", body.encode(), mode=0o755)

    def ar_member(name: str, payload: bytes) -> bytes:
        header = (f"{name:<16}{int(time.time()):<12}{0:<6}{0:<6}"
                  f"{'100644':<8}{len(payload):<10}`\n").encode()
        return header + payload + (b"\n" if len(payload) % 2 else b"")

    with open(out, "wb") as handle:
        handle.write(b"!<arch>\n")
        handle.write(ar_member("debian-binary", b"2.0\n"))
        handle.write(ar_member("control.tar.gz", meta.getvalue()))
        handle.write(ar_member("data.tar.gz", data.getvalue()))
    return out


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

def find_inno() -> str:
    """The Inno Setup compiler, or '' if it is not installed.

    It can land in three places: on PATH, under Program Files for a machine-wide
    install, or in the user's AppData when installed without administrator
    rights - which is what winget does by default.
    """
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    candidates = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
                 os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs")):
        if not base:
            continue
        for version in ("Inno Setup 6", "Inno Setup 5", "Inno Setup"):
            candidates.append(os.path.join(base, version, "ISCC.exe"))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return ""


def build_windows() -> str:
    ver = version()
    os.makedirs(OUT, exist_ok=True)
    iss = os.path.join(HERE, "windows", "rds-master.iss")
    compiler = find_inno()
    if compiler:
        say("compiling the installer with Inno Setup")
        subprocess.run([compiler, iss,
                        f"/DAppVersion={ver}",
                        f"/DSourceDir={FROZEN}",
                        f"/DOutputDir={OUT}"], check=True)
        return os.path.join(OUT, f"rds-master-{ver}-setup.exe")

    say("Inno Setup is not installed, falling back to a zip")
    say("  get it from https://jrsoftware.org/isdl.php to build the installer")
    archive = os.path.join(OUT, f"rds-master-{ver}-windows")
    return shutil.make_archive(archive, "zip", FROZEN)


# ---------------------------------------------------------------------------
# macOS - written from the documentation, never run
# ---------------------------------------------------------------------------

def build_macos() -> str:
    ver = version()
    os.makedirs(OUT, exist_ok=True)
    say("NOTE: the macOS path has never been run. Treat it as a starting point.")
    script = os.path.join(HERE, "macos", "build_pkg.sh")
    if os.path.exists(script) and sys.platform == "darwin":
        subprocess.run(["bash", script, ver, FROZEN, OUT], check=True)
        return os.path.join(OUT, f"rds-master-{ver}.pkg")
    archive = os.path.join(OUT, f"rds-master-{ver}-macos")
    return shutil.make_archive(archive, "zip", FROZEN)


# ---------------------------------------------------------------------------

TARGETS = {"deb": build_deb, "win": build_windows, "mac": build_macos}


def default_target() -> str:
    return {"win32": "win", "darwin": "mac"}.get(sys.platform, "deb")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS),
                        default=default_target())
    parser.add_argument("--skip-freeze", action="store_true",
                        help="package an existing dist/rds-master")
    args = parser.parse_args()

    print(f"RDS Master {version()} -> {args.target}")
    if args.skip_freeze:
        if not os.path.isdir(FROZEN):
            raise SystemExit(f"{FROZEN} is not there; run without --skip-freeze")
        say("reusing the existing frozen build")
    else:
        freeze()

    if args.target != default_target():
        say(f"WARNING: building a {args.target} package on {sys.platform}.")
        say("         PyInstaller bundles this machine's libraries, so the")
        say("         result will only run on the platform it was built on.")

    result = TARGETS[args.target]()
    size = os.path.getsize(result) / (1024 * 1024)
    print(f"\n{result}  ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
