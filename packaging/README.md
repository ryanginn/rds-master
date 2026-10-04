# Building RDS Master as an installable application

```bash
pip install -r packaging/build-requirements.txt
python packaging/build.py
```

That freezes the encoder and the tray icon with PyInstaller and wraps the
result in whatever this machine's installer format is. The output lands in
`dist/installers/`.

**These cannot be cross compiled.** PyInstaller bundles the Python and the
native libraries of the machine it runs on, so a `.deb` has to be built on
Debian and a Windows installer on Windows. Building the wrong target still
works and still produces a file — it just will not run anywhere.

| Target | Built with | Produces |
|---|---|---|
| `--target deb` | `dpkg-deb`, or assembled directly when it is absent | `rds-master_<version>_amd64.deb` |
| `--target win` | Inno Setup 6 (`ISCC`), or a zip without it | `rds-master-<version>-setup.exe` |
| `--target mac` | `pkgbuild` | `rds-master-<version>.pkg` |

## The thing to understand first: services and soundcards

The encoder's job is pushing MPX out of a soundcard, and that is exactly what a
system service is bad at. Audio belongs to a logged-in session:

- **Windows.** MME, DirectSound and WASAPI all go through the per-session audio
  engine, which does not exist in session 0. A service cannot open them.
  **Kernel streaming (WDM-KS) does work**, because it talks to the driver
  underneath — which is how StereoTool and BreakawayOne run as services.
- **Debian.** A system unit cannot reach PipeWire or PulseAudio, which are
  per-user. ALSA devices (`hw:0,0`) work fine.
- **macOS.** A LaunchDaemon has no CoreAudio session at all, and macOS has no
  equivalent of systemd's lingering. An agent plus automatic login is the only
  arrangement that works.

So each installer offers two ways to start it:

**Session mode** — runs in a logged-in session, so every host API works. On
Windows that is a scheduled task at logon; on Debian a `systemd --user` service,
which with `loginctl enable-linger` starts at boot **without** anyone logging
in; on macOS a LaunchAgent.

**Service mode** — starts before anyone logs in. Needs a kernel-level device:
WDM-KS on Windows, an ALSA `hw:` device on Debian. Not available on macOS.

If the encoder starts but the carrier never appears, this is almost always why.
The log says `Error opening OutputStream` and the tray flags it.

## Where things go

| | Program files | Configuration and data |
|---|---|---|
| Windows | `C:\Program Files\RDS Master\` | `C:\ProgramData\RDS Master\` |
| Debian | `/opt/rds-master/` | `/var/lib/rds-master/` or `~/.config/rds-master/` |
| macOS | `/Applications/RDS Master.app` | `~/Library/Application Support/RDS Master/` |

`datasets.json` holds the station's profiles, the web login and the Flask secret
key, so it is kept out of the program directory: a service account cannot write
there and an upgrade would overwrite it. `RDS_DATA_DIR` overrides the choice and
is what each service unit sets.

**An upgrade never overwrites an existing configuration.** It is left where it
is and a dated copy is made beside it.

## The tray icon

A separate small process, not the encoder — quitting it never takes a station
off air. It shows whether the encoder is answering, opens the web interface,
starts and stops whichever kind of service is installed, and has the two
settings you need when the web interface is the thing you cannot reach:

- **Network settings** — the address to listen on and the port.
- **Where can I reach it?** — every address this machine answers on, and whether
  the port is actually open.

## Signing

Nothing here is signed, so Windows shows a SmartScreen warning and macOS refuses
the package until the user right-clicks and chooses Open. Signing needs
certificates you have to buy:

```bash
signtool sign /fd sha256 /a dist/installers/rds-master-1.5-setup.exe
productsign --sign "Developer ID Installer: ..." in.pkg out.pkg
xcrun notarytool submit out.pkg --apple-id ... --wait
```

## What has actually been tested

| | |
|---|---|
| PyInstaller build | **built and run** — the frozen encoder serves, writes its config to the data directory, and its UECP listener accepts frames |
| Icon generation | **run** — `.ico`, `.icns` and nine PNGs from `rdsm-icon.svg` |
| Tray logic | **run headlessly** — settings read and written, addresses discovered, port checked, service detection. The icon itself has not been clicked in a desktop session |
| `.deb` assembly | **built and validated** — structure, control fields, maintainer script permissions, installed paths |
| `.deb` installed | **not done** — needs a Debian machine |
| Inno Setup installer | **not compiled** — Inno Setup is not installed here |
| Windows service | **not installed** — needs pywin32 and an administrator |
| macOS, all of it | **never run** — written from the documentation |

`tests/test_packaging.py` checks the parts that quietly rot: a unit pointing at
a path the package does not install, an installer naming a file that is no
longer built, a missing icon size, and the structure of any `.deb` that has been
built.
