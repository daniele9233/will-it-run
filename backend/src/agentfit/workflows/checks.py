"""Deterministic checks: workflow steps that are code, not a model.

A model can be persuaded, can misremember and can invent. A check cannot: it reads the
run's state and applies a fixed rule, so what it lets through does not depend on how
convincing an agent sounded. Every check fails closed: what it cannot verify is dropped,
and a check that ends the run does so with a named verdict instead of a best guess.

What the checks here establish, and what they do not:

* A quote is *verbatim*: the same words, in the same order, exist in the evidence. That
  proves the model did not make the quote up. It does not prove the quote means what the
  claim says; that judgement stays with the auditing agent.
* A hash is SHA-256 over the canonical JSON of an output. It shows that what a later step
  uses is byte-for-byte what an earlier step produced or reviewed. It is not a signature:
  everything runs in one process on one machine, and no keys are involved.
* A tally counts votes with fixed weights and a fixed threshold. The threshold is a
  setting, not a statistically calibrated value.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentfit.workflows.dsl import CheckDef, WorkflowDef

# -- hashing ------------------------------------------------------------------


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def bind_test_verdict(output: dict[str, Any], exit_code: int, closing_line: str) -> dict[str, Any]:
    """Make a test report's ``passed`` say what the last run of the suite said.

    An agent that ran the tests still has to conclude, and it can conclude wrongly in either
    direction: "passed" over a failing run, or "failed" over 33 green tests because another
    role had asked for more. The exit code of the suite does neither. ``passed`` is set
    from it, and ``evidence`` records the run and whether the agent had said otherwise.
    What else the agent found stays in its report, for the reviewer to judge.
    """
    passed = exit_code == 0
    evidence = f"Last run of the test suite: exit code {exit_code}" + (f" ({closing_line})" if closing_line else "") + "."
    if exit_code == -1:
        evidence = f"The last run of the test suite did not finish: {closing_line or 'no output'}."
    said = output.get("passed")
    if isinstance(said, bool) and said is not passed:
        evidence += f" The agent answered passed={str(said).lower()}; the verdict follows the test run."
    return {**output, "passed": passed, "evidence": evidence}


# Lists whose items get an engine-written id: field name -> (prefix, fields the id is made of).
_ID_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "claims": ("c", ("statement", "quote", "source")),
    "propositions": ("p", ("statement",)),
}


def stamp_ids(output: dict[str, Any]) -> dict[str, Any]:
    """Give every claim and proposition an id derived from its own content.

    Whatever the model wrote in ``id`` is replaced, and exact duplicates are dropped, so
    an id always names one specific statement and cannot be reused for another.
    """
    for key, (prefix, parts) in _ID_FIELDS.items():
        items = output.get(key)
        if not isinstance(items, list):
            continue
        seen: set[str] = set()
        stamped = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = f"{prefix}-{digest([str(item.get(p, '')).strip() for p in parts])[:8]}"
            if item_id not in seen:
                seen.add(item_id)
                stamped.append({**item, "id": item_id})
        output[key] = stamped
    return output


# -- verbatim quotes ----------------------------------------------------------

MIN_QUOTE_CHARS = 12
# Typographic quotes and dashes, written as escapes so the file stays unambiguous to read.
_PUNCT = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00ab": '"', "\u00bb": '"', "\u2013": "-", "\u2014": "-"})
_ELLIPSIS = re.compile(r"\s*(?:\[\s*\.\.\.\s*\]|\[\s*\u2026\s*\]|\.\.\.|\u2026)\s*")


def normalize(text: str) -> str:
    """Case, spacing and typographic quotes or dashes do not count; the words and their order do."""
    text = unicodedata.normalize("NFKC", text).translate(_PUNCT).casefold()
    return re.sub(r"\s+", " ", text).strip()


def find_quote(quote: str, documents: list[dict[str, Any]], prefer: str = "", min_chars: int = MIN_QUOTE_CHARS) -> tuple[str | None, str]:
    """Look for ``quote`` in the evidence. Returns (source id or None, reason when not found).

    A quote may leave words out with an ellipsis: every part must then be found, in order,
    in the same source.
    """
    cleaned = normalize(quote).strip("\"' ")
    if not cleaned:
        return None, "no quote"
    parts = [p.strip("\"' ") for p in _ELLIPSIS.split(cleaned) if p.strip("\"' ")]
    if sum(len(p) for p in parts) < min_chars:
        return None, f"quote shorter than {min_chars} characters proves nothing"
    ordered = sorted(documents, key=lambda d: d.get("id") != prefer)
    for document in ordered:
        haystack = document.get("_normalized")
        if haystack is None:
            haystack = normalize(str(document.get("text", "")))
        position = 0
        for part in parts:
            position = haystack.find(part, position)
            if position < 0:
                break
            position += len(part)
        else:
            return str(document.get("id")), ""
    return None, "quote not found in the evidence"


# -- registry -----------------------------------------------------------------


@dataclass
class CheckContext:
    task: str
    outputs: dict[str, Any]
    ledger: list[dict[str, Any]]
    history: list[str]
    definition: WorkflowDef
    evidence_dir: Path | None = None
    # What a check that works outside the run's state needs (the site cloner's preparation and build):
    # the run, its workspace, the files the run created, its events, Stop, and a model of the workflow.
    run_id: str = ""
    workspace: Path | None = None
    created: set[Path] | None = None
    emit: Callable[[str, dict[str, Any]], None] | None = None
    cancelled: Callable[[], bool] | None = None
    # complete(slot_id, messages, **options) -> an OpenAI-shaped response from that slot's model.
    complete: Callable[..., dict[str, Any]] | None = None


@dataclass(frozen=True)
class CheckSpec:
    kind: str
    label: str
    description: str
    run: Callable[[CheckDef, CheckContext], dict[str, Any]]
    # Parameters that name other nodes (one id or a list of ids); the validator resolves them.
    node_params: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    # Offered in the workflow editor. A step built by a feature of its own (a website's Explorer) is not.
    listed: bool = True


CHECKS: dict[str, CheckSpec] = {}


def _register(spec: CheckSpec) -> None:
    CHECKS[spec.kind] = spec


def param(check: CheckDef, name: str) -> Any:
    value = check.params.get(name)
    return CHECKS[check.kind].defaults.get(name) if value is None else value


def node_refs(check: CheckDef) -> list[str]:
    """Every node a check's parameters point at."""
    spec = CHECKS.get(check.kind)
    refs: list[str] = []
    for name in spec.node_params if spec else ():
        value = check.params.get(name)
        refs += [str(v) for v in value] if isinstance(value, list) else [str(value)] if value else []
    return refs


