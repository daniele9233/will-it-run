"""Dynamic mirror: a live page recorded with its network answers, replayed from this PC and measured against the live one.

This file holds the models, the limits and the pure helpers. A value that was not measured stays ``None``: it is never replaced by one that
looks like data, and the code never calls a mirror "faithful": the report shows numbers.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import itertools
import json
import re
import shutil
import socket
import struct
import time
import zlib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from agentfit.clone import dna, pipeline
from agentfit.clone.kits import host_of
from agentfit.clone.pipeline import _fail, _step
from agentfit.workflows import checks
from agentfit.workflows.checks import CheckContext, CheckSpec
from agentfit.workflows.dsl import END, START, CheckDef, EdgeDef, WorkflowDef, WorkflowMeta

HERE = Path(__file__).resolve().parent
RECORD_SCRIPT = HERE / "mirror.mjs"
WORKFLOW = "clone-site-mirror"

LIMITS: dict[str, int] = {"total_mb": 150, "file_mb": 40, "responses": 3000, "seconds": 120}

_HASH_NAME = re.compile(r"[0-9a-f]{16,64}")


class Entry(BaseModel):
    url: str
    host: str
    path: str
    status: int
    content_type: str | None
    file: str | None
    bytes: int
    order: int
    rewritten: bool = False


class Limit(BaseModel):
    code: str  # "too_large", "too_many", "timeout", "wheel_driver", "blank_first_load"
    count: int | None = None


class MirrorIndex(BaseModel):
    schema_version: Literal[1] = 1
    url: str
    final_url: str
    host: str
    captured_at: str
    viewport: tuple[int, int]
    document: str | None
    entries: list[Entry]
    limits_hit: list[Limit]
    not_recorded: list[str]
    driver: Literal["scroll", "wheel"] | None = None


class MirrorReport(BaseModel):
    """What the verification measured. Every field is optional: one that was not measured is ``None``."""

    schema_version: Literal[1] = 1
    live_at: str | None = None
    replay_at: str | None = None
    dna_match: dict[str, Any] | None = None
    facts: dict[str, dict[str, Any]] | None = None
    motion: dict[str, Any] | None = None
    pixels: list[dict[str, Any]] | None = None
    requests: dict[str, Any] | None = None
    loads: list[dict[str, Any]] | None = None
    driver: Literal["scroll", "wheel"] | None = None
    not_covered: list[str] | None = None
    seconds: float | None = None


def mirror_dir(workspace: Path) -> Path:
    return workspace / "mirror"


def local_path(entry_host: str, entry_path: str, document_host: str) -> str:
    """The address a recorded response is served at: the document's own host keeps its root-relative path, any other host goes under ``/_m/``."""
    path = entry_path if entry_path.startswith("/") else "/" + entry_path
    if entry_host.lower() == document_host.lower():
        return path
    return f"/_m/{entry_host}{path}"


def rewrite_text(text: str, document_host: str, recorded_hosts: set[str]) -> tuple[str, bool]:
    """Turn absolute and protocol-relative URLs of recorded hosts into local paths. Nothing else is edited."""
    if not recorded_hosts:
        return text, False
    hosts = "|".join(re.escape(h) for h in sorted(recorded_hosts, key=len, reverse=True))
    pattern = re.compile(rf"(?:(?<![\w])https?:)?(?<![\w:/.-])//(?P<host>{hosts})(?=/)|(?<![\w])https?://(?P<host2>{hosts})(?=/)", re.IGNORECASE)
    lowered = {h.lower() for h in recorded_hosts}

    def swap(m: re.Match[str]) -> str:
        host = (m.group("host") or m.group("host2")).lower()
        if host not in lowered:
            return m.group(0)
        return "" if host == document_host.lower() else f"/_m/{host}"

    out = pattern.sub(swap, text)
    return out, out != text


