"""Dynamic mirror, the verification: motion, pixels and the report. No browser: the script seam is replaced by a fake that visits the real replay server."""

from __future__ import annotations

import json
import struct
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agentfit.clone import mirror
from agentfit.clone.mirror import MirrorReport, motion_compare, pixel_share
from agentfit.workflows import checks
from agentfit.workflows.checks import CheckContext
from test_clone_mirror import URL, engine, record_ctx, write_mirror  # noqa: F401  (engine is a fixture)


def _steps(*rows: list[tuple[str, float, float]]) -> list[dict[str, Any]]:
    """One probe step per row; an item is (key, tx, opacity)."""
    return [{"scroll_y": i * 450, "items": [{"key": k, "tx": tx, "ty": 0.0, "opacity": op, "left": 0.0, "top": 10.0} for k, tx, op in row]}
            for i, row in enumerate(rows)]


def test_motion_compare_counts_moving_elements_and_matching_ones() -> None:
    live = {"steps": _steps([("a", 0, 1), ("b", 0, 0), ("c", 5, 1)], [("a", 100, 1), ("b", 0, 1), ("c", 5, 1)], [("a", 200, 1), ("b", 0, 1), ("c", 5, 1)])}
    replay = {"steps": _steps([("a", 0, 1), ("b", 0, 0), ("c", 5, 1)], [("a", 101, 1), ("b", 0, 0.5), ("c", 5, 1)], [("a", 201.5, 1), ("b", 0, 0.5), ("c", 5, 1)])}
    # a stays within 2 px; b's opacity is 0.5 off; c never moved
    assert motion_compare(live, replay) == {"moving_live": 2, "moving_replay": 2, "matching": 0.5}


def test_motion_compare_is_none_without_steps() -> None:
    assert motion_compare({"steps": []}, {"steps": _steps([("a", 0, 1)])}) is None
    assert motion_compare({"steps": _steps([("a", 0, 1)])}, {}) is None
    assert motion_compare({}, {}) is None


def test_motion_compare_leaves_matching_none_when_nothing_moved_live() -> None:
    still = {"steps": _steps([("a", 0, 1)], [("a", 0, 1)])}
    assert motion_compare(still, still) == {"moving_live": 0, "moving_replay": 0, "matching": None}


def _png(width: int, height: int, pixel: Callable[[int, int], tuple[int, int, int]], *, filter_type: int = 0, alpha: bool = False) -> bytes:
    def raw(y: int) -> bytes:
        return b"".join(bytes(pixel(x, y)) + (b"\xff" if alpha else b"") for x in range(width))

    rows = bytearray()
    for y in range(height):
        row = raw(y)
        kind = filter_type if y > 0 else 0
        if kind == 2:  # Up: stored as the difference to the row above
            row = bytes((a - b) & 255 for a, b in zip(row, raw(y - 1), strict=True))
        elif kind == 1:  # Sub: stored as the difference to the pixel on the left
            step = 4 if alpha else 3
            row = bytes((row[i] - (row[i - step] if i >= step else 0)) & 255 for i in range(len(row)))
        rows += bytes([kind]) + row

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")


def test_pixel_share_is_1_for_equal_images_and_lower_for_a_changed_block() -> None:
    grey = _png(20, 10, lambda x, y: (100, 120, 140))
    assert pixel_share(grey, grey) == 1.0
    block = _png(20, 10, lambda x, y: (250, 120, 140) if x < 10 and y < 5 else (100, 120, 140))
    assert pixel_share(grey, block) == 0.75
    assert pixel_share(grey, _png(20, 10, lambda x, y: (116, 104, 140))) == 1.0  # 16 off on two channels: the same pixel
    assert pixel_share(grey, _png(20, 10, lambda x, y: (117, 120, 140))) == 0.0


def test_pixel_share_reads_filtered_and_alpha_pngs_and_refuses_other_sizes() -> None:
    def gradient(x: int, y: int) -> tuple[int, int, int]:
        return (x * 10, y * 20, 7)

    plain = _png(16, 8, gradient)
    for filtered in (_png(16, 8, gradient, filter_type=1), _png(16, 8, gradient, filter_type=2), _png(16, 8, gradient, alpha=True)):
        assert pixel_share(filtered, plain) == 1.0
    with pytest.raises(ValueError):
        pixel_share(plain, _png(8, 8, gradient))
    with pytest.raises(ValueError):
        pixel_share(b"not a png", plain)


# -- the verification as a run (the seam is a fake that visits the real replay server) ----------------------------------------------------

def _probe_result(words: int, *, foreign: list[str] | None = None, x: float = 0.0) -> dict[str, Any]:
    result: dict[str, Any] = {"height": 3600, "sections": 3, "elements": 40, "img": 1, "video": 0, "canvas": 0, "words": words,
                              "smooth_scroll": "native", "driver": "scroll", "shots": 0, "steps": _steps([("a", 0, 1)], [("a", x, 1)])}
    if foreign is not None:
        result["foreign_hosts"] = foreign
    return result


def _verify(workspace: Path, monkeypatch: pytest.MonkeyPatch, fake: Callable[[CheckContext, dict[str, Any]], tuple[bool, str]]) -> dict[str, Any]:
    prepared = mirror.mirror_dir(workspace)
    write_mirror({"out": str(prepared)})
    monkeypatch.setattr(mirror, "_verify_script", fake)
    return mirror._clone_mirror_verify(mirror.definition(url=URL).checks[1], record_ctx(workspace))


