"""The Arena's data: questions, answers, and the verdicts verifiers give them.

A verdict says how an answer fared and *why*, in words that are translated when they are read, not when they are
written: the reason is an English template and its values, kept in the database as they are.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# Why an answer failed. Only a failure has a kind, and a score of zero.
WRONG = "wrong"  # an answer was given and it is not the right one
FORMAT = "format"  # no answer could be read: the format asked for was not followed
FABRICATED = "fabricated"  # it made something up where it should have said it does not know
REFUSED = "refused"  # it said it does not know a thing it should have known
TRUNCATED = "truncated"  # it ran out of tokens before it got there
FAIL_KINDS = (WRONG, FORMAT, FABRICATED, REFUSED, TRUNCATED)
# Why an answer was not scored: it needs a person (``manual``), or a tool this PC does not have (``tool``).
MANUAL = "manual"
TOOL = "tool"
TECH = "tech"  # the run itself broke (the server answered an error): not the model's fault
CAPABILITY = "capability"  # the model cannot do what the question needs (it cannot see pictures): skipped, not failed

STATUSES = ("passed", "partial", "failed", "unscored", "skipped", "error")


@dataclass(frozen=True)
class Reason:
    """A sentence with its values: the English template is also the key of the Italian catalog."""

    t: str
    a: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        from agentfit.i18n import tr

        # A value that is itself words («Words», «at least») is marked with ``tl`` and translated here, with the sentence.
        values = {k: (tr(v["tr"]) if isinstance(v, dict) and set(v) == {"tr"} else v) for k, v in self.a.items()}
        return tr(self.t, **values)

    def to_json(self) -> dict[str, Any]:
        return {"t": self.t, "a": self.a}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Reason:
        return cls(str(data.get("t", "")), dict(data.get("a") or {}))


def tl(text: str) -> dict[str, str]:
    """A value of a reason that is words and not a name or a number: it is translated when the reason is read."""
    return {"tr": text}


def why(template: str, /, **args: Any) -> Reason:
    """``why("Expected {expected}, found {found}.", expected=1, found=2)``. The template is what gets translated."""
    return Reason(template, args)


@dataclass
class Check:
    """One thing a verifier looked at, with what it found: shown as a list under the verdict."""

    ok: bool
    reason: Reason

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, **self.reason.to_json()}


@dataclass
class Verdict:
    status: str
    score: float | None
    kind: str | None = None
    reason: Reason | None = None
    # What the verifier took the answer to be, and what it expected: both as text a person can read.
    extracted: Any = None
    expected: Any = None
    checks: list[Check] = field(default_factory=list)
    # What decided it: "match", "execution", "terraform apply", "structure"... Two runs are comparable only if it is the same.
    method: str = ""
    # False when the answer was readable but not in the format that was asked for.
    format_ok: bool = True

    @classmethod
    def passed(cls, **kw: Any) -> Verdict:
        return cls("passed", 1.0, **kw)

    @classmethod
    def failed(cls, kind: str, reason: Reason, **kw: Any) -> Verdict:
        return cls("failed", 0.0, kind, reason, **kw)

    @classmethod
    def partial(cls, score: float, reason: Reason, **kw: Any) -> Verdict:
        if not 0.0 < score < 1.0:
            raise ValueError("a partial score lies strictly between 0 and 1")
        return cls("partial", round(score, 4), None, reason, **kw)

    @classmethod
    def unscored(cls, kind: str, reason: Reason, **kw: Any) -> Verdict:
        return cls("unscored", None, kind, reason, **kw)

    @classmethod
    def error(cls, reason: Reason, **kw: Any) -> Verdict:
        return cls("error", None, TECH, reason, **kw)

    @classmethod
    def skipped(cls, reason: Reason, **kw: Any) -> Verdict:
        return cls("skipped", None, CAPABILITY, reason, **kw)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status, "score": self.score, "kind": self.kind,
            "reason": self.reason.to_json() if self.reason else None,
            "extracted": self.extracted, "expected": self.expected,
            "checks": [c.to_json() for c in self.checks], "method": self.method, "format_ok": self.format_ok,
        }


@dataclass
class ToolCall:
    name: str
    # The arguments as the model wrote them, parsed. None when what it wrote is not JSON.
    arguments: dict[str, Any] | None
    raw: str = ""


@dataclass
class Answer:
    """What a model replied, with its reasoning set apart."""

    text: str
    raw: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass(frozen=True)
class Image:
    name: str
    data_b64: str
    width: int
    height: int
    # SHA-256 of the pixels, not of the PNG file: the file depends on the zlib version, the picture does not.
    pixels_sha256: str

    @property
    def data_url(self) -> str:
        return f"data:image/png;base64,{self.data_b64}"


@dataclass(frozen=True)
class Attachment:
    """A file the question is about, shown to the person (a PDF); the model is given its text in the prompt."""

    name: str
    mime: str
    data_b64: str
    sha256: str
    pages: int = 1


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    title: str
    lang: str
    prompt: str
    check: dict[str, Any]
    expected: str
    reference: str
    system: str | None = None
    # A whole conversation instead of the single prompt (a tool result to read, for one).
    messages: tuple[dict[str, Any], ...] | None = None
    tools: tuple[dict[str, Any], ...] | None = None
    response_format: dict[str, Any] | None = None
    images: tuple[Image, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    max_tokens: int = 700
    # 1 = in every pack, 2 = in standard and complete, 3 = complete only.
    tier: int = 2
    difficulty: int = 2
    needs: tuple[str, ...] = ()
    # Answers that must be judged wrong: the verifier is tested against them.
    wrong: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Question:
        """A question as a run stored it (``to_json`` with its assets). The wrong answers the bank tests its verifiers with are not kept."""
        return cls(
            id=data["id"], category=data["category"], title=data["title"], lang=data.get("lang", "it"), prompt=data["prompt"], check=data["check"],
            expected=data.get("expected", ""), reference=data.get("reference", ""), system=data.get("system"),
            messages=tuple(data["messages"]) if data.get("messages") is not None else None,
            tools=tuple(data["tools"]) if data.get("tools") else None, response_format=data.get("response_format"),
            images=tuple(Image(name=i["name"], data_b64=i["data_b64"], width=i["width"], height=i["height"], pixels_sha256=i["sha256"]) for i in data.get("images", [])),
            attachments=tuple(Attachment(name=a["name"], mime=a["mime"], data_b64=a["data_b64"], sha256=a["sha256"], pages=a.get("pages", 1)) for a in data.get("attachments", [])),
            max_tokens=data.get("max_tokens", 700), tier=data.get("tier", 2), difficulty=data.get("difficulty", 2), needs=tuple(data.get("needs", [])), tags=tuple(data.get("tags", [])),
        )

    def chat_messages(self) -> list[dict[str, Any]]:
        """The messages sent to every model, exactly as they are here."""
        if self.messages is not None:
            return [dict(m) for m in self.messages]
        out: list[dict[str, Any]] = []
        if self.system:
            out.append({"role": "system", "content": self.system})
        if self.images:
            content: list[dict[str, Any]] = [{"type": "text", "text": self.prompt}]
            content += [{"type": "image_url", "image_url": {"url": image.data_url}} for image in self.images]
            out.append({"role": "user", "content": content})
        else:
            out.append({"role": "user", "content": self.prompt})
        return out

    def fingerprint(self) -> dict[str, Any]:
        """Everything that makes two questions the same question, with pictures and files as hashes."""
        return {
            "id": self.id, "category": self.category, "lang": self.lang, "system": self.system, "prompt": self.prompt,
            "messages": list(self.messages) if self.messages is not None else None,
            "tools": list(self.tools) if self.tools else None, "response_format": self.response_format,
            "images": [i.pixels_sha256 for i in self.images], "attachments": [a.sha256 for a in self.attachments],
            "max_tokens": self.max_tokens, "tier": self.tier, "needs": list(self.needs), "check": self.check,
            "expected": self.expected,
        }

    def to_json(self, *, with_assets: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id, "category": self.category, "title": self.title, "lang": self.lang, "prompt": self.prompt,
            "system": self.system, "messages": list(self.messages) if self.messages is not None else None,
            "tools": list(self.tools) if self.tools else None, "response_format": self.response_format,
            "max_tokens": self.max_tokens, "tier": self.tier, "difficulty": self.difficulty, "needs": list(self.needs),
            "expected": self.expected, "reference": self.reference, "check": self.check, "tags": list(self.tags),
            "images": [{"name": i.name, "width": i.width, "height": i.height, "sha256": i.pixels_sha256, **({"data_b64": i.data_b64} if with_assets else {})} for i in self.images],
            "attachments": [{"name": a.name, "mime": a.mime, "pages": a.pages, "sha256": a.sha256, **({"data_b64": a.data_b64} if with_assets else {})} for a in self.attachments],
        }
        return data


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
