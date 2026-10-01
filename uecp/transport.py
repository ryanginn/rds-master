"""Getting UECP frames in: a TCP listener and a WebSocket client.

Both feed the same Store through handler.apply_frame(). Neither ever blocks the
audio path - they run on their own threads and only mutate the store.

Appendix 2 of SPB 490 covers UECP over IP. Framing on the wire is unchanged, so
a stream of bytes is all either transport has to deliver.
"""
from __future__ import annotations

import base64
import binascii
import socket
import socketserver
import threading
import time

from .frame import STA, STP, FrameReader, UecpError
from .handler import apply_frame
from .store import Store


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        store = server.store
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        store.note(f"TCP client connected from {peer}")
        server.clients += 1
        reader = FrameReader()
        try:
            self.request.settimeout(1.0)
            while not server.stopping:
                try:
                    chunk = self.request.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                for item in reader.feed(chunk):
                    if isinstance(item, UecpError):
                        store.errors += 1
                        store.last_error = str(item)
                        store.note(f"bad frame from {peer}: {item}")
                        continue
                    apply_frame(store, item, server.allowed, server.link)
                    if server.on_change:
                        server.on_change()
        finally:
            server.clients -= 1
            store.note(f"TCP client {peer} disconnected")


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, store, on_change, allowed=None, link=""):
        self.allowed = allowed
        self.link = link
        self.store = store
        self.on_change = on_change
        self.clients = 0
        self.stopping = False
        super().__init__(addr, _Handler)


class TcpListener:
    """Accepts UECP over TCP. Keeps trying to bind if the port is busy."""

    def __init__(self, host: str, port: int, store: Store, on_change=None,
                 allowed=None, name: str = "") -> None:
        self.allowed = allowed
        self.name = name or f"TCP {host}:{port}"
        self.host, self.port = host, port
        self.store = store
        self.on_change = on_change
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def clients(self) -> int:
        return self._server.clients if self._server else 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._server = _Server((self.host, self.port), self.store,
                                       self.on_change, self.allowed, self.name)
            except OSError as exc:
                self.last_error = str(exc)
                self.store.note(f"TCP listener cannot bind {self.host}:{self.port} - {exc}")
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0)
                continue
            self.last_error = ""
            backoff = 1.0
            self.store.note(f"TCP listener on {self.host}:{self.port}")
            try:
                self._server.serve_forever(poll_interval=0.5)
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                try:
                    self._server.server_close()
                except Exception:
                    pass
                self._server = None
            if self._stop.is_set():
                return

    def stop(self) -> None:
        self._stop.set()
        srv = self._server
        if srv is not None:
            srv.stopping = True
            try:
                srv.shutdown()
            except Exception:
                pass
        self.store.note("TCP listener stopped")


def _ws_payload(data) -> bytes | None:
    """Get raw UECP bytes out of one WebSocket message.

    Binary frames are already raw. Text frames are base64 in practice - the
    "pacific" style servers send each record that way - so decode base64 first
    and only fall back to treating the text as raw bytes if that does not
    produce something that looks like a UECP record.
    """
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if not isinstance(data, str):
        return None
    text = data.strip()
    if not text:
        return None
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if raw and (STA in raw or STP in raw):
        return raw
    return text.encode("latin-1", errors="replace")


class WebSocketClient:
    """Connects out to a UECP-over-WebSocket source and reconnects if it drops.

    Needs the `websocket-client` package; without it the client stays stopped and
    says so, rather than failing silently.
    """

    def __init__(self, url: str, store: Store, on_change=None,
                 allowed=None, name: str = "") -> None:
        self.allowed = allowed
        self.name = name or f"WebSocket {url}"
        self.url = url
        self.store = store
        self.on_change = on_change
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ws = None
        self.connected = False
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            import websocket  # type: ignore
        except ImportError:
            self.last_error = "websocket-client is not installed (pip install websocket-client)"
            self.store.note(f"WebSocket client disabled: {self.last_error}")
            return

        backoff = 1.0
        while not self._stop.is_set():
            reader = FrameReader()
            try:
                self.store.note(f"WebSocket connecting to {self.url}")
                self._ws = websocket.create_connection(self.url, timeout=10)
                self._ws.settimeout(1.0)
                self.connected = True
                self.last_error = ""
                backoff = 1.0
                self.store.note(f"WebSocket connected to {self.url}")
                while not self._stop.is_set():
                    try:
                        data = self._ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not data:
                        break
                    data = _ws_payload(data)
                    if data is None:
                        continue
                    for item in reader.feed(data):
                        if isinstance(item, UecpError):
                            self.store.errors += 1
                            self.store.last_error = str(item)
                            self.store.note(f"bad frame over WebSocket: {item}")
                            continue
                        apply_frame(self.store, item, self.allowed, self.name)
                        if self.on_change:
                            self.on_change()
            except Exception as exc:
                self.last_error = str(exc)
                self.store.note(f"WebSocket error: {exc}")
            finally:
                self.connected = False
                try:
                    if self._ws:
                        self._ws.close()
                except Exception:
                    pass
                self._ws = None
            if self._stop.is_set():
                return
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 30.0)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self.store.note("WebSocket client stopped")
