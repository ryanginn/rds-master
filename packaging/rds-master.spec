# PyInstaller spec for RDS Master: the encoder and its tray icon.
#
# Two executables in one folder, so the tray can find the icons and both share
# the bundled Python and libraries. Built per platform - this cannot be cross
# compiled; run it on the machine the installer is for.
#
#     pyinstaller packaging/rds-master.spec --noconfirm
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
PACKAGING = os.path.join(ROOT, "packaging")
ICONS = os.path.join(PACKAGING, "icons")

# The icons the tray loads at runtime, and the uecp package, which app.py
# imports inside functions rather than at the top so PyInstaller cannot see it.
datas = [(ICONS, "icons")]
hiddenimports = [
    "uecp", "uecp.frame", "uecp.mec", "uecp.element", "uecp.store",
    "uecp.handler", "uecp.bridge", "uecp.transport",
    "rds_charset",
    # Flask-SocketIO picks its async driver at runtime.
    "engineio.async_drivers.threading",
    # Optional at runtime, bundled so the features work if the hardware is there.
    "serial", "serial.tools.list_ports", "psutil", "websocket",
]

# updater.py is optional - app.py guards the import - so only bundle a copy
# that is actually present.
if os.path.exists(os.path.join(ROOT, "updater.py")):
    hiddenimports.append("updater")

excludes = [
    # Nothing here draws plots or trains anything, and these are enormous.
    "matplotlib", "tkinter.test", "test", "pytest", "IPython", "notebook",
    "PyQt5", "PyQt6", "PySide2", "PySide6",
]

encoder = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

tray = Analysis(
    [os.path.join(PACKAGING, "tray", "rdsm_tray.py")],
    pathex=[ROOT, PACKAGING],
    binaries=[],
    datas=[(ICONS, "icons")],
    hiddenimports=["pystray", "PIL", "PIL.Image"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes + ["numpy", "scipy", "flask", "flask_socketio"],
    noarchive=False,
)

# A third executable on Windows: the service wrapper. It needs pywin32, which
# is only a build-time requirement for service mode - without it the installer
# still offers session mode, which is the one that works with any soundcard.
service = None
if sys.platform == "win32":
    try:
        import win32serviceutil  # noqa: F401
        service = Analysis(
            [os.path.join(PACKAGING, "windows", "rds_service.py")],
            pathex=[ROOT, PACKAGING],
            binaries=[],
            datas=datas,
            hiddenimports=hiddenimports + ["win32timezone", "servicemanager",
                                           "win32service", "win32event"],
            hookspath=[],
            runtime_hooks=[],
            excludes=excludes,
            noarchive=False,
        )
    except ImportError:
        print("pywin32 is not installed: building without the service wrapper. "
              "Session mode will still work.")

parts = [(encoder, "rds-master", "rds-master"),
         (tray, "rds-master-tray", "rds-master-tray")]
if service is not None:
    parts.append((service, "rds-master-service", "rds-master-service"))
MERGE(*parts)

encoder_pyz = PYZ(encoder.pure)
tray_pyz = PYZ(tray.pure)

windows_icon = os.path.join(ICONS, "rdsm.ico")
icon = windows_icon if os.path.exists(windows_icon) else None

encoder_exe = EXE(
    encoder_pyz, encoder.scripts, [],
    exclude_binaries=True,
    name="rds-master",
    debug=False,
    strip=False,
    upx=False,
    # The encoder writes its startup messages and any fault to the console,
    # which is where a service's log picks them up.
    console=True,
    icon=icon,
)

tray_exe = EXE(
    tray_pyz, tray.scripts, [],
    exclude_binaries=True,
    name="rds-master-tray",
    debug=False,
    strip=False,
    upx=False,
    # No console: this one lives in the notification area.
    console=False,
    icon=icon,
)

collected = [encoder_exe, encoder.binaries, encoder.datas,
             tray_exe, tray.binaries, tray.datas]

if service is not None:
    service_exe = EXE(
        PYZ(service.pure), service.scripts, [],
        exclude_binaries=True,
        name="rds-master-service",
        debug=False,
        strip=False,
        upx=False,
        console=True,
        icon=icon,
    )
    collected = [service_exe, service.binaries, service.datas] + collected

COLLECT(
    *collected,
    strip=False,
    upx=False,
    name="rds-master",
)
