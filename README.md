
# RDS Master RDS Encoder

  ![Overview of the User Interface](https://github.com/user-attachments/assets/68e1d484-b81f-4fe7-bedf-2c62e7de98f4)


A work-in-progress open-source webUI based RDS encoder. Ships with a lightweight Flask + Socket.IO server, Tailwind-styled UI, and session-gated access for secure usage.

  

## Current Features

- Web UI with tabs for Dashboard, Basic RDS, Expert, Audio, and Settings.

- Config-driven login (credentials stored in `datasets.json` under `auth` - **gitignored**,
  since it also holds the Flask secret key). `datasets.example.json` is the template.

- **Profiles.** Each profile is a complete saved configuration and is one of two kinds,
  chosen when you create it:

  - **Internal RDS coder** - everything is configured in this UI (the original behaviour).
  - **UECP input** - RDS data arrives from an external UECP source over TCP or WebSocket.
    The UI monitors it; it does not edit it.

  The menus follow the live profile's kind, so a UECP profile hides the internal-coder tabs
  and shows a UECP Monitor instead. Switching profile switches the whole encoder, which makes
  failover scriptable - `POST /profiles/<n>/switch` (`/datasets/...` still works as an alias),
  so a watchdog can drop to a local internal-coder profile if the studio feed dies.

- Auto-start option (enabled by default) to bring the encoder on-air when the moment the program launches; toggle in Settings.

- Device selection for input/output plus pass-through and genlock controls (uses 19kHz carrier
  for RDS carrier phasing). Windows lists MME by default; **Show all available APIs** in the
  Audio tab adds WASAPI, DirectSound and WDM-KS. Only devices that accept the encoder's
  192 kHz sample rate are offered. The toggle is hidden on macOS and Linux, which never filter.

- Dynamic PS/RT editing with simple timed sequencing and centering options.

- Date/time placeholders in PS, Long PS, RT, eRT and PTYN:

| Token | Meaning | Example |
| --- | --- | --- |
| `\HR\` | Hour, 00-23 | `09` |
| `\H12\` | Hour, 01-12 | `09` |
| `\AP\` | AM / PM | `AM` |
| `\MN\` | **Minute**, 00-59 | `05` |
| `\S\` | Second, 00-59 | `07` |
| `\DD\` | Day of month, 01-31 | `04` |
| `\MO\` | **Month**, 01-12 | `02` |
| `\YYYY\` | Year, 4-digit | `2026` |
| `\YY\` | Year, 2-digit | `26` |
| `\MMM\` / `\MMMM\` | Month name | `Feb` / `February` |
| `\DDD\` / `\DDDD\` | Weekday name | `Wed` / `Wednesday` |

  So `\DD\ \MO\ \YYYY\` gives `04 02 2026` and `\DD\/\MO\/\YY\` gives `04/02/26`.
  Note `\MN\` is the **minute** (unchanged from earlier versions) — the **month** is `\MO\`.

- RT+ formatting with visual builder and support for up to 2 tags per RT message.

- RT+ "Between characters" tagging policy for text that isn't delimiter-separated —
  tags what sits between a pair of characters plus whatever is left over. e.g.
  `"Turnstyle" Dermot Kennedy` with open/close `"` tags *Turnstyle* as Title and
  *Dermot Kennedy* as Artist. Works with quotes, smart quotes, `(...)`, `[...]` or any
  character pair, on both RT+ and eRT+.

- Long PS (32 chars), PTYN, CT, AF Method A & B controls; DAB cross-reference (12A) (experimental).

- Fast tuning (Group 15B) — off by default, toggled in **Flags & Switches**. Repeats TP, PTY,
  TA, M/S and the DI bits with no text payload so receivers acquire faster than waiting for the
  0A/0B rotation. Blocks 2 and 4 are identical, block 3 repeats the PI code, and the DI bits are
  addressed by C1/C0 (00→d3, 01→d2, 10→d1, 11→d0) exactly as in group 0. Rate is selectable
  from 1 to 8 groups per scheduler cycle, and switching **TA on fires a burst of 8 15B groups**
  ahead of the schedule so receivers pick up the traffic announcement immediately.

- Enhanced Other Networks (EON) Group 14A support with PS/AF/PTY transmission (experimental).

- **Group 14B** — EON traffic-announcement switching. Sent automatically whenever EON is in
  use (it is not optional), one group per configured service each cycle. Block 2 carries
  TP(ON) in b4 and TA(ON) in b3 (b2-b0 unused), block 3 repeats this station's PI and block 4
  carries PI(ON). Each EON service now has a **TA (ON)** tickbox beside TP (ON); changing it
  fires a burst of 8 14B groups on **both** edges, so receivers switch to the other network
  when an announcement starts and back again when it ends.

- **PIN(ON)** — Group 14A variant 14 carries each EON service's Programme Item Number, packed
  as `[day:5][hour:5][minute:6]` exactly like the station's own PIN in group 1A. Set it per
  service in the EON editor.

- **Dynamic Control for EON** — each configured service appears in the Dynamic Control
  parameter list as `TA (ON)` and `PIN (ON)`, so a JSON feed can raise a traffic announcement
  or set a programme item on one other-network station at a time. Rules are keyed by PI(ON)
  rather than list position, so reordering or editing services never repoints a rule at the
  wrong station. Add a service and reload the page to see it in the list.

- **UECP** (Universal Encoder Communication Protocol) input via TCP or WebSocket, implemented
  against RDS Forum SPB 490 v7.05. The `uecp/` package is self-contained and independently
  testable:

  | | |
  |---|---|
  | `frame.py` | records, byte stuffing, CRC-16/CCITT, site/encoder addressing, stream reassembly |
  | `mec.py` | all 68 message commands, with the DSN/PSN/MEL layout taken from the spec |
  | `element.py` | message-field walking and the DSN/PSN targeting rules |
  | `store.py` | data sets, programme services, raw-group queues, slow labelling codes, clock |
  | `handler.py` | applying message elements to that store |
  | `bridge.py` | putting the live data set on air |
  | `transport.py` | TCP listener and reconnecting WebSocket client |

  `rds_charset.py` at the project root holds the IEC 62106-4 character table, shared
  with `app.py` so there is only one copy of it.

  Data sets (DSN) and programme services (PSN) live in a proper tree rather than being
  flattened onto one PS/RT. Per MEC `0x28`, one PSN in a data set is the **main service** -
  that is what goes to air - and the others are **other networks**, which become EON services
  in groups 14A/14B. `0x1C` (Data set select) chooses which set is live.

  **All 68 commands are handled** - nothing is reported as unimplemented.

  - **Text goes to air byte for byte.** PS, RadioText and PTYN arriving over UECP are
    already in the RDS character set, including the source's own `0x0D` terminator, so
    they are transmitted exactly as received. Nothing is decoded and re-encoded, nothing
    is centred and no terminator of ours is appended. The decoded text in the UI is for
    reading only.

    RadioText transmission stops after the segment holding the source's `0x0D`, and
    only that segment is space-filled - the segments beyond it are not sent, exactly as
    the internal coder does it. The A/B flag changes whenever the text does, which is
    what tells a receiver to clear its buffer and start a new message rather than merge
    it into the last one; taking the flag straight from the source's toggle bit was not
    enough once several messages were queued.

  - **TMC (MEC `30`) is transmitted**, not just decoded. Each 37-bit Alert-C message set
    is queued as a complete type 8A group with the number of transmissions the source
    asked for, cyclic buffers are repeated, buffer configuration `11` clears the buffer,
    and an "extremely urgent" set jumps the queue. If the source has announced a
    different group for AID `CD46`, TMC follows it there.
  - **Free-format groups (`24`) and ODA data (`40`/`42`/`46`)** are queued the same way.
    This is how RT+ arrives from most sources. An ODA table sent as raw 3A groups is read
    back, so the monitor shows which AID is on which group.
  - Raw groups are taken off the queue by the generator, one per scheduled slot, and a
    queue that is backing up is given more slots in the sequence. A one-shot group that
    has waited more than 20 seconds is dropped rather than transmitted late - RT+ tags
    pointing into a song that has already finished are worse than no tags.
  - Transparent data (`25`/`26`/`2B`) and paging (`08`/`0C`/`10`/`11`/`1B`/`20`/`12`,
    EPP `31`-`35`) are decoded and shown in the monitor; paging is not put on air, as
    that needs a group 7A paging encoder.

  Addressing follows section 2.3 of the spec: a command with both DSN and PSN applies to
  that service, one with only a DSN to every service in that data set, and one with
  neither to every data set. DSN `254` is every data set except the current one and `255`
  is all of them; PSN `0` is the main service, not a wildcard.

  In UECP mode the group sequence is always explicit, and it belongs to a data set - each
  DSN has its own, whether it came from the source (MEC `16`) or was set by hand, and
  switching data set switches the sequence with it. Selecting a data set in the monitor
  tree opens its own settings, so any DSN's sequence can be set, not only the one on air;
  a typo is refused with a message rather than quietly going out. The internal coder's
  optional groups are forced off, so the encoder never transmits 4A/6A/10A/15A content a
  UECP source never asked for, and group 1A carries an ECC or LIC only once the matching
  slow labelling variant has arrived - never the built-in E3/09.

  **Clock Time** is a setting for the whole encoder, not for a data set, and sits under the
  group sequence in the UECP menu. By default 4A goes out only once the source has sent a
  time (MEC `0D`), because the encoder's clock is not the source's. Set it to *from this
  encoder's clock* and it behaves exactly like the internal coder - 4A on the minute from
  this machine's time, daylight saving included, plus a UTC trim - which covers a source
  that sends no clock at all. *Never send CT* is there for a source sending a wrong one.
  4A takes no slot in the group sequence either way: it pre-empts the schedule on the
  minute, since a 4A sent at any other moment would set receiver clocks wrong.

  **In-House (group 6A)** sits beside Clock Time and is the internal coder's own in-house
  data, with the same first-start date, station identifier, frequency and site code behind
  it. It belongs to the transmitter site rather than to a programme service, so like CT it
  applies to every data set, and a UECP source has no way to send it.

  **Programme services can be entered here**, per data set, for the PSNs a source does not
  describe itself - which is most sources, since plenty send only one service. Right-click a
  data set in the monitor tree for *Add PSN*, *Set as Active DSN*, *Copy* and *Paste as new*;
  right-click a service for *Delete* and *Copy*. Clicking a service opens it in a form with
  three tabs, as a coder's own configuration tool does:

  - **General** - PI, PS, PTY, PTYN, TP/TA, Linkage and PIN, and what the service is:
    **EON**, an other network carried in groups 14A and 14B, or **Main**, the station
    itself. Main is only offered while the service is not an other network, since it cannot
    be both.
  - **AF List** - the main service's own alternative frequencies, in group 0A.
  - **EON-AF List** - an other network's frequencies: a plain list as 14A variant 4, and
    mapped pairs as variants 5 to 8.

  Each tab is only offered where it means something, so AF List is greyed for an other
  network and EON-AF List for the main service.

  **Linkage** (LA, EG, ILS and the Linkage Set Number) is transmitted: the whole word as
  group 14A **variant 12**, and the Linkage Actuator also as bit 15 of block 3 in group 1A,
  which is where a receiver looks for it on the station itself.

  This is the structure MEC `28` describes - one PSN of a data set is the main service and
  the others are its other networks - filled in by hand instead of over the wire. Anything
  the source does describe wins over a copy typed here, so connecting a source that sends
  its own EON does not leave duplicates on air. A service with no PI is refused rather than
  transmitted as `0000`.

  **Several connections at once**, the way a hardware coder takes a couple of TCP ports and
  a serial link together. Each is named, is TCP or WebSocket, and can be switched off; a TCP
  port of 0 deactivates it, as on a 2wcom. The monitor shows each one's state, its connected
  clients and any error.

  **MEC access rights** decide which commands a source may use, the way a 2wcom, Audemat or
  Deva encoder does - and they are set **per connection**, so a studio link can be allowed to
  set RadioText while a remote one cannot rewrite the PI or the AF list. A connection with no
  rights of its own follows the encoder-wide default. A command that is not allowed is
  counted and logged with the connection that sent it, rather than acted on, so it is clear
  from the monitor why a source's data is not appearing.

  The monitor lists every data set and programme service, used or not, so the structure is
  visible before any data arrives, and shows the other networks (EON), the ODA table and
  what is still queued.

  WebSocket sources may send each record as binary or as base64 text; both are handled.

- Headless mode support (run without audio devices when using UECP input).

- Live monitor panel that reflects PS/RT/PI/PTY and pilot status via WebSocket, including
  **CARRIER** and **RT SRC** health lamps that go red when the audio device is not open or a
  RadioText feed is failing.

- **Stays on air through outages.** File/URL/JSON RadioText is fetched by a background thread,
  never from the audio callback, so a stalled feed cannot overrun the real-time deadline. The
  last text that arrived keeps transmitting until new data comes in. The audio stream reopens
  automatically if the soundcard drops out, re-finding the device by name if the system
  renumbers it.

  

## Installation

1. Install Python 3.10+ and the PortAudio-compatible drivers for your audio hardware.

2. Install dependencies:

```bash

pip install -r requirements.txt

```

   `pyserial` and `psutil` in there are optional - without them the serial output and
   the CPU/memory readout are simply not offered. `websocket-client` is needed only for
   UECP over WebSocket; the TCP listener works without it.

  

## Usage

1. Start the app:

```bash

python app.py

```

2. Browse to `http://localhost:5000` and log in. Credentials live in `datasets.json`
   under `auth`; the defaults are `admin` / `pass`. Copy `datasets.example.json` to
   `datasets.json` to start from a template, and change the password before putting it
   anywhere reachable.

   `datasets.json` also holds the Flask secret key, which is why it is gitignored. Set
   `RDS_DATASETS_FILE` to run against a different configuration file.

3. In **Settings**, adjust:

- Auto-start on launch (default on).

- Username/password (leave password blank to keep the current one).

4. Select audio devices in **Audio & MPX**, set RDS fields in **Basic/Expert**, then toggle **ON AIR**.

5. Configuration is written back to `datasets.json` on changes or when stopping the
   encoder.

  

## Limits and Roadmap

- Not production-ready; use only for lab/bench testing until a formal release.

- RT+ tagging with visual builder supporting 2 tags per message. ✅

- EON (Enhanced Other Networks) Group 14A implemented but **experimental** - use with caution and verify on-air behavior. ⚠️

- AF Method B with regional variant (RV) flag support. ✅

- DAB cross-reference (Group 12A) is experimental and unverified on-air. ⚠️

- Extended ASCII character support (ISO-8859-1/Latin-1 encoding). ✅

- Datasets Mode (Group 5A) for transparent data channel. ✅

- No packaged EXE release yet; restart-on-crash not managed.

- Support for RBDS variant. ✅

- UECP (Universal Encoder Communication Protocol) input via TCP/WebSocket (SPB490/EN 50067 Annex E). ✅

- TMC (Alert-C) transmitted from a UECP source over MEC 30. ✅

- Support for MRDS1322 RDS encoder chip (planned)

## Development Status

Active development; breaking changes are possible. Avoid production deployment until a stable release is published. Feedback, contributions and issue reports are welcome.

## Licence

GNU General Public License v3.0 or later - see [LICENSE](LICENSE). The header of
`app.py` carries the same notice.

Déanta in Éirinn 💖🇮🇪