def safe_file(root: Path, name: str) -> Path | None:
    """The recorded file ``name`` under ``root/files``, or ``None`` for anything that is not a plain hash name of an existing file."""
    if not _HASH_NAME.fullmatch(name):
        return None
    path = root / "files" / name
    return path if path.is_file() else None


# ---------------------------------------------------------------- the mirror as a run

def _is_text(content_type: str | None) -> bool:
    """Only HTML, CSS, JavaScript and JSON are rewritten; every other body stays as it came."""
    kind = (content_type or "").split(";")[0].strip().lower()
    return kind in ("text/html", "text/css") or "javascript" in kind or "ecmascript" in kind or "json" in kind


def address_problem(url: str) -> str | None:
    """Why ``url`` may not be recorded, or ``None``: only http and https, and no host on a private or link-local network (this PC is fine)."""
    try:
        parsed = urlparse(url.strip())
        host = parsed.hostname
        _ = parsed.port
    except ValueError:
        return "The address is not valid."
    if parsed.scheme not in ("http", "https"):
        return "Only http and https addresses can be recorded."
    if not host:
        return "The address has no host."
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return None
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in socket.getaddrinfo(host, None)]
        except (OSError, ValueError):
            return None  # not resolvable here: the browser will say so
    for address in addresses:
        if address.is_loopback:
            continue
        if address.is_private or address.is_link_local or address.is_reserved or address.is_unspecified or address.is_multicast:
            return "That address is on a private network: only public sites and this PC can be recorded."
    return None


def _rewrite_recorded(folder: Path) -> MirrorIndex:
    """Rewrite the recorded text files in place: the new body is stored under its own hash, ``Entry.file`` follows, ``index.json`` is saved."""
    index = MirrorIndex.model_validate_json((folder / "index.json").read_text(encoding="utf-8-sig"))
    hosts = {e.host for e in index.entries} | {index.host}
    files = folder / "files"
    groups: dict[str, list[Entry]] = {}
    for entry in index.entries:
        if entry.file:
            groups.setdefault(entry.file, []).append(entry)
    moved: dict[str, str] = {}
    for name, group in groups.items():
        if not all(_is_text(e.content_type) for e in group) or safe_file(folder, name) is None:
            continue
        try:
            text = (files / name).read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        new_text, changed = rewrite_text(text, index.host, hosts)
        if not changed:
            continue
        body = new_text.encode("utf-8")
        new_name = hashlib.sha256(body).hexdigest()
        if new_name != name:
            (files / new_name).write_bytes(body)
            (files / name).unlink(missing_ok=True)
        moved[name] = new_name
        for entry in group:
            entry.file, entry.bytes, entry.rewritten = new_name, len(body), True
    if index.document in moved:
        index.document = moved[index.document]
    (folder / "index.json").write_text(index.model_dump_json(indent=2), encoding="utf-8")
    return index


