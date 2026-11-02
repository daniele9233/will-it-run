"""The mirror replay server, on a hand-written recorded folder. Loopback only, no browser."""

from __future__ import annotations

import http.client
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentfit.clone.mirror import Entry, MirrorIndex
from agentfit.clone.mirror_server import MirrorServer

DOC = "aa" * 8
JS = "bb" * 8
VIDEO = "cc" * 8
VIDEO_BYTES = bytes(range(200))


def _entry(url: str, host: str, path: str, file: str, ctype: str, size: int, order: int) -> Entry:
    return Entry(url=url, host=host, path=path, status=200, content_type=ctype, file=file, bytes=size, order=order)


@pytest.fixture()
def server(tmp_path: Path) -> Iterator[MirrorServer]:
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / DOC).write_bytes(b"<html>doc</html>")
    (tmp_path / "files" / JS).write_bytes(b"var a=1;")
    (tmp_path / "files" / VIDEO).write_bytes(VIDEO_BYTES)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    index = MirrorIndex(
        url="https://x.com/", final_url="https://x.com/", host="x.com", captured_at="t", viewport=(1440, 900), document=DOC,
        entries=[
            _entry("https://x.com/", "x.com", "/", DOC, "text/html; charset=utf-8", 16, 0),
            _entry("https://x.com/app.js?v=1", "x.com", "/app.js?v=1", JS, "application/javascript", 8, 1),
            _entry("https://cdn.x.com/v.mp4", "cdn.x.com", "/v.mp4", VIDEO, "video/mp4", 200, 2),
            _entry("https://x.com/huge.bin", "x.com", "/huge.bin", "", "application/octet-stream", 0, 3).model_copy(update={"file": None}),
            _entry("https://x.com/gone.js", "x.com", "/gone.js", "dd" * 8, "application/javascript", 8, 4),
        ],
        limits_hit=[], not_recorded=[],
    )
    (tmp_path / "index.json").write_text(index.model_dump_json(), encoding="utf-8")
    srv = MirrorServer(tmp_path)
    srv.start()
    yield srv
    srv.stop()


def _get(srv: MirrorServer, path: str, headers: dict[str, str] | None = None, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    host, port = srv.start().removeprefix("http://").rstrip("/").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    try:
        conn.request(method, path, headers=headers or {})
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def test_a_recorded_file_is_served_with_its_type(server: MirrorServer) -> None:
    status, headers, body = _get(server, "/")
    assert (status, body) == (200, b"<html>doc</html>")
    assert headers["content-type"].startswith("text/html")
    assert headers["cache-control"] == "no-store" and headers["access-control-allow-origin"] == "*"
    status, headers, body = _get(server, "/app.js?v=1")
    assert (status, body, headers["content-type"]) == (200, b"var a=1;", "application/javascript")
    assert _get(server, "/_m/cdn.x.com/v.mp4")[2] == VIDEO_BYTES
    assert server.start() == server.start()


def test_a_range_request_gets_206_and_the_bytes_asked_for(server: MirrorServer) -> None:
    status, headers, body = _get(server, "/_m/cdn.x.com/v.mp4", {"Range": "bytes=10-19"})
    assert status == 206 and body == VIDEO_BYTES[10:20]
    assert headers["content-range"] == "bytes 10-19/200" and headers["accept-ranges"] == "bytes"
    status, headers, body = _get(server, "/_m/cdn.x.com/v.mp4", {"Range": "bytes=190-"})
    assert status == 206 and body == VIDEO_BYTES[190:]
    assert _get(server, "/_m/cdn.x.com/v.mp4", {"Range": "bytes=500-"})[0] == 416


def _logged(server: MirrorServer, count: int) -> list[dict]:
    """The log is written just after the answer, so the client can be back before the line is: wait for it."""
    deadline = time.monotonic() + 5
    while len(server.requests()) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    return server.requests()


def test_an_unrecorded_path_is_a_404_marked_not_recorded_and_logged(server: MirrorServer) -> None:
    status, headers, _ = _get(server, "/api/runtime-built")
    assert status == 404 and headers["x-mirror"] == "not-recorded"
    _get(server, "/")
    log = _logged(server, 2)
    assert log[0] == {"path": "/api/runtime-built", "status": 404, "bytes": 12, "not_recorded": True}
    assert log[1]["status"] == 200 and log[1]["not_recorded"] is False and log[1]["bytes"] == 16
    assert _get(server, "/", method="POST")[0] == 405


def test_a_path_with_dotdot_or_an_encoded_slash_never_reads_a_file(server: MirrorServer) -> None:
    for path in ("/../secret.txt", "/..%2fsecret.txt", "/%2e%2e/secret.txt", f"/files/{DOC}", "/_m/x.com/../../secret.txt", "/" + "a" * 5000):
        status, headers, body = _get(server, path)
        assert status == 404 and headers["x-mirror"] == "not-recorded" and b"secret" not in body


def test_twenty_connections_at_once_all_get_their_file(server: MirrorServer) -> None:
    results: list[bytes] = []
    lock = threading.Lock()

    def worker() -> None:
        body = _get(server, "/_m/cdn.x.com/v.mp4")[2]
        with lock:
            results.append(body)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(results) == 20 and all(r == VIDEO_BYTES for r in results)


def test_stop_frees_the_port(server: MirrorServer) -> None:
    port = int(server.start().rsplit(":", 1)[1].rstrip("/"))
    server.stop()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_only_127_0_0_1_listens(server: MirrorServer) -> None:
    address = server.start()
    assert address.startswith("http://127.0.0.1:")
    port = int(address.rsplit(":", 1)[1].rstrip("/"))
    assert server._httpd is not None and server._httpd.server_address[0] == "127.0.0.1"
    with socket.socket() as s:
        s.settimeout(1)
        assert s.connect_ex(("127.0.0.1", port)) == 0


def test_an_entry_without_a_body_or_with_a_missing_file_is_a_404_marked_not_recorded(server: MirrorServer) -> None:
    for path in ("/huge.bin", "/gone.js"):
        status, headers, _ = _get(server, path)
        assert status == 404 and headers["x-mirror"] == "not-recorded"
    log = _logged(server, 2)
    assert [(r["path"], r["status"], r["not_recorded"]) for r in log] == [("/huge.bin", 404, True), ("/gone.js", 404, True)]
