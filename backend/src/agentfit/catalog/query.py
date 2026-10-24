"""Parse a free-text model search into structured filters.

"best uncensored model for 16GB VRAM" -> alignment=uncensored, vram_gb=16, rank by fit.
"Qwen uncensored 27B"                 -> terms=[qwen], alignment=uncensored, params=27.
"coding abliterated"                  -> categories=[Coding], alignment=uncensored.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agentfit.catalog.classify import UNCENSORED_TERMS
from agentfit.catalog.models import Alignment

_STOPWORDS = {
    "best", "top", "good", "great", "strongest", "model", "models", "llm", "llms", "for", "with", "the",
    "a", "an", "on", "my", "pc", "computer", "gpu", "vram", "ram", "gb", "of", "that", "fits", "fit",
    "run", "runs", "running", "and", "or", "to", "in", "local", "me", "find", "show", "recommended",
    "recommend", "which", "what", "can", "i", "is", "are", "installed", "any",
}

_CATEGORY_WORDS: dict[str, str] = {
    "coding": "Coding", "code": "Coding", "coder": "Coding", "programming": "Coding",
    "reasoning": "Reasoning", "thinking": "Reasoning",
    "vision": "Vision", "multimodal": "Vision", "image": "Vision",
    "writing": "Writing", "creative": "Writing", "roleplay": "Writing", "story": "Writing",
    "translation": "Translation", "translate": "Translation",
    "medical": "Medical / Biomedical Research", "biomedical": "Medical / Biomedical Research",
    "legal": "Legal Analysis", "embedding": "Embedding", "embeddings": "Embedding",
    "rerank": "Reranking", "reranking": "Reranking", "devops": "DevOps", "linux": "Linux",
    "tool": "Tool Use", "tools": "Tool Use",
}

_STANDARD_WORDS = {"standard", "aligned", "censored", "official", "safe"}
_VRAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*gb(?:\s*(?:of\s*)?(vram|ram|gpu))?", re.IGNORECASE)
_PARAMS_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*b(?![\w])", re.IGNORECASE)


class ParsedQuery(BaseModel):
    raw: str = ""
    terms: list[str] = Field(default_factory=list)
    alignment: Alignment | None = None
    categories: list[str] = Field(default_factory=list)
    params_b: float | None = None
    vram_gb: float | None = None
    wants_best: bool = False
    installed_only: bool = False
    tool_use: bool = False

    def hub_queries(self) -> list[str]:
        """Text queries to send to the Hub. Uncensored searches try each community term."""
        base = list(self.terms)
        if self.params_b:
            size = f"{self.params_b:g}b"
            if size not in base:
                base.append(size)
        if not base:
            for category in self.categories:
                if category == "Coding":
                    base.append("coder")
                elif category == "Vision":
                    base.append("vision")
        if self.alignment == Alignment.UNCENSORED:
            return [" ".join([*base, term]) for term in ("abliterated", "uncensored", "heretic")]
        return [" ".join(base)] if base else []


def parse_query(text: str) -> ParsedQuery:
    raw = text.strip()
    lowered = raw.lower()
    parsed = ParsedQuery(raw=raw)

    vram = _VRAM_RE.search(lowered)
    if vram and (vram.group(2) in ("vram", "gpu") or "vram" in lowered):
        parsed.vram_gb = float(vram.group(1))
    lowered = _VRAM_RE.sub(" ", lowered)

    params = _PARAMS_RE.search(lowered)
    if params:
        parsed.params_b = float(params.group(1))
        lowered = _PARAMS_RE.sub(" ", lowered, count=1)

    for token in re.findall(r"[a-z0-9][a-z0-9._+-]*", lowered):
        if token in UNCENSORED_TERMS or token in ("nsfw-uncensored", "abliterate"):
            parsed.alignment = Alignment.UNCENSORED
        elif token in _STANDARD_WORDS:
            parsed.alignment = Alignment.STANDARD
        elif token in _CATEGORY_WORDS:
            category = _CATEGORY_WORDS[token]
            if category == "Tool Use":
                parsed.tool_use = True
            elif category not in parsed.categories:
                parsed.categories.append(category)
        elif token in ("best", "top", "strongest", "recommended", "recommend"):
            parsed.wants_best = True
        elif token == "installed":
            parsed.installed_only = True
        elif token in _STOPWORDS or token == "use":
            continue
        else:
            parsed.terms.append(token)
    return parsed