def _record_script(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
    """Run ``mirror.mjs`` with a config; (True, "") when it finished well. The seam the tests replace."""
    install = pipeline._install()
    browser = pipeline.find_browser()
    if install is None or ctx.workspace is None or browser is None:
        return False, "The browser engine, or Chrome or Edge, is not available."
    out = Path(config["out"])
    out.mkdir(parents=True, exist_ok=True)
    path = out / "mirror.config.json"
    path.write_text(json.dumps({**config, "executable": browser["executable"]}), encoding="utf-8")
    return pipeline._run_script(ctx, install, RECORD_SCRIPT, path, "record", timeout=float(LIMITS["seconds"]) + 240.0)


def _clone_mirror_record(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    if pipeline._install() is None or ctx.workspace is None:
        return _fail("The recording is not available in this run.", "MIRROR_FAILED")
    url = str(checks.param(check, "url"))
    problem = address_problem(url)
    if problem:
        _step(ctx, "record", "failed", problem)
        return _fail(problem, "MIRROR_FAILED")
    folder = mirror_dir(ctx.workspace)
    shutil.rmtree(folder, ignore_errors=True)
    _step(ctx, "record", "running", url)
    ok, why = _record_script(ctx, {"url": url, "out": str(folder), "locale": str(checks.param(check, "locale") or ""), "limits": dict(LIMITS)})
    if not ok:
        _step(ctx, "record", "failed", why)
        return _fail(f"The site could not be recorded: {why}", "MIRROR_FAILED")
    try:
        index = _rewrite_recorded(folder)
    except (OSError, ValueError) as exc:
        _step(ctx, "record", "failed", str(exc)[:160])
        return _fail(f"The recording could not be read: {exc}", "MIRROR_FAILED")
    if index.document is None:
        main = next((e for e in index.entries if e.url == index.final_url), None)
        why = f"The page answered {main.status}." if main is not None and main.status >= 400 else "The page itself was not recorded (it may be over the file limit)."
        _step(ctx, "record", "failed", why)
        return _fail(f"The site could not be recorded: {why}", "MIRROR_FAILED")
    limits = ", ".join(sorted({limit.code for limit in index.limits_hit})) or "none"
    summary = f"Recorded {index.host}: {len(index.entries)} responses, {sum(1 for e in index.entries if e.rewritten)} rewritten, limits: {limits}."
    _step(ctx, "record", "done", summary)
    return {"passed": True, "verdict": "RECORDED", "summary": summary}


# ---------------------------------------------------------------- the verification

PROBE_SCRIPT = HERE / "mirror_probe.mjs"
WIDTHS: tuple[tuple[int, int], ...] = ((1440, 900), (390, 844))
PROBE_STEPS = 20
NOT_COVERED = ["hover", "click", "other pages", "sound", "touch", "login", "what loads only after a long wait"]
_FACTS = ("height", "sections", "elements", "img", "video", "canvas", "words")
_MOVE_PX, _MOVE_OPACITY = 0.5, 0.01  # a change smaller than this is not a movement
_MATCH_PX, _MATCH_OPACITY = 2.0, 0.02  # the live and the replay sequences match within this
_PIXEL_TOLERANCE = 16
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _sequences(probe: dict[str, Any]) -> dict[str, list[tuple[float, ...]]]:
    keys: dict[str, list[tuple[float, ...]]] = {}
    for step in probe.get("steps") or []:
        for item in step.get("items") or []:
            keys.setdefault(str(item["key"]), []).append(
                (float(item["tx"]), float(item["ty"]), float(item["left"]), float(item["top"]), float(item["opacity"])))
    return keys


def _moves(sequence: list[tuple[float, ...]]) -> bool:
    return any(abs(a[i] - b[i]) > (_MOVE_OPACITY if i == 4 else _MOVE_PX) for a, b in itertools.pairwise(sequence) for i in range(5))


def _same(a: list[tuple[float, ...]], b: list[tuple[float, ...]]) -> bool:
    return len(a) == len(b) and all(abs(p[i] - q[i]) <= (_MATCH_OPACITY if i == 4 else _MATCH_PX) for p, q in zip(a, b, strict=True) for i in range(5))


def motion_compare(live: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any] | None:
    """How many followed elements moved in the live page and in the replay, and the share of the live ones the replay moved the same way."""
    if not live.get("steps") or not replay.get("steps"):
        return None
    seq_live, seq_replay = _sequences(live), _sequences(replay)
    moving_live = [k for k, v in seq_live.items() if _moves(v)]
    moving_replay = [k for k, v in seq_replay.items() if _moves(v)]
    matching = sum(1 for k in moving_live if k in seq_replay and _same(seq_live[k], seq_replay[k]))
    return {"moving_live": len(moving_live), "moving_replay": len(moving_replay),
            "matching": round(matching / len(moving_live), 4) if moving_live else None}


# Adding two byte strings byte by byte (mod 256) with big integers: every byte is one lane, the carry of a lane never leaks into the next.
def _lanes(n: int, byte: int) -> int:
    return int.from_bytes(bytes([byte]) * n, "little")


def _add_bytes(a: int, b: int, low: int, high: int) -> int:
    return ((a & low) + (b & low)) ^ ((a ^ b) & high)


def _decode_png(data: bytes) -> tuple[int, int, int, list[bytes]]:
    """(width, height, bytes per pixel, rows) of an 8-bit RGB or RGBA PNG that is not interlaced. Anything else is a ``ValueError``."""
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG")
    pos, header, idat = len(_PNG_SIGNATURE), None, bytearray()
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + size]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + size
    if header is None:
        raise ValueError("no PNG header")
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace != 0 or width == 0 or height == 0:
        raise ValueError("only 8-bit RGB and RGBA PNGs that are not interlaced are read")
    bpp = 3 if color == 2 else 4
    stride = width * bpp
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError("the PNG data is damaged") from exc
    if len(raw) < height * (stride + 1):
        raise ValueError("the PNG data is short")
    low, high = _lanes(stride, 0x7F), _lanes(stride, 0x80)
    full = (1 << (8 * stride)) - 1
    rows: list[bytes] = []
    previous = bytes(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
        if kind == 0:
            row = line
        elif kind == 2:
            row = _add_bytes(int.from_bytes(line, "little"), int.from_bytes(previous, "little"), low, high).to_bytes(stride, "little")
        elif kind == 1:
            x, shift = int.from_bytes(line, "little"), bpp
            while shift < stride:  # a running sum along the row, doubled each time
                x = _add_bytes(x, (x << (8 * shift)) & full, low, high)
                shift *= 2
            row = x.to_bytes(stride, "little")
        elif kind in (3, 4):
            out = bytearray(line)
            for i in range(stride):
                a = out[i - bpp] if i >= bpp else 0
                b = previous[i]
                if kind == 3:
                    out[i] = (line[i] + (a + b) // 2) & 255
                else:
                    c = previous[i - bpp] if i >= bpp else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    out[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
            row = bytes(out)
        else:
            raise ValueError("unknown PNG filter")
        rows.append(row)
        previous = row
    return width, height, bpp, rows


def pixel_share(a: bytes, b: bytes) -> float:
    """The share of pixels whose red, green and blue differ by at most 16/255 between two PNG screenshots of the same size."""
    wa, ha, bpa, rows_a = _decode_png(a)
    wb, hb, bpb, rows_b = _decode_png(b)
    if (wa, ha) != (wb, hb):
        raise ValueError("the pictures have different sizes")
    close = 0
    for ra, rb in zip(rows_a, rows_b, strict=True):
        if ra == rb:
            close += wa
            continue
        for x in range(wa):
            i, j = x * bpa, x * bpb
            if (abs(ra[i] - rb[j]) <= _PIXEL_TOLERANCE and abs(ra[i + 1] - rb[j + 1]) <= _PIXEL_TOLERANCE
                    and abs(ra[i + 2] - rb[j + 2]) <= _PIXEL_TOLERANCE):
                close += 1
    return close / (wa * ha)


def _verify_script(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
    """Run ``mirror_probe.mjs`` (``config["script"] == "probe"``, ``out`` is a file) or ``dna.mjs`` (``"dna"``, ``out`` is a folder). The seam the tests replace."""
    install = pipeline._install()
    browser = pipeline.find_browser()
    if install is None or ctx.workspace is None or browser is None:
        return False, "The browser engine, or Chrome or Edge, is not available."
    which = str(config["script"])
    rest = {k: v for k, v in config.items() if k != "script"}
    out = Path(config["out"])
    if which == "probe":
        out.parent.mkdir(parents=True, exist_ok=True)
        path, script = out.with_suffix(".config.json"), PROBE_SCRIPT
    else:
        out.mkdir(parents=True, exist_ok=True)
        path, script = out / "dna.config.json", dna.DNA_SCRIPT
    path.write_text(json.dumps({**rest, "executable": browser["executable"]}), encoding="utf-8")
    return pipeline._run_script(ctx, install, script, path, "verify", timeout=300.0)


def _probe(ctx: CheckContext, folder: Path, label: str, url: str, size: tuple[int, int], driver: str, *, watch: bool) -> dict[str, Any] | None:
    """One probe of one page at one width; ``None`` when it did not finish (a value that was not measured)."""
    out = folder / "probe" / f"{label}-{size[0]}.json"
    out.unlink(missing_ok=True)
    config: dict[str, Any] = {"script": "probe", "url": url, "out": str(out), "viewport": list(size), "steps": PROBE_STEPS, "driver": driver,
                              "shots": str(folder / "shots" / f"{label}-{size[0]}")}
    if watch:
        config["watch_hosts"] = True
    ok, why = _verify_script(ctx, config)
    _step(ctx, "verify", "running" if ok else "failed", f"{label} {size[0]}: {'measured' if ok else why}"[:200])
    if not ok:
        return None
    try:
        found = json.loads(out.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _study(ctx: CheckContext, folder: Path, label: str, url: str) -> dna.SiteDNA | None:
    out = folder / "dna" / label
    ok, _ = _verify_script(ctx, {"script": "dna", "url": url, "out": str(out)})
    if not ok:
        return None
    try:
        return dna.analyze(json.loads((out / "dna-raw.json").read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _facts(live: dict[str, Any] | None, replay: dict[str, Any] | None) -> dict[str, Any]:
    """Each document fact with both values and whether they are equal; one that a probe did not give is ``None`` and so is ``equal``."""
    out: dict[str, Any] = {}
    for key in _FACTS:
        a = None if live is None else live.get(key)
        b = None if replay is None else replay.get(key)
        out[key] = {"live": a, "replay": b, "equal": None if a is None or b is None else a == b}
    return out


def _pixels(folder: Path, driver: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for at in ([0, 5, 10, 15, 20] if driver == "wheel" else [0, 25, 50, 75, 100]):
        name = f"shot-{at}.png"
        share: float | None = None
        with contextlib.suppress(OSError, ValueError):
            share = round(pixel_share((folder / "shots" / "live-1440" / name).read_bytes(), (folder / "shots" / "replay-1440" / name).read_bytes()), 4)
        out.append({"at": at, "within_16": share})
    return out


def _clone_mirror_verify(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    from agentfit.clone.mirror_server import MirrorServer  # the server imports this module

    if pipeline._install() is None or ctx.workspace is None:
        return _fail("The verification is not available in this run.", "MIRROR_FAILED")
    folder = mirror_dir(ctx.workspace)
    try:
        index = MirrorIndex.model_validate_json((folder / "index.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        _step(ctx, "verify", "failed", str(exc)[:160])
        return _fail(f"The recording could not be read: {exc}", "MIRROR_FAILED")
    url = str(checks.param(check, "url") or index.url)
    driver = index.driver or "scroll"
    started = time.monotonic()
    shutil.rmtree(folder / "shots", ignore_errors=True)
    shutil.rmtree(folder / "probe", ignore_errors=True)
    server = MirrorServer(folder)
    try:
        replay_url = server.start()
    except (OSError, ValueError) as exc:
        _step(ctx, "verify", "failed", str(exc)[:160])
        return _fail(f"The replay could not be started: {exc}", "MIRROR_FAILED")
    live: dict[int, dict[str, Any] | None] = {}
    replay: dict[int, dict[str, Any] | None] = {}
    loads: list[dict[str, Any]] = []
    leaked: set[str] = set()
    dna_match: dict[str, Any] | None = None
    try:
        _step(ctx, "verify", "running", replay_url)
        for size in WIDTHS:
            live[size[0]] = _probe(ctx, folder, "live", url, size, driver, watch=False)
            found = _probe(ctx, folder, "replay", replay_url, size, driver, watch=True)
            if found is not None:
                loads.append({"width": size[0], "visible_words": int(found.get("words") or 0)})
                leaked.update(str(h) for h in found.get("foreign_hosts") or [])  # the first load's leaks count even when it is blank
                if not found.get("words"):  # a blank first load: load again, once; both loads stay in the report
                    again = _probe(ctx, folder, "replay", replay_url, size, driver, watch=True)
                    if again is not None:
                        loads.append({"width": size[0], "visible_words": int(again.get("words") or 0)})
                        found = again
                leaked.update(str(h) for h in found.get("foreign_hosts") or [])
            replay[size[0]] = found
        study_live, study_replay = _study(ctx, folder, "live", url), _study(ctx, folder, "replay", replay_url)
        if study_live is not None and study_replay is not None:
            dna_match = dna.dna_match(study_live, study_replay)
    finally:
        log = server.requests()
        server.stop()
    served = any(r["path"] == "/" and r["status"] == 200 for r in log)
    both = live.get(WIDTHS[0][0]) is not None and replay.get(WIDTHS[0][0]) is not None
    report = MirrorReport(
        live_at=url, replay_at=replay_url, dna_match=dna_match,
        facts={str(w): _facts(live.get(w), replay.get(w)) for w, _ in WIDTHS},
        motion=motion_compare(live.get(WIDTHS[0][0]) or {}, replay.get(WIDTHS[0][0]) or {}),
        pixels=_pixels(folder, driver) if both else None,
        requests={"not_recorded": sum(1 for r in log if r["not_recorded"]), "leaked_hosts": sorted(leaked)},
        loads=loads, driver=driver, not_covered=list(NOT_COVERED), seconds=round(time.monotonic() - started, 1),
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    if not served:
        why = "The replay did not serve the recorded document to the browser."
        _step(ctx, "verify", "failed", why)
        return _fail(why, "MIRROR_FAILED")
    summary = f"Measured the replay of {index.host} against the live page: {len(loads)} loads, {report.requests['not_recorded']} unrecorded requests."
    _step(ctx, "verify", "done", summary)
    return {"passed": True, "verdict": "MIRRORED", "summary": summary, "report": report.model_dump(mode="json")}


def definition(*, url: str) -> WorkflowDef:
    params = {"url": url, "host": host_of(url), "locale": ""}
    return WorkflowDef(
        workflow=WorkflowMeta(id=WORKFLOW, name="Mirror a site", category="clone", max_steps=6,
                              description="Records a live page with its network answers, replays it from this PC and measures it against the live one."),
        models={}, agents=[],
        checks=[CheckDef(id="record", kind="clone_mirror_record", label="Record", params=params),
                CheckDef(id="verify", kind="clone_mirror_verify", label="Verify", params={"url": url, "host": host_of(url)})],
        edges=[EdgeDef(source=START, target="record"), EdgeDef(source="record", target="verify"), EdgeDef(source="verify", target=END)],
        layout={"record": {"x": 0.0, "y": 0.0}, "verify": {"x": 240.0, "y": 0.0}},
    )


def register() -> None:
    checks.CHECKS["clone_mirror_record"] = CheckSpec(
        kind="clone_mirror_record", label="Record",
        description="Opens the page in Chrome, scrolls it, and stores every answer the page receives, then points the stored text at this PC.",
        run=_clone_mirror_record, defaults={"url": "", "host": "", "locale": ""}, listed=False,
    )
    checks.CHECKS["clone_mirror_verify"] = CheckSpec(
        kind="clone_mirror_verify", label="Verify",
        description="Opens the live page and the replay at two widths, follows what moves while scrolling, compares screenshots and lists what the replay lacked. It gives numbers, not a verdict.",
        run=_clone_mirror_verify, defaults={"url": "", "host": ""}, listed=False,
    )


register()