def _report(workspace: Path) -> MirrorReport:
    return MirrorReport.model_validate_json((mirror.mirror_dir(workspace) / "report.json").read_text(encoding="utf-8"))


def _probe_fake(words: Callable[[bool, int], int], *, foreign: list[str] | None = None, miss: bool = False,
                seen: list[tuple[bool, int]] | None = None) -> Callable[[CheckContext, dict[str, Any]], tuple[bool, str]]:
    def fake(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
        if config["script"] != "probe":
            return False, "no study in this test"
        replay = bool(config.get("watch_hosts"))
        if seen is not None:
            seen.append((replay, config["viewport"][0]))
        if replay:
            urllib.request.urlopen(config["url"]).read()
            if miss:
                with pytest.raises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(config["url"] + "never-recorded.js")
                assert caught.value.code == 404 and caught.value.headers["X-Mirror"] == "not-recorded"
        Path(config["out"]).parent.mkdir(parents=True, exist_ok=True)
        Path(config["out"]).write_text(json.dumps(_probe_result(words(replay, config["viewport"][0]), foreign=(foreign or []) if replay else None)),
                                       encoding="utf-8")
        return True, ""

    return fake


def test_the_report_says_what_it_does_not_cover(tmp_path, engine, monkeypatch) -> None:  # noqa: F811
    result = _verify(tmp_path, monkeypatch, _probe_fake(lambda replay, width: 50))
    assert result["passed"] is True and result["verdict"] == "MIRRORED"
    report = _report(tmp_path)
    assert report.not_covered == ["hover", "click", "other pages", "sound", "touch", "login", "what loads only after a long wait"]
    assert report.dna_match is None  # the study failed: not measured, not zero
    assert report.driver == "scroll" and report.seconds is not None
    assert report.pixels == [{"at": at, "within_16": None} for at in (0, 25, 50, 75, 100)]  # no screenshot in this test: not measured
    assert report.facts is not None and report.facts["1440"]["words"] == {"live": 50, "replay": 50, "equal": True}
    assert report.motion == {"moving_live": 0, "moving_replay": 0, "matching": None}


def test_a_blank_first_load_is_reloaded_once_and_both_loads_are_reported(tmp_path, engine, monkeypatch) -> None:  # noqa: F811
    seen: list[tuple[bool, int]] = []
    runs: list[int] = []

    def words(replay: bool, width: int) -> int:
        if replay:
            runs.append(width)
        return 0 if replay and runs == [1440] else 60  # the first replay load at 1440 is blank

    result = _verify(tmp_path, monkeypatch, _probe_fake(words, seen=seen))
    assert result["verdict"] == "MIRRORED"
    assert [s for s in seen if s[0]] == [(True, 1440), (True, 1440), (True, 390)]  # reloaded once, only the blank one
    report = _report(tmp_path)
    assert report.loads is not None
    assert [(x["width"], x["visible_words"]) for x in report.loads] == [(1440, 0), (1440, 60), (390, 60)]
    assert report.facts is not None and report.facts["1440"]["words"]["replay"] == 60  # the facts come from the reload


def test_a_leak_to_another_host_is_counted_and_listed(tmp_path, engine, monkeypatch) -> None:  # noqa: F811
    fake = _probe_fake(lambda replay, width: 60, foreign=["cdn.leak.test", "api.leak.test"], miss=True)
    assert _verify(tmp_path, monkeypatch, fake)["verdict"] == "MIRRORED"
    report = _report(tmp_path)
    assert report.requests == {"not_recorded": 2, "leaked_hosts": ["api.leak.test", "cdn.leak.test"]}  # one unrecorded request for each width


def test_a_replay_that_does_not_serve_the_document_is_a_failure_with_a_reason(tmp_path, engine, monkeypatch) -> None:  # noqa: F811
    result = _verify(tmp_path, monkeypatch, lambda ctx, config: (False, "the browser did not start"))
    assert result["passed"] is False and result["verdict"] == "MIRROR_FAILED"
    assert "document" in result["summary"]


def test_the_verify_check_is_registered_for_real() -> None:
    spec = checks.CHECKS["clone_mirror_verify"]
    assert spec.run is mirror._clone_mirror_verify and spec.listed is False


def test_a_blank_first_load_keeps_its_leaks_beside_those_of_the_reload(tmp_path, engine, monkeypatch) -> None:  # noqa: F811
    runs: list[int] = []

    def fake(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]:
        if config["script"] != "probe":
            return False, "skip"
        replay = bool(config.get("watch_hosts"))
        words, foreign = 60, None
        if replay:
            urllib.request.urlopen(config["url"]).read()
            runs.append(config["viewport"][0])
            if runs == [1440]:
                words, foreign = 0, ["first.leak.test"]
            elif runs == [1440, 1440]:
                foreign = ["second.leak.test", "first.leak.test"]
            else:
                foreign = []
        Path(config["out"]).parent.mkdir(parents=True, exist_ok=True)
        Path(config["out"]).write_text(json.dumps(_probe_result(words, foreign=foreign)), encoding="utf-8")
        return True, ""

    assert _verify(tmp_path, monkeypatch, fake)["verdict"] == "MIRRORED"
    assert _report(tmp_path).requests["leaked_hosts"] == ["first.leak.test", "second.leak.test"]
