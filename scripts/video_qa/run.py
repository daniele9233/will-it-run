"""Sequential, resumable live QA against the existing authenticated Will It Run API."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import cv2
import httpx
from diagnose import analyze

SCENARIOS = {
    "scroll": "Locked camera. Slowly open the camera body into a precise mechanical exploded view. Preserve every component. End exactly at the ending photograph. No cuts.",
    "spin": "Rotate this entire rigid camera exactly 360 degrees on a turntable at constant speed. Locked camera, white background, fixed center and scale. Return exactly to the initial pose. No cuts.",
    "geometry": "Smooth camera orbit of 40 degrees around the stationary rigid camera. Strong parallax, consistent geometry, unchanged engraving, reflections and lighting. No cuts or deformation.",
    "stress": "Continuous smooth mechanical opening into the exact exploded arrangement in the ending photograph. Locked camera and lighting. Hold no still frames. No cuts, morphing or missing parts.",
}
PROMPT = ("Photorealistic professional product photograph of a closed precision silver and black "
          "camera with a detailed engraved lens, small screws, leather grip and realistic metal reflections. "
          "Centered, entire object visible with generous margins on a uniform white studio background. "
          "Three-quarter view, sharp focus, consistent soft studio lighting, 16:9 composition.")


def write(path: Path, data: dict) -> None:
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)


class Pipeline:
    def __init__(self, client: httpx.Client, root: Path, timeout: int):
        self.client, self.root, self.timeout = client, root, timeout

    def api(self, path: str, body: dict | None = None) -> dict:
        response = self.client.get(path) if body is None else self.client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    def wait(self, kind: str, job_id: str) -> dict:
        deadline = time.monotonic() + self.timeout
        last_status = None
        while time.monotonic() < deadline:
            if kind == "images":
                job = self.api(f"/api/images/jobs/{job_id}")["job"]
            else:
                job = next((j for j in self.api("/api/videos/status")["jobs"] if j["id"] == job_id), None)
                if job is None:
                    raise RuntimeError("Video job disappeared: backend restarted or job was evicted")
            current = (job["status"], job.get("stage", job.get("phase")))
            if current != last_status:
                print(kind, job_id, current, flush=True)
                last_status = current
            if job["status"] == "done":
                return job
            if job["status"] in ("failed", "cancelled"):
                raise RuntimeError(str(job.get("error") or job["status"]))
            time.sleep(5)
        self.api(f"/api/{kind}/jobs/{job_id}/cancel", {})
        # Do not submit anything else while cancellation might still be in progress.
        raise TimeoutError(f"{kind} job exceeded {self.timeout}s; cancellation requested, stopping suite")

    def download(self, path: str, target: Path) -> None:
        response = self.client.get(path)
        response.raise_for_status()
        target.write_bytes(response.content)

    def upload(self, path: Path) -> str:
        return self.api("/api/images/upload", {"filename": path.name,
                        "data": base64.b64encode(path.read_bytes()).decode()})["name"]

    def image(self, name: str, edit: str = "") -> Path:
        target = self.root / f"{name}.png"
        recipe = self.root / f"{name}.json"
        if target.exists() and recipe.exists():
            return target
        request = {"model_key": "qwen-image-2.1-uc-bf16", "width": 2048, "height": 1152,
                   "resolution": 2048, "steps": 50, "cfg": 2.5, "seed": 41234,
                   "template": "qwen_image_edit" if edit else "qwen_image_txt2img",
                   "prompt": ("Edit this exact camera photograph into a mechanically plausible exploded view: "
                              "separate lens, shell and internal components a short distance. "
                              "Keep camera viewpoint, center, white background, lighting and component identity unchanged."
                              if edit else PROMPT), "image": edit}
        if recipe.exists():
            record = json.loads(recipe.read_text(encoding="utf-8"))
        else:
            job = self.api("/api/images/generate", request)["job"]
            record = {"request": request, "job_id": job["id"]}
            write(recipe, record)
        job = self.wait("images", record["job_id"])
        self.download(f"/api/images/gallery/{job['image_id']}/file", target)
        record.update(job=job, sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        write(recipe, record)
        return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=Path(".shots/video-qa/run"))
    parser.add_argument("--timeout", type=int, default=10800)
    parser.add_argument("--stress-repeats", type=int, default=2)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {os.environ['AGENTFIT_TOKEN']}"} if os.getenv("AGENTFIT_TOKEN") else {}
    with httpx.Client(base_url=args.url, headers=headers, timeout=120, trust_env=False) as client:
        pipeline = Pipeline(client, args.output, args.timeout)
        report = {"started": datetime.now(UTC).isoformat(), "cases": [],
                  "quality": {"image": "Qwen 2.1 UC BF16 diffusion, INT8 encoder, PNG, 2048 resolution, 50 steps",
                              "video": "1280x704 native, 60 steps, interpolated/upscaled 1920x1080 30fps 8s",
                              "note": "Maximum quality is not a monotonic function of sampling steps."}}
        summary = args.output / "summary.json"
        try:
            video_status = pipeline.api("/api/videos/status")
            images = pipeline.api("/api/images/models")["models"]
            report["preflight"] = {"videos": video_status, "images": [m for m in images if m["key"] == "qwen-image-2.1-uc-bf16"]}
            report["plan"] = [{"scenario": scenario, "model": m["key"], "repeat": repeat}
                              for scenario in SCENARIOS for m in video_status["models"]
                              for repeat in range(args.stress_repeats if scenario == "stress" else 1)]
            write(summary, report)
            if args.preflight:
                return 0
            model = next(m for m in images if m["key"] == "qwen-image-2.1-uc-bf16")
            if not model["ready"]:
                raise RuntimeError("Requested BF16 image model is not ready; no quantized fallback")
            owned = {json.loads(p.read_text(encoding="utf-8")).get("job_id")
                     for p in args.output.glob("*/case.json")}
            if any(j["status"] in ("queued", "running") and j["id"] not in owned for j in video_status["jobs"]):
                raise RuntimeError("Video engine busy; refusing to collide with an existing job")
            origin = pipeline.image("origin")
            start_name = pipeline.upload(origin)
            end = pipeline.image("ending", edit=start_name)
            end_name = pipeline.upload(end)
            for scenario, prompt in SCENARIOS.items():
                for model in video_status["models"]:
                    for repeat in range(args.stress_repeats if scenario == "stress" else 1):
                        case = {"scenario": scenario, "model": model["key"], "repeat": repeat}
                        folder = args.output / f"{scenario}-{model['key']}-{repeat}"
                        folder.mkdir(exist_ok=True)
                        state = folder / "case.json"
                        if state.exists():
                            case = json.loads(state.read_text(encoding="utf-8"))
                        report["cases"].append(case)
                        if case.get("status") in ("passed_screening", "failed", "unsupported", "blocked"):
                            write(summary, report)
                            continue
                        if scenario == "geometry" and model["required_end"]:
                            case.update(status="unsupported", reason="Model requires a last image; not an I2V test")
                        elif not model["ready"]:
                            case.update(status="blocked", reason="Missing video model components or nodes")
                        else:
                            try:
                                if "job_id" not in case:
                                    request = {"model_key": model["key"], "image": start_name, "prompt": prompt,
                                               "width": 1280, "height": 720 if model["required_end"] else 704, "duration_seconds": 8,
                                               "motion_frames": 33 if model["required_end"] else None,
                                               "steps": 60, "seed": 41234 + repeat}
                                    if scenario != "geometry":
                                        request["end_image"] = start_name if scenario == "spin" else end_name
                                    case["request"] = request
                                    case["job_id"] = pipeline.api("/api/videos/generate", request)["id"]
                                    write(state, case)
                                job = pipeline.wait("videos", case["job_id"])
                                case["job"] = job
                                target = folder / "video.mp4"
                                pipeline.download(f"/api/videos/gallery/{case['job_id']}/file", target)
                                case["recipe"] = next((v for v in pipeline.api("/api/videos/gallery")["videos"]
                                                       if v["id"] == case["job_id"]), None)
                                diagnostic = analyze(target, folder, scenario="scroll" if scenario == "stress" else scenario,
                                                     first=origin, last=(origin if scenario == "spin" else end)
                                                     if scenario != "geometry" else None)
                                case.update(status=diagnostic["status"], failures=diagnostic["failures"])
                            except TimeoutError:
                                raise
                            except (RuntimeError, ValueError, OSError, KeyError, httpx.HTTPError, subprocess.SubprocessError, cv2.error) as exc:
                                case.update(status="failed", error=str(exc))
                        write(state, case)
                        write(summary, report)
                        print(case["scenario"], case["model"], case["status"], flush=True)
            report["status"] = "completed" if all(c["status"] == "passed_screening" for c in report["cases"]) else "completed_with_failures_or_gaps"
        except (RuntimeError, ValueError, OSError, KeyError, httpx.HTTPError, subprocess.SubprocessError, cv2.error) as exc:
            report.update(status="blocked", error=str(exc))
            print(type(exc).__name__, str(exc), flush=True)
        write(summary, report)
        return int(report.get("status") != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
