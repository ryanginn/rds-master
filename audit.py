"""The audit trail: who changed what, when, and from where.

Every event is one JSON object on its own line. That format was chosen because
it survives a power cut mid-write - a truncated last line is skipped and the
rest of the history is still readable, which a single JSON array would not be.

Values that would be worth stealing are never written. Passwords and the Flask
secret key are recorded as a change having happened, not as the value.

Nothing here may block the audio path, so writes are short, appended, and
guarded by a lock rather than held open.
"""
from __future__ import annotations

import datetime
import json
import os
import threading

# Keep a generous history: a year of ordinary use is a few megabytes, and the
# questions this answers ("who changed the PI in March?") are often old ones.
MAX_BYTES = 8 * 1024 * 1024
KEEP_ROTATIONS = 3

# Recorded as "changed", never with the value.
SECRET_KEYS = {"pass", "password", "secret_key", "auth", "new_password",
               "current_password"}

# What kinds of thing get recorded, and how they read in the UI.
KINDS = {
    "login": "Sign in",
    "login_failed": "Failed sign in",
    "logout": "Sign out",
    "config": "Setting changed",
    "profile": "Profile",
    "encoder": "Encoder",
    "uecp": "UECP",
    "system": "System",
}

_lock = threading.Lock()
_path = None


def configure(data_dir: str) -> str:
    """Point the trail at a directory. Returns the file it will write to."""
    global _path
    _path = os.path.join(data_dir, "audit.log")
    return _path


def path() -> str:
    return _path or "audit.log"


def _rotate_if_large() -> None:
    try:
        if not os.path.exists(_path) or os.path.getsize(_path) < MAX_BYTES:
            return
        for index in range(KEEP_ROTATIONS - 1, 0, -1):
            older, newer = f"{_path}.{index + 1}", f"{_path}.{index}"
            if os.path.exists(newer):
                os.replace(newer, older)
        os.replace(_path, f"{_path}.1")
    except OSError:
        pass


def _ends_with_newline(name: str) -> bool:
    """Whether the file is a whole number of lines."""
    try:
        with open(name, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) == b"\n"
    except OSError:
        return True


def _client(request) -> dict:
    """Who and where, taken from a Flask request if there is one."""
    if request is None:
        return {"ip": "", "agent": ""}
    # A reverse proxy puts the real address here; the first entry is the client.
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "")
    return {"ip": ip, "agent": request.headers.get("User-Agent", "")[:120]}


def record(kind: str, action: str, detail: str = "", user: str = "",
           request=None, **extra) -> dict:
    """Write one event. Never raises: an audit failure must not stop the air."""
    event = {
        "at": datetime.datetime.now(datetime.timezone.utc)
                      .replace(microsecond=0).isoformat(),
        "kind": kind if kind in KINDS else "system",
        "action": action,
        "detail": detail,
        "user": user,
    }
    event.update(_client(request))
    if extra:
        event.update({k: v for k, v in extra.items() if v is not None})
    try:
        if _path is None:
            return event
        with _lock:
            _rotate_if_large()
            with open(_path, "a", encoding="utf-8", newline="\n") as handle:
                # If the previous write was cut short - a power cut mid-line -
                # begin a new one rather than appending onto the fragment,
                # which would lose this event as well as that one.
                if handle.tell() and not _ends_with_newline(_path):
                    handle.write("\n")
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return event


def describe_change(key: str, before, after) -> str:
    """One setting's change, with anything sensitive left out."""
    if key in SECRET_KEYS or any(s in key.lower() for s in ("pass", "secret")):
        return f"{key}: changed"

    def short(value):
        text = "" if value is None else str(value)
        text = text.replace("\n", " ")
        return text if len(text) <= 60 else text[:57] + "..."

    return f"{key}: {short(before)} -> {short(after)}"


def changes(before: dict, after: dict, ignore=()) -> list:
    """Every key whose value differs, described for the log."""
    out = []
    for key in sorted(set(before) | set(after)):
        if key in ignore:
            continue
        old, new = before.get(key), after.get(key)
        if old != new:
            out.append(describe_change(key, old, new))
    return out


def read(limit: int = 500, kind: str = "", search: str = "",
         since: str = "", until: str = "") -> list:
    """The most recent events first, filtered.

    Rotated files are read too, so a search does not stop at the point the log
    last rolled over.
    """
    files = [_path] + [f"{_path}.{n}" for n in range(1, KEEP_ROTATIONS + 1)]
    events = []
    for name in files:
        if not name or not os.path.exists(name):
            continue
        try:
            with open(name, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue          # a half-written last line
        except OSError:
            continue
        if len(events) > limit * 4:
            break

    needle = search.lower().strip()
    picked = []
    for event in events:
        if kind and event.get("kind") != kind:
            continue
        at = event.get("at", "")
        if since and at < since:
            continue
        if until and at > until:
            continue
        if needle:
            haystack = " ".join(str(event.get(f, "")) for f in
                                ("action", "detail", "user", "ip", "kind")).lower()
            if needle not in haystack:
                continue
        picked.append(event)

    picked.sort(key=lambda e: e.get("at", ""), reverse=True)
    return picked[:limit]


def summary() -> dict:
    """Counts for the panel header."""
    events = read(limit=5000)
    counts = {}
    for event in events:
        counts[event.get("kind", "system")] = counts.get(event.get("kind", "system"), 0) + 1
    return {
        "total": len(events),
        "by_kind": counts,
        "oldest": events[-1]["at"] if events else "",
        "newest": events[0]["at"] if events else "",
        "file": path(),
    }


def to_pdf(events: list, title: str = "RDS Master audit log",
           site: str = "") -> bytes:
    """The events as a PDF. Raises RuntimeError if reportlab is not installed."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                        Table, TableStyle)
    except ImportError:
        raise RuntimeError("Exporting to PDF needs reportlab: pip install reportlab")

    import io as _io

    buffer = _io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=title, author="RDS Master")
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small")
    small.fontSize = 7.5
    small.leading = 9.5

    story = [Paragraph(title + (f" - {site}" if site else ""), styles["Title"])]
    generated = datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat()
    story.append(Paragraph(f"{len(events)} events, exported {generated}",
                           styles["Normal"]))
    story.append(Spacer(1, 6 * mm))

    header = ["When (UTC)", "Type", "Action", "Detail", "User", "From"]
    rows = [[Paragraph(h, small) for h in header]]
    for event in events:
        rows.append([
            Paragraph(event.get("at", ""), small),
            Paragraph(KINDS.get(event.get("kind", ""), event.get("kind", "")), small),
            Paragraph(str(event.get("action", "")), small),
            Paragraph(str(event.get("detail", ""))[:300], small),
            Paragraph(str(event.get("user", "")), small),
            Paragraph(str(event.get("ip", "")), small),
        ])

    table = Table(rows, repeatRows=1,
                  colWidths=[32 * mm, 24 * mm, 42 * mm, 115 * mm, 22 * mm, 28 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d81b60")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#999999")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    story.append(table)
    doc.build(story)
    return buffer.getvalue()
