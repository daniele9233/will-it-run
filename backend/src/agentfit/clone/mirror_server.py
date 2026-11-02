"""The replay server of the dynamic mirror: recorded answers served from this PC, on ``127.0.0.1`` only.

It never fetches anything and never uses a request path as a file path: a request is looked up in the recorded index
and only the hash name of a recorded file is opened, through ``safe_file``.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agentfit.clone.mirror import Entry, MirrorIndex, local_path, safe_file

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_CHUNK = 256 * 1024


class MirrorServer:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._log: list[dict[str, Any]] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._address: str | None = None
        self._routes: dict[str, Entry] = {}
        self._document: Entry | None = None

    def _load(self) -> None:
        index = MirrorIndex.model_validate(json.loads((self._root / "index.json").read_text(encoding="utf-8")))
        routes: dict[str, Entry] = {}
        for entry in sorted(index.entries, key=lambda e: e.order):
            routes.setdefault(local_path(entry.host, entry.path, index.host), entry)
        self._routes = routes
        self._document = next((e for e in index.entries if index.document and e.file == index.document), None)

    def lookup(self, raw_path: str) -> Entry | None:
        if raw_path == "/" and self._document is not None:
            return self._document
        hit = self._routes.get(raw_path)
        if hit is None and "?" in raw_path:
            hit = self._routes.get(raw_path.split("?", 1)[0])
        return hit

    def start(self) -> str:
        with self._lock:
            if self._address is not None:
                return self._address
            self._load()
            owner = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, format: str, *args: Any) -> None:
                    return

                def do_GET(self) -> None:
                    owner._answer(self, head=False)

                def do_HEAD(self) -> None:
                    owner._answer(self, head=True)

                def _refuse(self) -> None:
                    self.send_response(405)
                    self.send_header("Allow", "GET, HEAD")
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            httpd.daemon_threads = True
            self._httpd = httpd
            self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            self._thread.start()
            self._address = f"http://127.0.0.1:{httpd.server_address[1]}/"
            return self._address

    def stop(self) -> None:
        with self._lock:
            httpd, thread = self._httpd, self._thread
            self._httpd = self._thread = self._address = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._log]

    def _record(self, path: str, status: int, size: int, not_recorded: bool) -> None:
        with self._lock:
            self._log.append({"path": path, "status": status, "bytes": size, "not_recorded": not_recorded})

    def _answer(self, h: BaseHTTPRequestHandler, head: bool) -> None:
        path = h.path
        entry = self.lookup(path)
        file = safe_file(self._root, entry.file) if entry is not None and entry.file else None
        if entry is None or file is None:
            body = b"not recorded"
            h.send_response(404)
            h.send_header("X-Mirror", "not-recorded")
            h.send_header("Content-Type", "text/plain; charset=utf-8")
            h.send_header("Cache-Control", "no-store")
            h.send_header("Access-Control-Allow-Origin", "*")
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            if not head:
                h.wfile.write(body)
            self._record(path, 404, len(body), True)
            return
        size = file.stat().st_size if file is not None else 0
        start, end, status = 0, size - 1, entry.status
        rng = _RANGE.fullmatch(h.headers.get("Range", "").strip()) if file is not None and status == 200 else None
        if rng is not None and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
            else:
                start = max(size - int(rng.group(2)), 0)
            if start >= size or start > end:
                h.send_response(416)
                h.send_header("Content-Range", f"bytes */{size}")
                h.send_header("Content-Length", "0")
                h.end_headers()
                self._record(path, 416, 0, False)
                return
            status = 206
        length = max(end - start + 1, 0) if file is not None else 0
        h.send_response(status)
        h.send_header("Content-Type", entry.content_type or "application/octet-stream")
        h.send_header("Cache-Control", "no-store")
        h.send_header("Access-Control-Allow-Origin", "*")
        h.send_header("Accept-Ranges", "bytes")
        if status == 206:
            h.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        h.send_header("Content-Length", str(length))
        h.end_headers()
        sent = 0
        if file is not None and not head:
            try:
                with file.open("rb") as fh:
                    fh.seek(start)
                    while sent < length:
                        chunk = fh.read(min(_CHUNK, length - sent))
                        if not chunk:
                            break
                        h.wfile.write(chunk)
                        sent += len(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        self._record(path, status, sent if not head else 0, False)