def _as_list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else [str(value)] if value else []


def _documents(ctx: CheckContext, node: str | None) -> list[dict[str, Any]]:
    output = ctx.outputs.get(node or "") or {}
    documents = [dict(d) for d in output.get("documents") or [] if isinstance(d, dict)]
    for document in documents:
        document["_normalized"] = normalize(str(document.get("text", "")))
    return documents


def _latest(ctx: CheckContext, nodes: list[str]) -> str | None:
    """The node among ``nodes`` that produced output most recently."""
    for node in reversed(ctx.history):
        if node in nodes and node in ctx.outputs:
            return node
    return next((n for n in nodes if n in ctx.outputs), None)


# -- evidence dossier ---------------------------------------------------------

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".log", ".ini", ".toml", ".tex", ".py", ".js", ".ts", ".sql", ".sh", ".ps1",
}
MAX_FILES = 40
MAX_FILE_BYTES = 2_000_000


def _evidence_dossier(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    max_chars = int(param(check, "max_chars"))
    documents: list[dict[str, Any]] = []
    skipped: list[str] = []
    used = 0

    def add(name: str, text: str) -> None:
        nonlocal used
        text = text.replace("\r\n", "\n").strip()
        if not text:
            return
        room = max_chars - used
        if room <= 0:
            skipped.append(f"{name}: the dossier is full ({max_chars} characters)")
            return
        cut = text[:room]
        used += len(cut)
        documents.append({"id": f"E{len(documents) + 1}", "name": name, "text": cut, "truncated": len(cut) < len(text)})

    if param(check, "include_task"):
        add("Request", ctx.task)
    folder = ctx.evidence_dir
    if folder is not None and folder.is_dir():
        files = sorted((p for p in folder.rglob("*") if p.is_file() and not any(part.startswith(".") for part in p.parts)), key=str)
        for path in files[:MAX_FILES]:
            name = str(path.relative_to(folder)).replace("\\", "/")
            if path.suffix.lower() not in TEXT_SUFFIXES:
                skipped.append(f"{name}: only plain-text files are read")
            elif path.stat().st_size > MAX_FILE_BYTES:
                skipped.append(f"{name}: larger than {MAX_FILE_BYTES // 1_000_000} MB")
            else:
                add(name, path.read_text(encoding="utf-8", errors="replace"))
        if len(files) > MAX_FILES:
            skipped.append(f"{len(files) - MAX_FILES} more files: only the first {MAX_FILES} are read")

    total = sum(len(d["text"]) for d in documents)
    sources = [
        {"id": d["id"], "name": d["name"], "sha256": hashlib.sha256(d["text"].encode("utf-8")).hexdigest(), "chars": len(d["text"]), "truncated": d["truncated"]}
        for d in documents
    ]
    task_tokens = int(len(ctx.task) / 3.5)
    limit = param(check, "max_task_tokens")
    if limit and task_tokens > int(limit):
        passed, verdict = False, "REQUEST_TOO_LONG"
        summary = f"The request is about {task_tokens} tokens, above the {int(limit)} this workflow accepts. No model was called."
    elif total < int(param(check, "min_chars")):
        passed, verdict = False, "NO_EVIDENCE"
        summary = (
            f"Only {total} characters of evidence were provided; at least {int(param(check, 'min_chars'))} are needed. "
            "Paste the material into the request or choose a folder of text documents. No model was called."
        )
    else:
        passed, verdict = True, "EVIDENCE_READY"
        summary = f"{len(documents)} evidence source{'s' if len(documents) != 1 else ''}, {total} characters, each recorded with its SHA-256."
    return {
        "passed": passed, "verdict": verdict, "summary": summary, "sources": sources, "total_chars": total,
        "dossier_sha256": digest([s["sha256"] for s in sources]), "skipped": skipped,
        "documents": [{"id": d["id"], "name": d["name"], "text": d["text"]} for d in documents],
    }


_register(CheckSpec(
    kind="evidence_dossier",
    label="Evidence dossier",
    description=(
        "Runs before any model. Collects the request and the text files of the documents folder into numbered "
        "sources, records the SHA-256 of each, and stops the run when there is too little evidence or the request "
        "is too long. Everything an agent later quotes is checked against this text."
    ),
    run=_evidence_dossier,
    defaults={"include_task": True, "max_chars": 48000, "min_chars": 200, "max_task_tokens": None},
))


# -- claims -------------------------------------------------------------------


def _verify_claims(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    source = _latest(ctx, _as_list(param(check, "claims")))
    claims = [c for c in (ctx.outputs.get(source or "") or {}).get("claims") or [] if isinstance(c, dict)]
    documents = _documents(ctx, param(check, "evidence"))
    audit_node = param(check, "audit")
    audited = audit_node is not None
    verdicts: dict[str, dict[str, Any]] = {}
    if audited:
        for verdict in (ctx.outputs.get(audit_node) or {}).get("verdicts") or []:
            if isinstance(verdict, dict):
                verdicts.setdefault(str(verdict.get("claim", "")).strip(), verdict)
    known = {str(c.get("id")) for c in claims}

    accepted: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for claim in claims:
        claim_id, statement = str(claim.get("id", "")), str(claim.get("statement", ""))
        found, reason = find_quote(str(claim.get("quote", "")), documents, str(claim.get("source", "")), int(param(check, "min_quote_chars")))
        if found is None:
            discarded.append({"id": claim_id, "statement": statement, "reason": reason})
            continue
        if audited:
            verdict = verdicts.get(claim_id)
            if verdict is None:
                discarded.append({"id": claim_id, "statement": statement, "reason": "the auditor gave no verdict on it"})
                continue
            if not verdict.get("holds"):
                objection = str(verdict.get("objection") or "").strip() or "no reason given"
                discarded.append({"id": claim_id, "statement": statement, "reason": f"the auditor rejected it: {objection}"})
                continue
        accepted.append({"id": claim_id, "statement": statement, "quote": str(claim.get("quote", "")), "source": found})

    passed = len(accepted) >= int(param(check, "min_accepted"))
    # What the agent itself said the evidence does not cover. Reported as its words, not as a finding.
    gaps = [str(g) for g in (ctx.outputs.get(source or "") or {}).get("gaps") or []]
    if claims:
        summary = (
            f"{len(accepted)} of {len(claims)} claims kept: each has a quote found word for word in the evidence"
            + (" and an auditor's verdict that it holds" if audited else "") + f". {len(discarded)} discarded."
        )
    else:
        summary = "No claim was offered with a quote, so there is nothing to answer from."
        if gaps:
            summary += " The agent reported that the evidence does not say: " + " ".join(gaps[:4])
    return {
        "passed": passed,
        "verdict": "CLAIMS_VERIFIED" if passed else "NO_VERIFIED_CLAIMS",
        "summary": summary,
        "gaps": gaps,
        "from": source,
        "accepted": accepted,
        "discarded": discarded,
        "accepted_count": len(accepted),
        "discarded_count": len(discarded),
        "unknown_verdicts": sorted(v for v in verdicts if v not in known),
    }


_register(CheckSpec(
    kind="verify_claims",
    label="Claim filter",
    description=(
        "Keeps a claim only if its quote exists word for word in the evidence and, when an auditor is set, the "
        "auditor's verdict says it holds. A claim with no quote, an invented quote or no verdict is discarded. "
        "It proves the quote is real, not that the claim reads it correctly: that is the auditor's job."
    ),
    run=_verify_claims,
    node_params=("claims", "audit", "evidence"),
    defaults={"audit": None, "min_accepted": 1, "min_quote_chars": MIN_QUOTE_CHARS},
))


# -- consensus ----------------------------------------------------------------

DECIDED = {"AGREE": "ACCEPTED", "DISAGREE": "REJECTED"}


def _tally_votes(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    panel = _as_list(param(check, "panel"))
    propositions = [p for p in (ctx.outputs.get(param(check, "propositions")) or {}).get("propositions") or [] if isinstance(p, dict)]
    documents = _documents(ctx, param(check, "evidence"))
    threshold = float(param(check, "threshold"))
    previous = {str(r.get("id")): r for r in (ctx.outputs.get(param(check, "previous") or "") or {}).get("results") or []}
    size = max(1, len(panel))

    ballots: dict[str, dict[str, dict[str, Any]]] = {}
    for member in panel:
        votes: dict[str, dict[str, Any]] = {}
        for vote in (ctx.outputs.get(member) or {}).get("votes") or []:
            if isinstance(vote, dict):
                votes.setdefault(str(vote.get("proposition", "")).strip(), vote)
        ballots[member] = votes

    results: list[dict[str, Any]] = []
    for proposition in propositions:
        pid, statement = str(proposition.get("id", "")), str(proposition.get("statement", ""))
        earlier = previous.get(pid)
        if earlier and earlier.get("outcome") in ("ACCEPTED", "REJECTED"):
            # Settled in the earlier round: it is not reopened.
            results.append({**earlier, "round": earlier.get("round", 1)})
            continue
        leaning = earlier.get("leaning") if earlier else None
        weight = {"AGREE": 0.0, "DISAGREE": 0.0}
        proven = {"AGREE": False, "DISAGREE": False}
        quotes: dict[str, list[dict[str, str]]] = {"AGREE": [], "DISAGREE": []}
        counts = {"AGREE": 0, "DISAGREE": 0, "UNSURE": 0}
        penalized = 0
        for member in panel:
            vote = ballots[member].get(pid)
            choice = str((vote or {}).get("vote", "UNSURE")).upper()
            if choice not in weight:
                counts["UNSURE"] += 1
                continue
            counts[choice] += 1
            found, _ = find_quote(str((vote or {}).get("quote", "")), documents, str((vote or {}).get("source", "")))
            if found is not None:
                weight[choice] += 1.0
                proven[choice] = True
                quotes[choice].append({"quote": str((vote or {}).get("quote", "")), "source": found})
            elif leaning and choice != leaning:
                # Going against the panel's earlier majority without evidence counts for nothing.
                penalized += 1
            else:
                weight[choice] += 0.5
        outcome = "CONTESTED"
        for choice, decided in DECIDED.items():
            # A side wins only with enough weight and at least one quote that is really in the evidence.
            if weight[choice] / size >= threshold and proven[choice]:
                outcome = decided
        results.append({
            "id": pid, "statement": statement, "outcome": outcome,
            "agree": counts["AGREE"], "disagree": counts["DISAGREE"], "unsure": counts["UNSURE"],
            "agree_weight": round(weight["AGREE"], 2), "disagree_weight": round(weight["DISAGREE"], 2),
            "leaning": "AGREE" if weight["AGREE"] > weight["DISAGREE"] else "DISAGREE" if weight["DISAGREE"] > weight["AGREE"] else None,
            "quotes_for": quotes["AGREE"], "quotes_against": quotes["DISAGREE"],
            "penalized_votes": penalized, "round": 2 if earlier else 1,
        })

    def pick(outcome: str) -> list[dict[str, Any]]:
        return [
            {"id": r["id"], "statement": r["statement"], "support": r["quotes_for"] if outcome == "ACCEPTED" else r["quotes_against"]}
            for r in results if r["outcome"] == outcome
        ]

    contested = [r for r in results if r["outcome"] == "CONTESTED"]
    decided_share = (len(results) - len(contested)) / len(results) if results else 0.0
    passed = bool(results) and decided_share >= float(param(check, "quorum"))
    return {
        "passed": passed,
        "verdict": "CONSENSUS" if passed else "INSUFFICIENT_CONSENSUS",
        "summary": (
            f"{len(results) - len(contested)} of {len(results)} propositions decided by a panel of {len(panel)} "
            f"(a side needs {threshold:.0%} of the panel's weight and one quote found in the evidence). "
            f"{len(contested)} left contested."
        ),
        "accepted": pick("ACCEPTED"),
        "rejected": pick("REJECTED"),
        "contested": [{"id": r["id"], "statement": r["statement"]} for r in contested],
        "contested_count": len(contested),
        "decided_share": round(decided_share, 2),
        # What the panel is shown before a second round: counts and quotes, never who voted what.
        "feedback": [
            {"id": r["id"], "statement": r["statement"], "agree": r["agree"], "disagree": r["disagree"], "unsure": r["unsure"],
             "quotes_for": r["quotes_for"], "quotes_against": r["quotes_against"]}
            for r in contested
        ],
        "results": results,
        "ballots": {member: {pid: str(v.get("vote", "")) for pid, v in votes.items()} for member, votes in ballots.items()},
    }


_register(CheckSpec(
    kind="tally_votes",
    label="Consensus tally",
    description=(
        "Counts the panel's votes on each proposition. A vote backed by a quote found in the evidence weighs 1, a "
        "vote without one weighs half, and in a second round a vote against the earlier majority without a quote "
        "weighs nothing. A proposition is decided only when one side reaches the threshold and holds a real quote; "
        "the rest stay contested. The threshold is a setting, not a calibrated value."
    ),
    run=_tally_votes,
    node_params=("panel", "propositions", "evidence", "previous"),
    defaults={"threshold": 0.66, "quorum": 0.5, "previous": None},
))


# -- commits ------------------------------------------------------------------


def _last_entry(ledger: list[dict[str, Any]], node: str) -> dict[str, Any] | None:
    return next((e for e in reversed(ledger) if e.get("node") == node), None)


def _verify_commits(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    commits = _as_list(param(check, "commits"))
    review_node = str(param(check, "review"))
    review = ctx.outputs.get(review_node) or {}
    verdicts = {str(v.get("commit", "")).strip(): v for v in review.get("verdicts") or [] if isinstance(v, dict)}
    reviewed = (_last_entry(ctx.ledger, review_node) or {}).get("read") or {}

    approved: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for commit in commits:
        output = ctx.outputs.get(commit)
        if not isinstance(output, dict):
            dropped.append({"commit": commit, "reason": "it produced nothing"})
            continue
        current = digest(output)
        verdict = verdicts.get(commit)
        if verdict is None:
            dropped.append({"commit": commit, "sha256": current, "reason": "the reviewer gave no verdict on it"})
        elif not verdict.get("approved"):
            issues = "; ".join(str(i) for i in verdict.get("issues") or []) or "no reason given"
            dropped.append({"commit": commit, "sha256": current, "reason": f"the reviewer rejected it: {issues}"})
        elif reviewed.get(commit) != current:
            dropped.append({
                "commit": commit, "sha256": current,
                "reason": f"what was approved ({str(reviewed.get(commit))[:12]}) is not what is there now ({current[:12]})",
            })
        else:
            approved.append({
                "commit": commit, "role": ctx.definition.node_label(commit), "sha256": current,
                "summary": output.get("summary", ""), "content": output.get("content", ""),
                "assumptions": output.get("assumptions") or [],
            })
    passed = len(approved) >= int(param(check, "min_approved"))
    return {
        "passed": passed,
        "verdict": "COMMITS_VERIFIED" if passed else "NO_VERIFIED_COMMIT",
        "summary": (
            f"{len(approved)} of {len(commits)} commits passed: approved by the reviewer, and the hash recomputed now "
            f"equals the hash of what the reviewer was shown. {len(dropped)} dropped."
        ),
        "approved": approved,
        "dropped": dropped,
        "approved_count": len(approved),
    }


_register(CheckSpec(
    kind="verify_commits",
    label="Commit check",
    description=(
        "Recomputes the SHA-256 of every commit and lets one through only if the reviewer approved it and the hash "
        "equals the one recorded when the reviewer read it. A commit that was rejected, not reviewed, or changed "
        "after its review is dropped. The merge step is shown only what passes."
    ),
    run=_verify_commits,
    node_params=("commits", "review"),
    defaults={"min_approved": 1},
))


# -- audit trail --------------------------------------------------------------


def _audit_trail(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    answer_node = _latest(ctx, _as_list(param(check, "answer")))
    answer = ctx.outputs.get(answer_node or "") or {}
    # Several nodes may be named (a first and a final tally): the one that ran last decides.
    basis = ctx.outputs.get(_latest(ctx, _as_list(param(check, "accepted"))) or "") or {}
    allowed = {str(i.get("id") or i.get("commit")) for key in ("accepted", "rejected", "approved") for i in basis.get(key) or [] if isinstance(i, dict)}
    used = [str(u).strip() for u in answer.get("used") or []]
    unknown = sorted(set(used) - allowed)

    mismatches = []
    for node, output in ctx.outputs.items():
        entry = _last_entry(ctx.ledger, node)
        if entry is None or entry.get("sha256") != digest(output):
            mismatches.append(node)
    counts: dict[str, int] = {}
    for entry in ctx.ledger:
        counts[str(entry.get("node"))] = counts.get(str(entry.get("node")), 0) + 1

    if mismatches:
        passed, verdict = False, "LEDGER_MISMATCH"
        summary = f"The recorded hash of {', '.join(mismatches)} does not match its output. The answer is withheld."
    elif unknown:
        passed, verdict = False, "UNSUPPORTED_ANSWER"
        summary = f"The answer relies on {len(unknown)} item(s) that were never accepted ({', '.join(unknown)}). It is withheld."
    elif not used:
        passed, verdict = False, "UNSUPPORTED_ANSWER"
        summary = "The answer names no accepted item it relies on. It is withheld."
    else:
        passed, verdict = True, "SEALED"
        summary = (
            f"The answer relies on {len(used)} accepted item(s), all of them known. {len(ctx.ledger)} earlier steps "
            "recorded, every stored hash recomputed and equal."
        )
    out: dict[str, Any] = {
        "passed": passed, "verdict": verdict, "summary": summary, "used": used, "unknown_citations": unknown,
        "caveats": answer.get("caveats") or [],
        "steps": [
            {"step": i + 1, "node": e.get("node"), "label": ctx.definition.node_label(str(e.get("node"))), "kind": e.get("kind"),
             "model": e.get("model", ""), "sha256": e.get("sha256"), "read": e.get("read") or {}}
            for i, e in enumerate(ctx.ledger)
        ],
        "activations": counts,
    }
    if passed:
        out["markdown"] = str(answer.get("answer", ""))
    return out


_register(CheckSpec(
    kind="audit_trail",
    label="Audit trail",
    description=(
        "The last step. Checks that every item the answer says it relies on was accepted earlier, recomputes every "
        "recorded hash, and releases the answer together with the list of steps, what each one read and its SHA-256. "
        "It checks the citations the answer declares, not the wording of the answer."
    ),
    run=_audit_trail,
    node_params=("answer", "accepted"),
    defaults={},
))


def _refuse(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    return {
        "passed": False,
        "verdict": str(param(check, "verdict")),
        "summary": str(param(check, "message")),
        "activations": {node: ctx.history.count(node) for node in dict.fromkeys(ctx.history)},
    }


_register(CheckSpec(
    kind="refuse",
    label="Refusal",
    description=(
        "Ends the run without an answer, with a named verdict. A workflow routes here when no trusted path "
        "produced something that could be verified: refusing is the outcome, not a failure to be worked around."
    ),
    run=_refuse,
    defaults={"verdict": "REFUSED", "message": "The run refuses rather than answer without verification."},
))


def catalog() -> list[dict[str, Any]]:
    return [{"kind": s.kind, "label": s.label, "description": s.description, "defaults": s.defaults} for s in CHECKS.values() if s.listed]


def run_check(check: CheckDef, ctx: CheckContext) -> dict[str, Any]:
    spec = CHECKS.get(check.kind)
    if spec is None:
        return {"passed": False, "verdict": "UNKNOWN_CHECK", "summary": f"Unknown check '{check.kind}'."}
    return spec.run(check, ctx)
