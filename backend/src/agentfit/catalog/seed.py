"""Offline seed catalog, derived from llmfit's model database (MIT, Copyright (c) Alex Jones).

Built by ``scripts/build_seed_catalog.py``. Seed entries name real Hugging Face
repositories but carry no file list: exact files, sizes and hashes are fetched live
before anything is shown as downloadable.
"""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from importlib import resources
from typing import Any

from agentfit.catalog import classify
from agentfit.catalog.models import ModelEntry
from agentfit.hardware.models import Confidence


def entry_from_seed(raw: dict[str, Any]) -> ModelEntry:
    base_name: str = raw["name"]
    repo: str = raw["gguf_repos"][0]
    params = raw.get("parameters")
    params_b = params / 1e9 if params else None
    active = raw.get("active_parameters")
    active_b = active / 1e9 if active else None
    name_total, name_active = classify.parse_params_from_name(base_name)
    params_disagree = False
    if params_b is None:
        params_b = name_total
    elif name_total and abs(params_b - name_total) / name_total > 0.30:
        # Scraped counts are sometimes off by an order of magnitude (e.g. gpt-oss-20b listed
        # as 1.8B). The size in the model's own name wins; keep the active/total ratio.
        params_disagree = True
        if active_b:
            active_b = active_b * name_total / params_b
        params_b = name_total
    if name_active and raw.get("is_moe"):
        active_b = name_active if active_b is None or params_disagree else active_b
    capabilities = list(raw.get("capabilities") or [])
    # Alignment evidence can sit in either the base name or the GGUF repo name.
    alignment, evidence = classify.detect_alignment(f"{base_name} {repo}")
    publisher = repo.split("/", 1)[0]
    base_publisher = base_name.split("/", 1)[0]
    unknown = [
        field
        for field, value in (
            ("license", raw.get("license")),
            ("parameters", params_b),
            ("architecture", raw.get("architecture")),
            ("context_length", raw.get("context_length")),
        )
        if not value
    ]
    if params_disagree:
        unknown.append("parameters (name and scraped count disagree)")
    return ModelEntry(
        repo=repo,
        name=repo.split("/", 1)[-1].removesuffix("-GGUF").removesuffix("-gguf"),
        publisher=publisher,
        base_models=[base_name] if base_name != repo else [],
        quantizer=publisher if publisher != base_publisher else None,
        family=classify.detect_family(base_name),
        architecture=raw.get("architecture"),
        params_b=round(params_b, 3) if params_b else None,
        nominal_params_b=name_total,
        active_params_b=round(active_b, 3) if active_b else None,
        is_moe=bool(raw.get("is_moe")),
        num_experts=raw.get("num_experts"),
        active_experts=raw.get("active_experts"),
        num_layers=raw.get("num_layers"),
        num_kv_heads=raw.get("num_kv_heads"),
        head_dim=raw.get("head_dim"),
        context_length=raw.get("context_length"),
        license=raw.get("license"),
        capabilities=capabilities,
        categories=classify.detect_categories(
            base_name, None, raw.get("pipeline_tag"), capabilities, params_b
        ),
        alignment=alignment,
        alignment_evidence=evidence,
        downloads=raw.get("downloads"),
        likes=raw.get("likes"),
        release_date=raw.get("release_date"),
        pipeline_tag=raw.get("pipeline_tag"),
        source="seed",
        metadata_confidence=Confidence.MEDIUM if len(unknown) <= 1 else Confidence.LOW,
        unknown_fields=unknown,
        quality=classify.estimate_task_quality(base_name, params_b, active_b),
    )


@lru_cache(maxsize=1)
def load_seed() -> tuple[ModelEntry, ...]:
    try:
        blob = resources.files("agentfit.catalog.data").joinpath("seed_catalog.json.gz").read_bytes()
    except FileNotFoundError:
        return ()
    raw = json.loads(gzip.decompress(blob).decode("utf-8"))
    return tuple(entry_from_seed(r) for r in raw)
