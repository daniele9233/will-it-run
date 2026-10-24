"""Build Will It Run's offline seed catalog from llmfit's model database.

Usage:
    python scripts/build_seed_catalog.py <path-to-llmfit>/llmfit-core/data/hf_models.json

The source data is part of llmfit (https://github.com/AlexsJones/llmfit), MIT License,
Copyright (c) Alex Jones. Only GGUF-runnable text/vision models are kept, and only the
fields Will It Run's fit engine uses. The output is a gzip-compressed JSON list written to
backend/src/agentfit/catalog/data/seed_catalog.json.gz.

The seed is a starting point for offline use. Exact file names, sizes and hashes are
always fetched live from Hugging Face before anything is downloaded.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "backend/src/agentfit/catalog/data/seed_catalog.json.gz"

SKIP_CAPABILITIES = {"audio", "tts"}
SKIP_PIPELINES = {"text-to-speech", "automatic-speech-recognition", "text-to-audio", "audio-to-audio"}
MIN_PARAMS = 400_000_000
# Keep every model at or above this popularity, plus all that already list a GGUF source.
MIN_DOWNLOADS = 2_000

KEEP = {
    "provider": "publisher",
    "parameters_raw": "parameters",
    "context_length": "context_length",
    "architecture": "architecture",
    "license": "license",
    "hf_downloads": "downloads",
    "hf_likes": "likes",
    "release_date": "release_date",
    "pipeline_tag": "pipeline_tag",
    "num_hidden_layers": "num_layers",
    "num_key_value_heads": "num_kv_heads",
    "head_dim": "head_dim",
    "is_moe": "is_moe",
    "active_parameters": "active_parameters",
    "num_experts": "num_experts",
    "active_experts": "active_experts",
}


def is_gguf_repo(name: str) -> bool:
    return name.lower().endswith(("-gguf", "_gguf", ".gguf")) or "-gguf-" in name.lower()


def convert(entry: dict) -> dict | None:
    caps = set(entry.get("capabilities") or [])
    if caps & SKIP_CAPABILITIES or entry.get("pipeline_tag") in SKIP_PIPELINES:
        return None
    if entry.get("format") != "gguf":
        return None
    params = entry.get("parameters_raw") or 0
    if params < MIN_PARAMS:
        return None
    name = entry["name"]
    sources = [s["repo"] for s in entry.get("gguf_sources") or [] if s.get("repo")]
    if not sources and is_gguf_repo(name):
        sources = [name]
    if not sources:
        return None
    if not entry.get("gguf_sources") and (entry.get("hf_downloads") or 0) < MIN_DOWNLOADS:
        return None
    out: dict = {"name": name, "gguf_repos": sources[:4], "capabilities": sorted(caps)}
    for src, dst in KEEP.items():
        value = entry.get(src)
        if value not in (None, "", [], False):
            out[dst] = value
    return out


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    source = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    seen: set[str] = set()
    out: list[dict] = []
    for entry in source:
        converted = convert(entry)
        if not converted:
            continue
        key = converted["gguf_repos"][0].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(converted)
    out.sort(key=lambda e: -(e.get("downloads") or 0))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with gzip.open(OUT, "wb", compresslevel=9) as fh:
        fh.write(payload)
    flagged = sum(
        1 for e in out if any(t in e["name"].lower() for t in ("uncensored", "abliterat", "derestrict", "heretic"))
    )
    print(f"source entries : {len(source)}")
    print(f"seed entries   : {len(out)}  ({flagged} uncensored/abliterated by name)")
    print(f"json size      : {len(payload) / 1e6:.2f} MB -> gz {OUT.stat().st_size / 1e6:.2f} MB")
    print(f"written        : {OUT}")
    for e in out[:5]:
        print("  ", e["name"], "->", e["gguf_repos"][0], e.get("downloads"))


if __name__ == "__main__":
    main()
