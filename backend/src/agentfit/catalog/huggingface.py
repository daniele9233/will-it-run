"""Hugging Face Hub client: live model search and exact file metadata.

Uses the public Hub API (https://huggingface.co/docs/hub/api). No account or token is
required for public repositories.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from agentfit.branding import USER_AGENT
from agentfit.catalog import classify
from agentfit.catalog.models import Alignment, ModelEntry, ModelFile, hub_endpoint
from agentfit.fit.quant import bytes_per_param, parse_quant_from_filename
from agentfit.hardware.models import Confidence


class HubUnavailable(Exception):
    """The Hub could not be reached (offline, DNS failure, rate limit, timeout)."""


def _is_mmproj(filename: str) -> bool:
    return "mmproj" in filename.lower()


def _is_split_part(filename: str) -> bool:
    # "model-00002-of-00003.gguf": llama.cpp shards. Only the first shard represents the set.
    match = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", filename, re.IGNORECASE)
    return bool(match and match.group(1) != "00001")


def files_from_siblings(repo: str, siblings: list[dict[str, Any]], revision: str | None) -> tuple[list[ModelFile], list[ModelFile]]:
    """Split a repo's siblings into model files and vision projector files."""
    models: list[ModelFile] = []
    mmproj: list[ModelFile] = []
    shard_sizes: dict[str, int] = {}
    for sib in siblings:
        name = sib.get("rfilename") or ""
        if not name.lower().endswith(".gguf"):
            continue
        size = sib.get("size")
        lfs = sib.get("lfs") or {}
        shard = re.search(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$", name, re.IGNORECASE)
        if shard and size:
            shard_sizes[shard.group(1)] = shard_sizes.get(shard.group(1), 0) + int(size)
        if _is_split_part(name):
            continue
        entry = ModelFile(
            repo=repo,
            filename=name,
            quant=parse_quant_from_filename(name),
            size_bytes=int(size) if size else None,
            sha256=lfs.get("sha256"),
            revision=revision,
        )
        (mmproj if _is_mmproj(name) else models).append(entry)
    # A sharded model's size is the sum of its shards.
    for entry in models:
        shard = re.search(r"^(.*)-00001-of-\d{5}\.gguf$", entry.filename, re.IGNORECASE)
        if shard and shard.group(1) in shard_sizes:
            entry.size_bytes = shard_sizes[shard.group(1)]
            entry.sha256 = None
    models.sort(key=lambda f: f.size_bytes or 0)
    return models, mmproj


_AUXILIARY_NAME = re.compile(r"(mmproj|projector|vision-tower|clip-|draft|mtp-head|encoder-only|vocab-only)", re.IGNORECASE)
# A file smaller than this share of the expected size for its quantization cannot hold the
# whole model: it is a draft head, projector or other auxiliary tensor file.
_MIN_PLAUSIBLE_SHARE = 0.45


def drop_auxiliary_files(files: list[ModelFile], params_b: float | None) -> list[ModelFile]:
    """Remove GGUF files that are not a complete copy of the model weights."""
    kept: list[ModelFile] = []
    for file in files:
        if _AUXILIARY_NAME.search(file.filename):
            continue
        if params_b and file.size_bytes and file.quant:
            expected = params_b * bytes_per_param(file.quant) * 1024**3
            if file.size_bytes < expected * _MIN_PLAUSIBLE_SHARE:
                continue
        kept.append(file)
    return kept


def _license(info: dict[str, Any]) -> str | None:
    card = info.get("cardData") or {}
    value = card.get("license")
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    if value:
        return str(value)
    for tag in info.get("tags") or []:
        if tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return None


def _base_models(info: dict[str, Any]) -> list[str]:
    card = info.get("cardData") or {}
    value = card.get("base_model")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [t.split(":")[-1] for t in info.get("tags") or [] if t.startswith("base_model:") and t.count(":") == 1]


def entry_from_hub(info: dict[str, Any]) -> ModelEntry:
    """Convert a Hub model-info document into a catalog entry, recording what is unknown."""
    repo: str = info.get("id") or info.get("modelId") or ""
    tags: list[str] = list(info.get("tags") or [])
    gguf = info.get("gguf") or {}
    revision = info.get("sha")
    siblings = info.get("siblings") or []
    has_sizes = any(s.get("size") for s in siblings)
    files, mmproj = files_from_siblings(repo, siblings, revision)

    name_total, name_active = classify.parse_params_from_name(repo)
    params_b = gguf["total"] / 1e9 if gguf.get("total") else name_total
    params_disagree = bool(name_total and params_b and abs(params_b - name_total) / name_total > 0.30)
    if params_disagree:
        # The Hub reads GGUF metadata from one file, which may be an auxiliary one. Trust
        # the size the publisher put in the name and flag the disagreement.
        params_b = name_total
    # GGUF metadata counts vision-tower tensors too; a name-declared size is the better
    # figure when the two roughly agree, and marks MoE active parameters when present.
    is_moe = name_active is not None or "moe" in (gguf.get("architecture") or "").lower()
    files = drop_auxiliary_files(files, name_total or params_b)
    capabilities: list[str] = []
    pipeline = info.get("pipeline_tag")
    if pipeline in ("image-text-to-text", "visual-question-answering") or mmproj or "vision" in tags:
        capabilities.append("vision")
    template = gguf.get("chat_template") or ""
    if "tools" in template or "tool_call" in template or "function-calling" in tags or "tool-use" in tags:
        capabilities.append("tool_use")
    if "<think>" in template or "reasoning" in tags or "thinking" in tags:
        capabilities.append("reasoning")

    base_models = _base_models(info)
    alignment, evidence = classify.detect_alignment(repo, tags)
    for base in base_models:
        base_alignment, base_evidence = classify.detect_alignment(base)
        if base_alignment == Alignment.UNCENSORED:
            alignment = base_alignment
            evidence += [f"base model: {e}" for e in base_evidence]
    publisher = info.get("author") or repo.split("/", 1)[0]
    license_ = _license(info)
    context = gguf.get("context_length")
    architecture = gguf.get("architecture")
    unknown = [
        field
        for field, value in (
            ("license", license_),
            ("parameters", params_b),
            ("architecture", architecture),
            ("context_length", context),
            ("file sizes", has_sizes or not siblings),
        )
        if not value
    ]
    if params_disagree:
        unknown.append("parameters (name and GGUF metadata disagree)")
    quality_name = base_models[0] if base_models else repo
    base_publishers = {b.split("/", 1)[0] for b in base_models}
    return ModelEntry(
        repo=repo,
        name=repo.split("/", 1)[-1].removesuffix("-GGUF").removesuffix("-gguf"),
        publisher=publisher,
        base_models=base_models,
        quantizer=publisher if base_publishers and publisher not in base_publishers else None,
        family=classify.detect_family(repo),
        architecture=architecture,
        params_b=round(params_b, 3) if params_b else None,
        active_params_b=name_active,
        is_moe=is_moe,
        context_length=context,
        license=license_,
        capabilities=capabilities,
        categories=classify.detect_categories(repo, tags, pipeline, capabilities, params_b),
        alignment=alignment,
        alignment_evidence=list(dict.fromkeys(evidence)),
        downloads=info.get("downloads"),
        likes=info.get("likes"),
        release_date=(info.get("createdAt") or "")[:10] or None,
        last_modified=(info.get("lastModified") or "")[:10] or None,
        pipeline_tag=pipeline,
        tags=[t for t in tags if not t.startswith(("base_model:", "region:", "license:"))][:24],
        files=files,
        mmproj_files=mmproj,
        files_complete=bool(files) and has_sizes,
        revision=revision,
        source="huggingface",
        metadata_confidence=(
            Confidence.HIGH if not unknown else Confidence.MEDIUM if len(unknown) <= 2 else Confidence.LOW
        ),
        unknown_fields=unknown,
        quality=classify.estimate_task_quality(quality_name, params_b, name_active),
    )


class HuggingFaceClient:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.get(f"{hub_endpoint()}/api{path}", params=params)
        except httpx.HTTPError as exc:
            raise HubUnavailable(str(exc)) from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise HubUnavailable(f"Hugging Face returned HTTP {response.status_code}")
        return response.json()

    def search(self, text: str, limit: int = 30, sort: str = "downloads") -> list[ModelEntry]:
        """Search GGUF repositories. Results carry tags but not file sizes (see ``details``)."""
        params = {
            "search": text,
            "filter": "gguf",
            "sort": sort,
            "direction": "-1",
            "limit": str(limit),
            "full": "true",
        }
        data = self._get("/models", params) or []
        return [entry_from_hub(item) for item in data if item.get("id")]

    def details(self, repo: str) -> ModelEntry | None:
        """Full metadata including exact file names, sizes and SHA-256 hashes."""
        data = self._get(f"/models/{repo}", {"blobs": "true"})
        return entry_from_hub(data) if data else None

    def close(self) -> None:
        self._client.close()
