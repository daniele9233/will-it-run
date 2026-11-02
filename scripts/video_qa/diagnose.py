"""Offline diagnostics. No GPU, models or network; thresholds are screening heuristics."""
from __future__ import annotations

import argparse
import json
import subprocess
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
from skimage.metrics import structural_similarity


def command(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True, timeout=1800).stdout


def compare(a: np.ndarray, b: np.ndarray) -> dict:
    resized = a.shape != b.shape
    if resized:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_LANCZOS4)
    return {"mse": float(np.mean((a.astype(float) - b.astype(float)) ** 2)),
            "ssim": float(structural_similarity(a, b, channel_axis=2, data_range=255)),
            "exact_pixels": bool(np.array_equal(a, b)), "reference_resized": resized}


def foreground(frame: np.ndarray) -> dict | None:
    # Neutral-background fixture only: reflections and shadows can bias this measurement.
    border = np.concatenate((frame[0], frame[-1], frame[:, 0], frame[:, -1]))
    bg = np.median(border.astype(float), axis=0)
    mask = (np.linalg.norm(frame.astype(float) - bg, axis=2) > 35).astype(np.uint8)
    count, _labels, stats, centers = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[i]
    return {"center": (centers[i] / [frame.shape[1], frame.shape[0]]).tolist(),
            "area": float(area / mask.size),
            "clipped": bool(x == 0 or y == 0 or x + w == frame.shape[1] or y + h == frame.shape[0])}


