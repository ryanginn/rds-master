

# RDS Master (Python)

<img width="1920" height="951" alt="image" src="https://github.com/user-attachments/assets/a88a381b-e5b6-4df9-8fc7-5310dce3fc7f" />


A work-in-progress open-source webUI based RDS encoder. Ships with a lightweight Flask + Socket.IO server, Tailwind-styled UI, and session-gated access for secure usage.

Many thanks to **Bkram, Hans van Eijsden, RZCH, Wötkylä, notluca, Dido, Hyper DX, almyyyzzz and Adam W** (not in order) for Testing, ideas and assistance in the development of this program! ❤️

  

## Current Features

- Web UI with tabs for Dashboard, Basic RDS, Experts settings, RDS2, Datasets, and Settings.

- Config-driven login (credentials stored in `config.ini` under `[AUTH]`).

- Auto-start option (enabled by default) to bring the encoder on-air when the moment the program launches; toggle in Settings.

- Device selection for input/output (only MME devices on Windows at the moment) plus pass-through and genlock controls (uses 19kHz carrier for RDS carrier phasing).

- Dynamic PS/RT editing with simple timed sequencing and centering options.

- RT+ formatting with visual builder and support for up to 2 tags per RT message.

- Long PS (32 chars), Enhanced Radiotext (128 bytes UTF-8) and eRT+, PTYN, CT, AF Method A & B controls; DAB cross-reference (12A).

- Datasets for preset data

- Basic remote control of PTY/PTYN/M-S/TP-TA via JSON

- UECP input

- Enhanced Other Networks (EON) Group 14A support with PS/AF/Mapped AF/PTY transmission.

- FM-DAB linking via ODA

- Custom RDS group input from RDS Spy recording / Airomate-standard txt file 

- Live monitor panel that reflects PS/RT/PI/PTY and pilot status via WebSocket.

- Basic RDS2 logos (experimental)

## Installation

1. Install Python 3.10+ and the PortAudio-compatible drivers for your audio hardware. (```sudo apt install libportaudio2 portaudio19-dev```)

2. Install dependencies:

```bash

pip install flask flask-socketio sounddevice numpy scipy

```

  

## Usage

1. Start the app:

```bash

python app.py

```

2. Browse to `http://localhost:5000` and log in with the credentials set in `datasets.json` ( defaults are`admin` / `pass`).

3. In **Settings**, adjust:

- Auto-start on launch (default on).

- Username/password (leave password blank to keep the current one).

4. Select audio devices in **Audio & MPX**, set RDS fields in **Basic/Expert**, then toggle **ON AIR**.

5. Config is persisted back to `datasets.json` on changes or when stopping the encoder.

## Limits and Roadmap

- EON (Enhanced Other Networks) Group 14B TA not implemented. ⚠️

- No packaged EXE release yet

- Support for UECP output (planned)

- Support for MRDS1322 RDS encoder chip (planned)

## Development Status

Active development; breaking changes are possible. Avoid production deployment until a stable release is published. Feedback, contributions and issue reports are welcome.

Déanta in Éirinn 💖🇮🇪
