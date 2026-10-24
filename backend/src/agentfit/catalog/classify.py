"""Derive alignment, categories, size and task quality from model metadata.

Everything here is inference from names, tags and declared metadata. Each result
carries its evidence or a confidence level so the UI never presents a guess as fact.

Alignment is a *label*, never a score input: nothing in this module (or in scoring)
raises or lowers a model's rank because of it.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources

from agentfit.catalog.models import Alignment, TaskQuality
from agentfit.hardware.models import Confidence

# Terms the community uses for models with refusal behaviour removed or reduced.
UNCENSORED_TERMS = [
    "uncensored",
    "abliterated",
    "abliteration",
    "abliterate",
    "derestricted",
    "de-restricted",
    "unrestricted",
    "unfiltered",
    "decensored",
    "heretic",
    "lorablated",
    "noslop-uncensored",
]

_TERM_RE = re.compile("|".join(re.escape(t) for t in sorted(UNCENSORED_TERMS, key=len, reverse=True)), re.IGNORECASE)


def detect_alignment(name: str, tags: list[str] | None = None) -> tuple[Alignment, list[str]]:
    """Classify a model as standard or uncensored, returning the evidence found."""
    evidence: list[str] = []
    for match in _TERM_RE.finditer(name):
        evidence.append(f'name contains "{match.group(0).lower()}"')
    for tag in tags or []:
        if _TERM_RE.fullmatch(tag.strip()):
            evidence.append(f'tagged "{tag.lower()}"')
    evidence = list(dict.fromkeys(evidence))
    return (Alignment.UNCENSORED if evidence else Alignment.STANDARD), evidence


# -- size ---------------------------------------------------------------------

_PARAMS_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s?([BM])(?![A-Za-z0-9])", re.IGNORECASE)
_MOE_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)B-A(\d+(?:\.\d+)?)B(?![A-Za-z0-9])", re.IGNORECASE)
_MIX_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+)x(\d+(?:\.\d+)?)B(?![A-Za-z0-9])", re.IGNORECASE)


def parse_params_from_name(name: str) -> tuple[float | None, float | None]:
    """Return (total, active) parameters in billions parsed from a model name.

    "Qwen3.6-35B-A3B" -> (35, 3); "Mixtral-8x7B" -> (56, None); "gemma-3-270m" -> (0.27, None).
    """
    base = name.rsplit("/", 1)[-1]
    moe = _MOE_RE.search(base)
    if moe:
        return float(moe.group(1)), float(moe.group(2))
    mix = _MIX_RE.search(base)
    if mix:
        return float(mix.group(1)) * float(mix.group(2)), None
    candidates: list[float] = []
    for value, unit in _PARAMS_RE.findall(base):
        number = float(value)
        billions = number if unit.upper() == "B" else number / 1000
        if 0.05 <= billions <= 1000:
            candidates.append(billions)
    return (max(candidates) if candidates else None), None


# -- family -------------------------------------------------------------------

_FAMILIES = [
    "qwen", "deepseek", "llama", "gemma", "mistral", "mixtral", "phi", "granite", "devstral",
    "codestral", "starcoder", "glm", "kimi", "nemotron", "gpt-oss", "olmo", "hermes", "dolphin",
    "command-r", "aya", "smollm", "lfm", "yi", "internlm", "minicpm", "llava", "pixtral", "magnum",
    "medgemma", "biomistral", "falcon", "solar", "zephyr", "openchat", "wizardlm", "codellama",
]


def detect_family(name: str) -> str | None:
    lower = name.lower()
    for family in sorted(_FAMILIES, key=len, reverse=True):
        if family in lower:
            return family
    return None


# -- categories ---------------------------------------------------------------

_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Coding", ("coder", "code", "devstral", "codestral", "starcoder", "deepseek-coder", "codellama", "swe")),
    ("Reasoning", ("reason", "think", "-r1", "-r2", "qwq", "deepseek-r", "o1", "cot")),
    ("Vision", ("vision", "-vl", "vlm", "llava", "pixtral", "minicpm-v", "multimodal", "moondream")),
    ("Writing", ("writer", "story", "novel", "roleplay", "-rp", "creative", "magnum", "prose")),
    ("Translation", ("translate", "translation", "madlad", "nllb", "aya", "seamless")),
    ("Medical / Biomedical Research", ("med", "bio", "clinical", "health", "pubmed", "meditron")),
    ("Scientific Research", ("sci", "chem", "physics", "galactica")),
    ("Legal Analysis", ("legal", "law", "saul")),
    ("Embedding", ("embed", "bge-", "e5-", "gte-", "nomic-embed", "minilm")),
    ("Reranking", ("rerank", "reranker")),
    ("Computer Use", ("ui-tars", "computer-use", "cua", "gui-", "os-atlas", "showui")),
    ("DevOps", ("devops", "sre", "kubernetes", "k8s", "terraform")),
    ("Linux", ("linux", "shell", "bash")),
]

_WORD_BOUNDARY_TERMS = {"med", "bio", "sci", "law", "code", "think", "o1", "cot", "aya", "sre", "cua", "swe"}


def _has_term(lower: str, term: str) -> bool:
    if term in _WORD_BOUNDARY_TERMS:
        return re.search(rf"(?<![a-z]){re.escape(term)}", lower) is not None
    return term in lower


def detect_categories(
    name: str,
    tags: list[str] | None = None,
    pipeline_tag: str | None = None,
    capabilities: list[str] | None = None,
    params_b: float | None = None,
) -> list[str]:
    """A model may belong to several categories."""
    lower = name.lower()
    haystack = lower + " " + " ".join(t.lower() for t in tags or [])
    caps = set(capabilities or [])
    out: list[str] = []
    for category, terms in _CATEGORY_RULES:
        if any(_has_term(haystack if category != "Coding" else lower, term) for term in terms):
            out.append(category)
    if "vision" in caps or pipeline_tag in ("image-text-to-text", "visual-question-answering"):
        out.append("Vision")
    if pipeline_tag in ("feature-extraction", "sentence-similarity"):
        out.append("Embedding")
    is_special = any(c in out for c in ("Embedding", "Reranking"))
    if not is_special:
        out.append("General")
        # Mid-size and larger general models are usable for multi-step reasoning and prose.
        if (params_b or 0) >= 12 and "Reasoning" not in out:
            out.append("Reasoning")
        if (params_b or 0) >= 7 and "Writing" not in out:
            out.append("Writing")
        if "tool_use" in caps and (params_b or 0) >= 7 and "Coding" not in out:
            out.append("Coding")
    return list(dict.fromkeys(out))


# -- task quality -------------------------------------------------------------


@lru_cache(maxsize=1)
def _family_scores() -> list[tuple[str, dict[str, float]]]:
    raw = json.loads(resources.files("agentfit.catalog.data").joinpath("family_scores.json").read_text("utf-8"))
    table: list[tuple[str, dict[str, float]]] = []
    for family in raw["families"]:
        for needle in family["match"]:
            table.append((needle, family["scores"]))
    # Longest match wins so "qwen3.5-coder" beats "qwen3".
    return sorted(table, key=lambda t: -len(t[0]))


def _size_base(params_b: float) -> float:
    if params_b < 1:
        return 30
    if params_b < 3:
        return 45
    if params_b < 7:
        return 60
    if params_b < 10:
        return 72
    if params_b < 20:
        return 80
    if params_b < 40:
        return 87
    return 93


def estimate_task_quality(name: str, params_b: float | None, active_params_b: float | None = None) -> TaskQuality:
    """Estimate relative task strength.

    Size sets the baseline; a curated per-family table (relative strengths aggregated from
    public leaderboards) shifts it per task. MoE models are sized on active parameters.
    Unknown size means unknown quality: scores stay 0 with UNKNOWN confidence.
    """
    size = active_params_b or params_b
    if not size:
        return TaskQuality(basis="unknown", confidence=Confidence.UNKNOWN)
    # A MoE model is stronger than its active size alone suggests; blend toward the total.
    if active_params_b and params_b:
        size = (active_params_b * params_b) ** 0.5
    base = _size_base(size)
    lower = name.lower()
    family = next((scores for needle, scores in _family_scores() if needle in lower), None)

    def shifted(task: str) -> float:
        if not family:
            return base
        return max(0.0, min(100.0, base + (family[task] - 72.0) * 0.4))

    if family:
        coding, reasoning, chat = shifted("coding"), shifted("reasoning"), shifted("chat")
        basis, confidence = "family_table+size", Confidence.MEDIUM
    else:
        coding = base + (6 if any(t in lower for t in ("coder", "code", "starcoder")) else 0)
        reasoning = base + (5 if size >= 13 else 0)
        chat = base
        basis, confidence = "size_heuristic", Confidence.LOW
    return TaskQuality(
        coding=round(min(100.0, coding), 1),
        reasoning=round(min(100.0, reasoning), 1),
        writing=round(min(100.0, chat), 1),
        general=round(min(100.0, (coding + reasoning + chat) / 3), 1),
        basis=basis,
        confidence=confidence,
    )


def stars(score: float) -> int:
    """Map a 0-100 score to 0-5 stars for display."""
    if score <= 0:
        return 0
    return max(1, min(5, round((score - 35) / 12)))