def analyze(video: Path, output: Path, *, scenario: str, first: Path | None = None,
            last: Path | None = None, export_count: int | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    probe = json.loads(command(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-show_streams", "-show_frames", "-of", "json", str(video)]))
    stream = probe["streams"][0]
    pts = [float(f["best_effort_timestamp_time"]) for f in probe["frames"]
           if "best_effort_timestamp_time" in f]
    # Strict full-stream decode, independently from OpenCV's sometimes silent early EOF.
    command(["ffmpeg", "-v", "error", "-xerror", "-i", str(video), "-map", "0:v:0",
             "-f", "null", "-"])
    capture = cv2.VideoCapture(str(video))
    previous = initial = penultimate = final = None
    black, duplicate, flicker, geometry, centers = [], [], [], [], []
    tracks = None
    initial_tracks = 0
    count = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if initial is None:
            initial = frame.copy()
        penultimate, final = final, frame.copy()
        small = cv2.resize(frame, (640, 360))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if float(np.mean(gray < 8)) > 0.98:
            black.append(count)
        if scenario == "spin":
            centers.append(foreground(small))
        if previous is not None:
            if np.array_equal(gray, previous):
                duplicate.append(count)
            delta = abs(float(gray.mean()) - float(previous.mean()))
            if delta > 20:
                flicker.append({"frame": count, "luma_delta": delta})
            if scenario == "geometry" and tracks is not None and len(tracks):
                moved, status, _ = cv2.calcOpticalFlowPyrLK(previous, gray, tracks, None)
                back, reverse, _ = cv2.calcOpticalFlowPyrLK(gray, previous, moved, None)
                keep = ((status.ravel() == 1) & (reverse.ravel() == 1)
                        & (np.linalg.norm(back - tracks, axis=2).ravel() < 1.5)
                        & np.isfinite(moved).all(axis=(1, 2)))
                p, q = tracks[keep].reshape(-1, 2), moved[keep].reshape(-1, 2)
                inliers = None
                if len(p) >= 8:
                    _, mask = cv2.findFundamentalMat(p, q, cv2.FM_RANSAC, 1.5, 0.99)
                    if mask is not None:
                        inliers = float(mask.mean())
                tracks = moved[keep]
                geometry.append({"frame": count, "surviving_initial_tracks": len(tracks),
                                 "epipolar_inlier_ratio": inliers})
        elif scenario == "geometry":
            tracks = cv2.goodFeaturesToTrack(gray, 1500, 0.01, 8)
            initial_tracks = len(tracks) if tracks is not None else 0
        previous = gray
        count += 1
    capture.release()
    if count < 2:
        raise ValueError("Video has fewer than two decoded frames")
    fps = float(Fraction(stream["avg_frame_rate"]))
    duration = float(stream["duration"])
    failures = []
    if (stream["width"], stream["height"]) != (1920, 1080):
        failures.append("resolution")
    if abs(duration - 8) > 1 / 30 or count != 240 or abs(fps - 30) > 0.01:
        failures.append("duration/fps/frame_count")
    if len(pts) != count or any(b <= a or abs(b - a - 1 / 30) > 0.005 for a, b in pairwise(pts)):
        failures.append("timestamps/dropped_frames")
    endpoints = {}
    for name, reference, actual in [("first", first, initial), ("last", last, final)]:
        if reference:
            image = cv2.imread(str(reference))
            if image is None:
                raise ValueError(f"Cannot read reference: {reference}")
            endpoints[name] = compare(actual, image)
            if endpoints[name]["ssim"] < 0.95:
                failures.append(f"{name}_endpoint")
    if black or duplicate or flicker:
        failures.append("temporal_screening")
    report = {"scenario": scenario, "decoded_frames": count, "fps": fps, "duration": duration,
              "width": stream["width"], "height": stream["height"], "endpoints": endpoints,
              "black_frames": black, "duplicate_frames_at_640x360": duplicate,
              "flicker_candidates": flicker, "failures": failures,
              "thresholds": {"endpoint_ssim": 0.95, "flicker_luma": 20,
                             "flow_forward_backward_pixels": 1.5},
              "geometry": {"initial_tracks": initial_tracks, "pairs": geometry,
                           "survival_ratio": (len(tracks) / initial_tracks if initial_tracks else None),
                           "colmap": "not_evaluated", "reconstruction": "not_evaluated"}}
    if scenario == "geometry":
        survival = report["geometry"]["survival_ratio"]
        if survival is None or survival < 0.15:
            failures.append("geometry_track_survival")
    if scenario == "spin":
        valid = [c for c in centers if c is not None]
        report["loop_penultimate_to_first"] = compare(initial, penultimate)
        report["loop_last_to_first"] = compare(initial, final)
        report["foreground"] = {"samples": centers, "method": "largest neutral-background component"}
        if len(valid) != count:
            failures.append("foreground_not_measurable")
        if valid:
            drift = float(np.max(np.linalg.norm(np.array([c["center"] for c in valid]) - valid[0]["center"], axis=1)))
            scale = max(c["area"] for c in valid) / max(min(c["area"] for c in valid), 1e-9)
            report["foreground"].update(center_drift=drift, area_ratio=scale)
            if drift > 0.1 or scale > 1.5 or any(c["clipped"] for c in valid):
                failures.append("spin_center_scale_clipping")
        if report["loop_penultimate_to_first"]["ssim"] < 0.95:
            failures.append("loop_seam")
    n = export_count or (120 if scenario == "spin" else 300)
    frames_dir = output / "frames"
    frames_dir.mkdir(exist_ok=True)
    if list(frames_dir.iterdir()):
        raise ValueError("Frame export directory must be empty (avoid stale frames)")
    # Uniformly resample for the browser; repeated samples do NOT count as generated motion.
    command(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={n}/{duration}",
             "-frames:v", str(n), "-c:v", "libwebp", "-quality", "90", "-compression_level", "6",
             str(frames_dir / "%04d.webp")])
    files = sorted(frames_dir.glob("*.webp"))
    if len(files) != n:
        raise ValueError(f"Expected {n} browser frames, got {len(files)}")
    report["browser_frames"] = {"count": n, "source_frames": count, "resampled": n != count}
    report["status"] = "failed" if failures else "passed_screening"
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps({"scenario": scenario,
        "frames": [f"frames/{p.name}" for p in files]}), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--scenario", choices=["scroll", "spin", "geometry"], required=True)
    parser.add_argument("--first", type=Path)
    parser.add_argument("--last", type=Path)
    args = parser.parse_args()
    report = analyze(args.video, args.output, scenario=args.scenario, first=args.first, last=args.last)
    print(json.dumps({"status": report["status"], "failures": report["failures"]}))
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
