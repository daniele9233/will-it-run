"""The report that reads like the run did: each question as it was asked, then what every model answered.

``export`` summarises a run (ranking, areas, a matrix of marks) and shows only the answers that were not right. This
one is for reading: for every question, the text the models were given, what was expected, and the answer of each model
side by side with the way it was judged, so two or more models can be compared on the very same question.

Nothing here judges anything: the verdicts are the stored ones, turned into words when the file is written.
"""

from __future__ import annotations

import json
from typing import Any

from agentfit import branding
from agentfit.arena import export as base
from agentfit.arena.store import ArenaStore
from agentfit.i18n import current_language, tr

#: Statuses that mean "a verifier looked at it and decided": two models differ when these differ.
JUDGED = ("passed", "partial", "failed")


def _text_of(content: Any) -> str:
    """The words of a message, whether it is a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text")
    return "" if content is None else str(content)


def conversation(question: dict[str, Any]) -> list[tuple[str, str]]:
    """(role, text) of what was sent to every model, in order."""
    messages = question.get("messages")
    if messages:
        out: list[tuple[str, str]] = []
        for message in messages:
            text = _text_of(message.get("content"))
            if message.get("tool_calls"):
                text = (text + "\n" if text else "") + json.dumps(message["tool_calls"], ensure_ascii=False)
            out.append((str(message.get("role") or "user"), text))
        return out
    out = []
    if question.get("system"):
        out.append(("system", str(question["system"])))
    out.append(("user", str(question.get("prompt") or "")))
    return out


def build(store: ArenaStore, run_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Every question with the answer of every model, the models in the order of the ranking."""
    models = report["models"]
    results: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in base._results(store, run_id, report):
        results.setdefault((row["question"], row["model"]), []).append(row)
    items = []
    for question in store.all_questions(run_id):
        answers = []
        for model in models:
            for row in results.get((question["id"], model["key"]), []):
                text = (row["answer"] or "").strip()
                answers.append({
                    "model": model["label"], "key": model["key"], "repeat": row["repeat"], "status": row["status"], "kind": row["kind"],
                    "reason": row["reason"], "extracted": row["extracted"], "manual": row["manual"], "manual_note": row["manual_note"],
                    "answer": text[:base.TEXT_LIMIT], "cut": len(text) > base.TEXT_LIMIT, "tool_calls": row["tool_calls"],
                    "output_tokens": row["output_tokens"], "duration_s": row["duration_s"], "tps": row["tps"], "error": row["error"],
                    "finish_reason": row["finish_reason"],
                })
        judged = {a["status"] for a in answers if a["status"] in JUDGED}
        items.append({
            "id": question["id"], "title": question["title"], "category": question["category"], "conversation": conversation(question),
            "expected": question.get("expected") or "", "images": question.get("images") or [], "attachments": question.get("attachments") or [],
            "answers": answers, "differs": len(judged) > 1, "mistakes": any(a["status"] in ("failed", "partial") for a in answers),
        })
    return {"models": models, "questions": items}


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks inside the text."""
    longest = run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _verdict_line(answer: dict[str, Any]) -> str:
    glyph = base.GLYPHS.get(answer["status"], "?")
    parts = [f"{glyph} {base._status(answer['status'])}"]
    if answer["kind"]:
        parts.append(base._kind(answer["kind"]))
    if answer["reason"]:
        parts.append(str(answer["reason"]))
    if answer["manual"]:
        parts.append(tr("your mark: {mark}", mark=tr("right") if answer["manual"] == "passed" else tr("wrong")))
    return " · ".join(parts)


def _speed(answer: dict[str, Any]) -> str:
    bits = []
    if answer["output_tokens"]:
        bits.append(f"{answer['output_tokens']} tok")
    if answer["duration_s"]:
        bits.append(f"{answer['duration_s']:.1f} s")
    if answer["tps"]:
        bits.append(f"{answer['tps']:.0f} tok/s")
    return " · ".join(bits)


# -- Markdown ----------------------------------------------------------------------------------------------------


def to_markdown(store: ArenaStore, run_id: str, report: dict[str, Any]) -> str:
    data = build(store, run_id, report)
    run, models = report["run"], data["models"]
    made = tr("Made with {app} {version}", app=branding.APP_NAME, version=branding.APP_VERSION)
    when = (run["started_at"] or run["created_at"])[:16].replace("T", " ")
    lines = [f"# {run['title'] or tr('Arena run')} — {tr('Questions and answers')}", "", f"*{made} · {when}*", ""]
    lines.append(f"**{tr('Suite')}**: {run['suite_id']} {run['suite_version']} · {tr('seed')} {run['seed']} · {tr('pack')} {run['preset'] or tr('custom')} · "
                 f"{tr('hash')} `{run['suite_hash'][:12]}`")
    merged = (run["settings"] or {}).get("merged")
    if merged:
        lines += ["", f"> {tr('This comparison was made from {n} separate challenges with the same questions.', n=len(merged.get('from') or []))}"]
        for line in merged.get("differences") or []:
            lines.append(f"> - {line}")
    lines += ["", f"## {tr('Ranking')}", ""]
    lines += base._table(
        ["#", tr("Model"), tr("Score"), "95%", "tok/s"],
        ([m["rank"] or "—", m["label"], base._score(m["score"]), base._interval(m["ci"]), base._score(m["summary"]["speed"]["tps_mean"])] for m in models),
    )
    lines += ["", f"*{tr('Not evaluated, skipped and run errors are not wrong answers: they are left out of every score.')}*", ""]
    category = None
    for q in data["questions"]:
        if q["category"] != category:
            category = q["category"]
            lines += [f"## {base._category(category)}", ""]
        lines.append(f"### `{q['id']}` {q['title']}" + (f" — {tr('the models differ here')}" if q["differs"] else ""))
        lines.append("")
        for role, text in q["conversation"]:
            lines.append(f"**{role}**")
            lines += [f"> {line}" if line else ">" for line in text.splitlines() or [""]]
            lines.append("")
        for image in q["images"]:
            lines += [f"*{tr('Picture')}: {image.get('name')} ({image.get('width')}x{image.get('height')})*", ""]
        for item in q["attachments"]:
            lines += [f"*{tr('File')}: {item.get('name')} ({item.get('pages')} {tr('pages')})*", ""]
        if q["expected"]:
            lines += [f"**{tr('Expected')}**: {q['expected']}", ""]
        for a in q["answers"]:
            repeat = f" #{a['repeat'] + 1}" if a["repeat"] else ""
            lines += [f"#### {a['model']}{repeat} — {_verdict_line(a)}", ""]
            body = a["answer"]
            if body:
                fence = _fence(body)
                lines += [fence + "text", body + ("…" if a["cut"] else ""), fence, ""]
            elif a["tool_calls"]:
                fence = "```"
                lines += [fence + "json", json.dumps(a["tool_calls"], ensure_ascii=False, indent=2), fence, ""]
            else:
                lines += [f"*{tr('No answer was kept.')}*", ""]
            if a["error"]:
                lines += [f"*{a['error']}*", ""]
            speed = _speed(a)
            if speed:
                lines += [f"*{speed}*", ""]
    return "\n".join(lines) + "\n"


# -- HTML --------------------------------------------------------------------------------------------------------

_EXTRA_CSS = """
.q{border-top:1px solid var(--grid);padding:14px 0}.q h3{margin:0 0 6px}.chips{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink2)}.msg{margin:6px 0}.role{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.ans{border:1px solid var(--ring);border-left-width:4px;border-radius:8px;padding:8px 10px;margin:8px 0;background:var(--surface)}
.ans.passed{border-left-color:var(--good)}.ans.partial{border-left-color:var(--warn)}.ans.failed{border-left-color:var(--bad)}.ans.error{border-left-color:#6250d6}
.ans.unscored,.ans.skipped{border-left-color:var(--none)}.ans h4{margin:0 0 4px;font-size:14px}.diff{font-size:12px;color:var(--ink2);font-weight:400}
.filters{display:flex;flex-wrap:wrap;gap:16px;margin:12px 0}.filters label{cursor:pointer}.q img{max-width:100%;border-radius:8px;border:1px solid var(--ring)}
body:has(#only-diff:checked) .q:not(.differs){display:none}body:has(#only-bad:checked) .q:not(.mistakes){display:none}
"""


def to_html(store: ArenaStore, run_id: str, report: dict[str, Any]) -> str:
    data = build(store, run_id, report)
    run, models = report["run"], data["models"]
    e = base._e
    light = "".join(f"--s{i + 1}:{c[0]};" for i, c in enumerate(base.SERIES))
    dark = "".join(f"--s{i + 1}:{c[1]};" for i, c in enumerate(base.SERIES))
    css = base._CSS.replace("@@LIGHT@@", light).replace("@@DARK@@", dark) + _EXTRA_CSS
    title = f"{run['title'] or tr('Arena run')} — {tr('Questions and answers')}"
    made = tr("Made with {app} {version}", app=branding.APP_NAME, version=branding.APP_VERSION)
    started = (run["started_at"] or run["created_at"])[:16].replace("T", " ")
    body: list[str] = [f"<h1>{e(title)}</h1>",
                       f'<p class="sub">{e(made)} · {e(started)} · {e(run["suite_id"])} {e(run["suite_version"])} · {e(tr("seed"))} {run["seed"]} · '
                       f'{e(tr("pack"))} {e(run["preset"] or tr("custom"))}</p>']
    merged = (run["settings"] or {}).get("merged")
    if merged:
        notes = "".join(f"<li>{e(line)}</li>" for line in merged.get("differences") or [])
        body.append(f'<p class="note"><b>{e(tr("This comparison was made from {n} separate challenges with the same questions.", n=len(merged.get("from") or [])))}</b></p>'
                    + (f'<ul class="note">{notes}</ul>' if notes else ""))
    legend = "".join(f'<span><span class="dot" style="background:{base._color(i)}"></span>{e(m["label"])}</span>' for i, m in enumerate(models))
    body.append(f'<div class="legend">{legend}</div>')
    body.append(f'<h2>{e(tr("Ranking"))}</h2><div class="card">')
    for i, m in enumerate(models):
        width = m["score"] if m["score"] is not None else (m["score_all"] or 0)
        ci = f" ({base._interval(m['ci'])})" if m["ci"] else ""
        body.append(f'<div class="rank"><b>{m["rank"] or base.DASH}</b><span>{e(m["label"])}</span>'
                    f'<span class="track"><span class="fill" style="width:{width:.1f}%;background:{base._color(i)}"></span></span>'
                    f'<span class="num"><b>{base._score(m["score"])}</b>{e(ci)}</span></div>')
    body.append("</div>")
    body.append(f'<h2>{e(tr("Questions and answers"))}</h2>')
    body.append(f'<div class="filters"><label><input type="checkbox" id="only-diff"> {e(tr("Only where the models differ"))}</label>'
                f'<label><input type="checkbox" id="only-bad"> {e(tr("Only the mistakes"))}</label></div>')
    category = None
    index = {m["key"]: i for i, m in enumerate(models)}
    for q in data["questions"]:
        if q["category"] != category:
            category = q["category"]
            body.append(f"<h3>{e(base._category(category))}</h3>")
        classes = "q" + (" differs" if q["differs"] else "") + (" mistakes" if q["mistakes"] else "")
        chips = "".join(
            f'<span class="chip"><span class="cell st-{a["status"]}">{base.GLYPHS.get(a["status"], "?")}</span>{e(a["model"])}</span>' for a in q["answers"]
        )
        flag = f' <span class="diff">· {e(tr("the models differ here"))}</span>' if q["differs"] else ""
        body.append(f'<section class="{classes}"><h3><code>{e(q["id"])}</code> {e(q["title"])}{flag}</h3><div class="chips">{chips}</div>')
        for role, text in q["conversation"]:
            body.append(f'<div class="msg"><span class="role">{e(role)}</span><pre>{e(text)}</pre></div>')
        for image in q["images"]:
            if image.get("data_b64"):
                body.append(f'<img alt="{e(image.get("name"))}" src="data:image/png;base64,{e(image["data_b64"])}">')
        for item in q["attachments"]:
            body.append(f'<p class="note">{e(tr("File"))}: {e(item.get("name"))} ({e(item.get("pages"))} {e(tr("pages"))})</p>')
        if q["expected"]:
            body.append(f'<p class="k">{e(tr("Expected"))}: <b>{e(q["expected"])}</b></p>')
        for a in q["answers"]:
            repeat = f" #{a['repeat'] + 1}" if a["repeat"] else ""
            swatch = f'<span class="dot" style="background:{base._color(index.get(a["key"], 0))}"></span>'
            shown = e(a["answer"]) + ("…" if a["cut"] else "")
            if not a["answer"] and a["tool_calls"]:
                shown = e(json.dumps(a["tool_calls"], ensure_ascii=False, indent=2))
            elif not a["answer"]:
                shown = f"<i>{e(tr('No answer was kept.'))}</i>"
            extra = f'<p class="note">{e(a["error"])}</p>' if a["error"] else ""
            speed = _speed(a)
            body.append(f'<div class="ans {a["status"]}"><h4>{swatch}{e(a["model"])}{e(repeat)} <span class="diff">· {e(_verdict_line(a))}</span></h4>'
                        f'<pre>{shown}</pre>{extra}' + (f'<p class="note">{e(speed)}</p>' if speed else "") + "</div>")
        body.append("</section>")
    return (f'<!doctype html><html lang="{current_language()}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{e(title)}</title><style>{css}</style></head><body><main>{''.join(body)}</main></body></html>")

