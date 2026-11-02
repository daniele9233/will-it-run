"""Explicit real-GPU TRELLIS.2 smoke test; not part of pytest or CI."""
from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

from agentfit.imagegen.trellis import TrellisRequest, TrellisStudio


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, help="A PNG reference, extracted from a video if needed")
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    studio = TrellisStudio(args.output)
    (args.output / "runtime.json").write_text(json.dumps({"distribution": args.distribution}), encoding="utf-8")
    status = studio.status()
    print(json.dumps({"runtime": status}, ensure_ascii=False), flush=True)
    if not status["ready"]:
        return 2
    image = studio.upload(base64.b64encode(args.image.read_bytes()).decode())
    job = studio.generate(TrellisRequest(image=image, seed=1, steps=24, resolution=1536, texture_size=4096))
    print(f"Generating {job['id']} locally…", flush=True)
    while (job := studio.job()) and job["status"] == "running":
        time.sleep(2)
    print(json.dumps(job, ensure_ascii=False), flush=True)
    if not job or job["status"] != "completed":
        return 1
    print(json.dumps(studio.gallery(), ensure_ascii=False), flush=True)
    print(studio.file(job["id"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
