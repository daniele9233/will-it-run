"""A run as a file: JSON for machines, CSV for a spreadsheet, Markdown for a note, HTML for a page that stands on its own.

Every format carries what is needed to repeat the run (the suite's version and hashes, the seed, the sampling, the models
and the tools that checked the answers), and none of them hides what was not evaluated: it has its own column, count and colour.
"""

from __future__ import annotations

import csv
import html
import io
import json
import math
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from agentfit import branding
from agentfit.arena import quants
from agentfit.arena.categories import AREAS
from agentfit.arena.labels import AREA_LABELS, CATEGORY_LABELS, KIND_LABELS, STATUS_LABELS
from agentfit.arena.report import build_report, render_verdict
from agentfit.arena.store import ArenaStore
from agentfit.i18n import current_language, tr

FORMATS = {"json": "application/json", "csv": "text/csv", "md": "text/markdown", "html": "text/html"}
FORMAT_VERSION = 1
# The reasoning and the reply are cut here in the JSON file: a thinking model can write a whole book.
TEXT_LIMIT = 8000
EXCERPT = 600
DASH = "–"
GLYPHS = {"passed": "✓", "partial": "½", "failed": "✗", "unscored": "?", "skipped": DASH, "error": "!"}
STATUSES = ("passed", "partial", "failed", "unscored", "skipped", "error")

# The eight series colours of the reference palette, validated on its light and dark surfaces, in their fixed order.
SERIES = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
          ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#6250d6", "#9085e9"), ("#e34948", "#e66767")]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "run"


def _category(category: str) -> str:
    return tr(CATEGORY_LABELS.get(category, category))


def _area(area: str) -> str:
    return tr(AREA_LABELS.get(area, area))


def _status(status: str) -> str:
    return tr(STATUS_LABELS.get(status, status))


def _kind(kind: str | None) -> str:
    return tr(KIND_LABELS[kind]) if kind in KIND_LABELS else (kind or "")


def _score(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _interval(ci: list[float] | None) -> str:
    return f"{ci[0]:.0f}{DASH}{ci[1]:.0f}" if ci else ""


def _results(store: ArenaStore, run_id: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    """Every answer, with the verdict in words and the question it belongs to."""
    questions = {q["id"]: q for q in report["matrix"]}
    rows = []
    for row in store.results(run_id):
        verdict = render_verdict(row["verdict"])
        q = questions.get(row["question_id"], {})
        rows.append({
            "model": row["model_key"], "question": row["question_id"], "category": q.get("category"), "title": q.get("title"), "repeat": row["repeat"],
            "status": row["status"], "score": row["score"], "kind": row["kind"], "method": row["method"], "reason": verdict.get("reason"),
            "checks": verdict.get("checks"), "extracted": verdict.get("extracted"), "expected": verdict.get("expected"), "answer": row["answer"],
            "reasoning": row["reasoning"], "tool_calls": row["tool_calls"], "finish_reason": row["finish_reason"], "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"], "reasoning_tokens": row["reasoning_tokens"], "ttft_s": row["ttft_s"], "duration_s": row["duration_s"],
            "tps": row["tps"], "error": row["error"], "manual": row["manual"], "manual_note": row["manual_note"],
        })
    return rows


def _quant_sections(store: ArenaStore, run_id: str, report: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """For each family of quantizations in the run (family, comparison)."""
    out = []
    for family in report.get("families") or []:
        result = quants.compare(store, run_id, family=family["id"])
        if result and result["comparison"]:
            out.append((family, result["comparison"]))
    return out


QUANT_VERDICT = {"worse": "real loss", "better": "real gain", "noise": "noise"}


def _gb(size: float | None) -> str:
    return DASH if not size else f"{size / 1024 ** 3:.1f} GB"


def _memory(mib: float | None) -> str:
    return DASH if not mib else f"{mib / 1024:.1f} GB"


def _signed(value: float | None, digits: int = 1) -> str:
    if value is None:
        return DASH
    return f"{'+' if value > 0 else '−' if value < 0 else ''}{abs(value):.{digits}f}"


def _quant_rows(comparison: dict[str, Any]) -> list[list[str]]:
    rows = []
    for key in comparison["order"]:
        m = comparison["models"][key]
        if m["is_reference"]:
            gap, spread, verdict = tr("reference"), "", ""
        else:
            gap = _signed(m["delta"])
            spread = f"{_signed(m['delta_ci'][0])} … {_signed(m['delta_ci'][1])}" if m.get("delta_ci") else ""
            verdict = tr("identical answers") if m.get("identical") else tr(QUANT_VERDICT[m["verdict"]])
            if m["verdict"] == "noise" and not m.get("identical"):
                smallest = f"{m['detectable']:.1f}"
                verdict += f" ({tr('smaller than {n} points cannot be seen', n=smallest)})"
        rows.append([f"{m['label']} · {m['quant'] or DASH}", _gb(m["size_bytes"]), _memory(m["vram_mib"]), _score(m["tps_mean"]), _score(m["score"]), gap, spread, verdict])
    return rows


def _quant_head() -> list[str]:
    return [tr("Model"), tr("File"), tr("Graphics memory"), "tok/s", tr("Score"), tr("Difference"), "95%", tr("Verdict")]


def _pc(env: dict[str, Any]) -> str:
    ram = f"{env.get('ram_gb')} GB RAM" if env.get("ram_gb") else ""
    return ", ".join(x for x in (str(env.get("cpu") or ""), ", ".join(env.get("gpu") or []), ram) if x)


def _tools(env: dict[str, Any]) -> str:
    parts = []
    for name, info in (env.get("tools") or {}).items():
        parts.append(f"{name} {info.get('version') or ''}".strip() if info.get("available") else f"{name} ({tr('missing')})")
    return ", ".join(parts)


# -- JSON --------------------------------------------------------------------------------------------------------


def to_json(store: ArenaStore, run_id: str, report: dict[str, Any]) -> str:
    run = report["run"]
    results = _results(store, run_id, report)
    for row in results:
        for name in ("answer", "reasoning"):
            if row[name] and len(row[name]) > TEXT_LIMIT:
                row[name] = row[name][:TEXT_LIMIT] + "…"
    kept = ("id", "category", "title", "lang", "prompt", "system", "tools", "response_format", "expected", "tier", "difficulty", "needs", "check")
    questions = []
    for q in store.all_questions(run_id):
        item = {k: v for k, v in q.items() if k in kept}
        item["images"] = [{"name": i["name"], "width": i["width"], "height": i["height"], "sha256": i["sha256"]} for i in q.get("images", [])]
        item["attachments"] = [{"name": a["name"], "pages": a["pages"], "sha256": a["sha256"]} for a in q.get("attachments", [])]
        questions.append(item)
    standing = ("rank", "tied", "key", "label", "score", "ci", "score_all", "ci_all", "areas", "status", "error", "context_granted", "load_seconds", "info", "summary")
    payload = {
        "format": "willitrun-arena-report", "format_version": FORMAT_VERSION, "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app": {"name": branding.APP_NAME, "version": branding.APP_VERSION},
        "suite": {"id": run["suite_id"], "version": run["suite_version"], "hash": run["suite_hash"], "bank_hash": run["bank_hash"], "seed": run["seed"],
                  "preset": run["preset"], "categories": run["categories"]},
        "run": {k: run[k] for k in ("id", "title", "status", "created_at", "started_at", "finished_at", "total", "done", "note")},
        "settings": run["settings"], "environment": run["environment"],
        "ranking": [{k: m[k] for k in standing} for m in report["models"]],
        "categories": report["categories"], "questions": questions, "results": results,
        "quantizations": [{"family": family["label"], "members": family["members"], **comparison} for family, comparison in _quant_sections(store, run_id, report)],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


# -- CSV ---------------------------------------------------------------------------------------------------------

CSV_COLUMNS = ["run", "model", "question", "category", "title", "repeat", "status", "score", "kind", "method", "reason", "expected", "extracted", "answer",
               "input_tokens", "output_tokens", "reasoning_tokens", "ttft_s", "duration_s", "tokens_per_second", "finish_reason", "manual", "error"]


def to_csv(store: ArenaStore, run_id: str, report: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in _results(store, run_id, report):
        answer = " ".join((row["answer"] or "").split())[:2000]
        writer.writerow([
            run_id, row["model"], row["question"], row["category"], row["title"], row["repeat"], row["status"], row["score"], row["kind"] or "", row["method"] or "",
            row["reason"] or "", row["expected"] or "", row["extracted"] or "", answer, row["input_tokens"], row["output_tokens"],
            row["reasoning_tokens"], row["ttft_s"], row["duration_s"], row["tps"], row["finish_reason"] or "", row["manual"] or "", row["error"] or "",
        ])
    return out.getvalue()


# -- Markdown ----------------------------------------------------------------------------------------------------


def _table(header: Iterable[str], rows: Iterable[Iterable[str]]) -> list[str]:
    head = list(header)
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def to_markdown(store: ArenaStore, run_id: str, report: dict[str, Any]) -> str:
    run, models = report["run"], report["models"]
    settings, env = run["settings"], run["environment"]
    made = tr("Made with {app} {version}", app=branding.APP_NAME, version=branding.APP_VERSION)
    when = (run["started_at"] or run["created_at"])[:16].replace("T", " ")
    lines = [f"# {run['title'] or tr('Arena run')}", "", f"*{made} · {when}*", ""]
    lines.append(f"**{tr('Suite')}**: {run['suite_id']} {run['suite_version']} · {tr('seed')} {run['seed']} · {tr('pack')} {run['preset'] or tr('custom')} · "
                 f"{tr('hash')} `{run['suite_hash'][:12]}` ({tr('bank')} `{run['bank_hash'][:12]}`)")
    lines.append("")
    lines.append(f"**{tr('Sampling')}**: {tr('temperature')} {settings.get('temperature')}, top-p {settings.get('top_p') or DASH}, top-k {settings.get('top_k') or DASH}, "
                 f"{tr('repeat penalty')} {settings.get('repeat_penalty')}, {tr('seed')} {settings.get('seed')}, {tr('repeats')} {settings.get('repeats')}, "
                 f"{tr('context')} {settings.get('context')}, {tr('max tokens')} {settings.get('max_tokens')}")
    lines.append("")
    if report["provisional"]:
        lines += [f"> {tr('The run is not over: these figures will change.')}", ""]

    lines += [f"## {tr('Ranking')}", ""]
    lines += _table(
        ["#", tr("Model"), tr("Score"), "95%", tr("Score on all it did"), tr("Coverage"), "tok/s"],
        ([m["rank"] or "—", m["label"], _score(m["score"]), _interval(m["ci"]), _score(m["score_all"]), f"{m['summary']['coverage'] * 100:.0f}%",
          _score(m["summary"]["speed"]["tps_mean"])] for m in models),
    )
    if report["common"]:
        common = ", ".join(_category(c) for c in report["common"])
        lines += ["", f"*{tr('The score is the mean of the categories every model could be scored on:')} {common}.*"]

    lines += ["", f"## {tr('Areas')}", ""]
    lines += _table([tr("Model"), *[_area(a) for a in AREAS]], ([m["label"], *[_score(m["areas"][a], 0) for a in AREAS]] for m in models))

    lines += ["", f"## {tr('Categories')}", ""]
    category_rows = []
    for c in report["categories"]:
        row = [_category(c["id"])]
        for m in models:
            entry = c["models"].get(m["key"]) or {}
            row.append("—" if entry.get("score") is None else f"{entry['score']:.0f}% ({entry['scored']}/{entry['total']})")
        category_rows.append(row)
    lines += _table([tr("Category"), *[m["label"] for m in models]], category_rows)

    lines += ["", f"## {tr('What was not a right answer')}", ""]
    lines += _table([tr("Model"), *[_status(s) for s in STATUSES]], ([m["label"], *[m["summary"]["counts"].get(s, 0) for s in STATUSES]] for m in models))
    lines += ["", f"*{tr('Not evaluated, skipped and run errors are not wrong answers: they are left out of every score.')}*", ""]
    failures = [(m["label"], m["summary"]["fail_kinds"]) for m in models if m["summary"]["fail_kinds"]]
    if failures:
        lines += [f"### {tr('How the wrong answers went wrong')}", ""]
        for label, kinds in failures:
            how = ", ".join(f"{_kind(k)} {n}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
            lines.append(f"- **{label}**: {how}")
        lines.append("")

    for family, comparison in _quant_sections(store, run_id, report):
        reference = comparison["models"][comparison["reference"]]
        lines += [f"## {tr('Quantizations')}: {family['label']}", "", f"*{tr('Compared with {model}, question by question, on the {n} questions every quantization was scored on.', model=reference['label'] + ' · ' + str(reference['quant'] or DASH), n=comparison['questions'])}*", ""]
        lines += _table(_quant_head(), _quant_rows(comparison))
        lines += ["", f"*{tr('A difference is real only if its interval leaves out zero and the corrected sign test agrees; otherwise it may be noise.')}*", ""]
        lines += _table([tr("Category"), tr("Questions"), *[m["label"] for m in (comparison["models"][k] for k in comparison["order"])]],
                        ([_category(c["id"]), c["n"], *[_score(c["models"][k]["score"], 0) + ("" if c["models"][k]["delta"] is None else f" ({_signed(c['models'][k]['delta'], 0)})") for k in comparison["order"]]]
                         for c in comparison["categories"]))
        lines.append("")

    lines += [f"## {tr('Questions')}", ""]
    question_rows = []
    for q in report["matrix"]:
        marks = [GLYPHS.get((q["cells"].get(m["key"]) or {}).get("status", ""), "·") for m in models]
        question_rows.append([f"`{q['id']}` {q['title']}", _category(q["category"]), *marks])
    lines += _table([tr("Question"), tr("Category"), *[m["label"] for m in models]], question_rows)
    lines += ["", " · ".join(f"{GLYPHS[s]} {_status(s)}" for s in STATUSES), ""]

    lines += [f"## {tr('How to repeat it')}", "", f"- {tr('Same suite version, seed and pack give the same questions: check the hash.')}",
              f"- {tr('Same sampling and the same models, one at a time in memory.')}"]
    for label, value in ((tr("PC"), _pc(env)), ("LM Studio", (env.get("lmstudio") or {}).get("version") or ""), (tr("Tools that checked the answers"), _tools(env))):
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines) + "\n"


# -- HTML --------------------------------------------------------------------------------------------------------

_CSS = """
:root{color-scheme:light;--bg:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);
--good:#0ca30c;--warn:#fab219;--bad:#d03b3b;--none:#c3c2b7;--accent:#2a78d6;@@LIGHT@@}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--ring:rgba(255,255,255,.10);--accent:#3987e5;@@DARK@@}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px}h3{font-size:15px;margin:20px 0 8px}
.sub{color:var(--ink2);margin:0 0 4px}.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px;margin:12px 0}
table{border-collapse:collapse;width:100%}th,td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--grid);vertical-align:top}th{color:var(--ink2);font-weight:600;font-size:13px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.wrap{overflow-x:auto}.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:8px 0;font-size:13px;color:var(--ink2)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:-1px}.rank{display:grid;grid-template-columns:32px minmax(120px,220px) 1fr 110px;gap:8px;align-items:center;margin:6px 0}
.rank .track{height:14px;background:var(--grid);border-radius:4px;position:relative}.rank .fill{position:absolute;top:0;bottom:0;left:0;border-radius:4px}
.rank .num{font-variant-numeric:tabular-nums;text-align:right}.cell{display:inline-block;width:26px;height:22px;line-height:22px;text-align:center;font-size:12px;color:#fff;border-radius:4px;font-weight:600}
.st-passed{background:var(--good)}.st-partial{background:var(--warn);color:#0b0b0b}.st-failed{background:var(--bad)}
.st-unscored{background:repeating-linear-gradient(45deg,var(--none),var(--none) 3px,transparent 3px,transparent 6px);color:var(--ink)}
.st-skipped{background:transparent;color:var(--muted);border:1px dashed var(--axis)}.st-error{background:#6250d6}.st-none{color:var(--muted)}
details{border-top:1px solid var(--grid);padding:8px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border-radius:8px;padding:8px;margin:6px 0;font-size:13px}
.k{color:var(--ink2);font-size:13px}.heat{text-align:right;font-variant-numeric:tabular-nums}.note{color:var(--ink2);font-size:13px}code{font-size:13px}svg text{fill:var(--ink2);font-size:12px}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _color(index: int) -> str:
    return f"var(--s{index % len(SERIES) + 1})"


def _radar(models: list[dict[str, Any]]) -> str:
    cx, cy, radius = 230, 220, 150
    count = len(AREAS)

    def point(index: int, value: float) -> tuple[float, float]:
        angle = -math.pi / 2 + 2 * math.pi * index / count
        return cx + radius * value / 100 * math.cos(angle), cy + radius * value / 100 * math.sin(angle)

    parts = [f'<svg viewBox="0 0 460 440" role="img" aria-label="{_e(tr("Radar of the areas"))}" style="width:100%;max-width:520px">']
    for ring in (25, 50, 75, 100):
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, ring) for i in range(count)))
        parts.append(f'<polygon points="{points}" fill="none" stroke="var(--grid)" stroke-width="1"/>')
    for i, area in enumerate(AREAS):
        x, y = point(i, 100)
        lx, ly = point(i, 118)
        anchor = "middle" if abs(lx - cx) < 12 else ("start" if lx > cx else "end")
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="var(--grid)"/>')
        parts.append(f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="{anchor}">{_e(_area(area))}</text>')
    for index, model in enumerate(models):
        values = [model["areas"].get(a) or 0 for a in AREAS]
        polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, v) for i, v in enumerate(values)))
        color = _color(index)
        parts.append(f'<polygon points="{polygon}" fill="{color}" fill-opacity=".12" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')
        for i, v in enumerate(values):
            x, y = point(i, v)
            tip = f"{model['label']} · {_area(AREAS[i])}: {_score(model['areas'].get(AREAS[i]), 0)}"
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}" stroke="var(--surface)" stroke-width="2"><title>{_e(tip)}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def _heat(score: float | None) -> str:
    """A cell of the category table: the more it is right, the deeper the blue (the figure is in the cell as well)."""
    if score is None:
        return "var(--bg)"
    share = max(0.0, min(1.0, score / 100))
    return f"color-mix(in srgb,var(--accent) {round(10 + 70 * share)}%,var(--surface))"


def _row(cells: Iterable[str], head: bool = False) -> str:
    tag = "th" if head else "td"
    return "<tr>" + "".join(f"<{tag}{c}" if c.startswith(" ") else f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def _cells(items: Iterable[tuple[str, str]]) -> str:
    """Table cells given as (attributes, content): the content is already safe HTML."""
    return "".join(f"<td{attrs}>{content}</td>" for attrs, content in items)


def _head(labels: Iterable[str], numeric_from: int = 1) -> str:
    out = []
    for i, label in enumerate(labels):
        cls = ' class="n"' if i >= numeric_from else ""
        out.append(f"<th{cls}>{_e(label)}</th>")
    return "<tr>" + "".join(out) + "</tr>"


def to_html(store: ArenaStore, run_id: str, report: dict[str, Any]) -> str:
    run, models = report["run"], report["models"]
    env, settings = run["environment"], run["settings"]
    light = "".join(f"--s{i + 1}:{c[0]};" for i, c in enumerate(SERIES))
    dark = "".join(f"--s{i + 1}:{c[1]};" for i, c in enumerate(SERIES))
    css = _CSS.replace("@@LIGHT@@", light).replace("@@DARK@@", dark)
    title = run["title"] or tr("Arena run")
    started = (run["started_at"] or run["created_at"])[:16].replace("T", " ")
    made = tr("Made with {app} {version}", app=branding.APP_NAME, version=branding.APP_VERSION)
    body: list[str] = [f"<h1>{_e(title)}</h1>"]
    body.append(f'<p class="sub">{_e(made)} · {_e(started)} · {_e(run["suite_id"])} {_e(run["suite_version"])} · '
                f'{_e(tr("seed"))} {run["seed"]} · {_e(tr("pack"))} {_e(run["preset"] or tr("custom"))}</p>')
    if report["provisional"]:
        body.append(f'<p class="note"><b>{_e(tr("The run is not over: these figures will change."))}</b></p>')
    legend = "".join(f'<span><span class="dot" style="background:{_color(i)}"></span>{_e(m["label"])}</span>' for i, m in enumerate(models))
    body.append(f'<div class="legend">{legend}</div>')

    body.append(f"<h2>{_e(tr('Ranking'))}</h2><div class=\"card\">")
    for i, m in enumerate(models):
        width = m["score"] if m["score"] is not None else (m["score_all"] or 0)
        ci = f" ({_interval(m['ci'])})" if m["ci"] else ""
        body.append(f'<div class="rank"><b>{m["rank"] or DASH}</b><span>{_e(m["label"])}</span>'
                    f'<span class="track"><span class="fill" style="width:{width:.1f}%;background:{_color(i)}"></span></span>'
                    f'<span class="num"><b>{_score(m["score"])}</b>{_e(ci)}</span></div>')
    if report["common"]:
        common = ", ".join(_category(c) for c in report["common"])
        body.append(f'<p class="note">{_e(tr("The score is the mean of the categories every model could be scored on:"))} {_e(common)}.</p>')
    body.append("</div>")

    body.append(f"<h2>{_e(tr('Areas'))}</h2><div class=\"card\" style=\"display:flex;flex-wrap:wrap;gap:16px;align-items:center\">{_radar(models)}")
    body.append('<div class="wrap" style="flex:1;min-width:260px"><table>' + _head([tr("Area"), *[m["label"] for m in models]]))
    for area in AREAS:
        body.append("<tr><td>" + _e(_area(area)) + "</td>" + "".join(f'<td class="n">{_score(m["areas"][area], 0)}</td>' for m in models) + "</tr>")
    body.append(f'</table><p class="note">{_e(tr("An axis without a score is drawn at the centre."))}</p></div></div>')

    body.append(f"<h2>{_e(tr('Categories'))}</h2><div class=\"card wrap\"><table>" + _head([tr("Category"), *[m["label"] for m in models]]))
    for c in report["categories"]:
        cells = []
        for m in models:
            entry = c["models"].get(m["key"]) or {}
            score = entry.get("score")
            text = DASH if score is None else f"{score:.0f}%"
            cells.append((f' class="heat" style="background:{_heat(score)}"', f'{text}<span class="note"> {entry.get("scored", 0)}/{c["total"]}</span>'))
        never = "" if c["scored"] else f' <span class="note">({_e(tr("never scored"))})</span>'
        body.append(f"<tr><td>{_e(_category(c['id']))}{never}</td>{_cells(cells)}</tr>")
    body.append("</table></div>")

    body.append(f"<h2>{_e(tr('What was not a right answer'))}</h2><div class=\"card wrap\"><table>")
    body.append(_head([tr("Model"), *[_status(s) for s in STATUSES]]).replace("</tr>", f"<th>{_e(tr('How the wrong answers went wrong'))}</th></tr>"))
    for m in models:
        counts = m["summary"]["counts"]
        kinds = ", ".join(f"{_kind(k)} {n}" for k, n in sorted(m["summary"]["fail_kinds"].items(), key=lambda kv: -kv[1]))
        body.append("<tr><td>" + _e(m["label"]) + "</td>" + "".join(f'<td class="n">{counts.get(s, 0)}</td>' for s in STATUSES) + f"<td>{_e(kinds)}</td></tr>")
    body.append(f'</table><p class="note">{_e(tr("Not evaluated, skipped and run errors are not wrong answers: they are left out of every score."))}</p></div>')

    for family, comparison in _quant_sections(store, run_id, report):
        reference = comparison["models"][comparison["reference"]]
        body.append(f"<h2>{_e(tr('Quantizations'))}: {_e(family['label'])}</h2><div class=\"card wrap\"><table>" + _head(_quant_head(), numeric_from=1))
        for row in _quant_rows(comparison):
            body.append("<tr>" + "".join(f"<td>{_e(c)}</td>" if i in (0, 7) else f'<td class="n">{_e(c)}</td>' for i, c in enumerate(row)) + "</tr>")
        said = tr("Compared with {model}, question by question, on the {n} questions every quantization was scored on.",
                  model=f"{reference['label']} · {reference['quant'] or DASH}", n=comparison["questions"])
        body.append(f'</table><p class="note">{_e(said)} {_e(tr("A difference is real only if its interval leaves out zero and the corrected sign test agrees; otherwise it may be noise."))}</p>')
        body.append("<table>" + _head([tr("Category"), tr("Questions"), *[comparison["models"][k]["label"] for k in comparison["order"]]]))
        for c in comparison["categories"]:
            cells = []
            for k in comparison["order"]:
                cell = c["models"][k]
                gap = "" if cell["delta"] is None else f' <span class="note">({_signed(cell["delta"], 0)})</span>'
                cells.append((f' class="heat" style="background:{_heat(cell["score"])}"', f"{_score(cell['score'], 0)}{gap}"))
            body.append(f"<tr><td>{_e(_category(c['id']))}</td><td class=\"n\">{c['n']}</td>{_cells(cells)}</tr>")
        body.append("</table></div>")

    body.append(f"<h2>{_e(tr('Questions'))}</h2><div class=\"card wrap\"><table>" + _head([tr("Question"), *[m["label"] for m in models]], numeric_from=99))
    for q in report["matrix"]:
        cells = []
        for m in models:
            cell = q["cells"].get(m["key"])
            if not cell:
                cells.append(("", '<span class="cell st-none">·</span>'))
                continue
            tip = _status(cell["status"]) + (f" · {_kind(cell['kind'])}" if cell["kind"] else "")
            cells.append(("", f'<span class="cell st-{cell["status"]}" title="{_e(tip)}">{GLYPHS.get(cell["status"], "?")}</span>'))
        body.append(f"<tr><td><code>{_e(q['id'])}</code> {_e(q['title'])}<br><span class=\"note\">{_e(_category(q['category']))}</span></td>{_cells(cells)}</tr>")
    key = "".join(f'<span><span class="cell st-{s}">{GLYPHS[s]}</span> {_e(_status(s))}</span>' for s in STATUSES)
    body.append(f'</table><div class="legend">{key}</div></div>')

    # The answers that were not right, with what was expected and why.
    questions = {q["id"]: q for q in store.all_questions(run_id)}
    labels = {m["key"]: m["label"] for m in models}
    by_question: dict[str, list[dict[str, Any]]] = {}
    for row in _results(store, run_id, report):
        if row["status"] != "passed":
            by_question.setdefault(row["question"], []).append(row)
    if by_question:
        body.append(f"<h2>{_e(tr('Answers that were not right'))}</h2><div class=\"card\">")
        for qid, rows in by_question.items():
            q = questions.get(qid, {})
            body.append(f"<details><summary><code>{_e(qid)}</code> {_e(q.get('title'))} <span class=\"note\">· {_e(_category(q.get('category', '')))}</span></summary>")
            body.append(f'<p class="k">{_e(tr("Expected"))}: {_e(q.get("expected"))}</p>')
            for row in rows:
                detail = _status(row["status"]) + (f" · {_kind(row['kind'])}" if row["kind"] else "") + (f" · {row['reason']}" if row["reason"] else "")
                excerpt = (row["answer"] or "").strip()
                excerpt = excerpt[:EXCERPT] + "…" if len(excerpt) > EXCERPT else excerpt
                body.append(f'<h3>{_e(labels.get(row["model"], row["model"]))} <span class="k">· {_e(detail)}</span></h3>')
                if excerpt:
                    body.append(f"<pre>{_e(excerpt)}</pre>")
            body.append("</details>")
        body.append("</div>")

    facts = [
        (tr("Suite"), f"{run['suite_id']} {run['suite_version']}"), (tr("hash"), run["suite_hash"]), (tr("bank"), run["bank_hash"]), (tr("seed"), run["seed"]),
        (tr("Sampling"), f"{tr('temperature')} {settings.get('temperature')}, top-p {settings.get('top_p') or DASH}, top-k {settings.get('top_k') or DASH}, "
                         f"{tr('repeat penalty')} {settings.get('repeat_penalty')}, {tr('seed')} {settings.get('seed')}"),
        (tr("repeats"), settings.get("repeats")), (tr("context"), settings.get("context")), (tr("max tokens"), settings.get("max_tokens")),
        (tr("PC"), _pc(env)), ("LM Studio", (env.get("lmstudio") or {}).get("version") or ""), (tr("Tools that checked the answers"), _tools(env)),
    ]
    body.append(f"<h2>{_e(tr('How to repeat it'))}</h2><div class=\"card wrap\"><table>")
    body += [f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in facts if v not in (None, "")]
    body.append(f"</table><h3>{_e(tr('Models'))}</h3><table>" + _head([tr("Model"), tr("Context"), tr("Load"), tr("Notes")], numeric_from=99))
    for m in models:
        info = m["info"]
        note = ", ".join(str(x) for x in (info.get("params"), info.get("quant"), tr("cloud") if m["cloud"] else None, m["error"]) if x)
        load = f"{m['load_seconds']:.1f} s" if m.get("load_seconds") else ""
        body.append(f"<tr><td>{_e(m['label'])}<br><code>{_e(m['key'])}</code></td><td>{_e(m['context_granted'] or '')}</td><td>{_e(load)}</td><td>{_e(note)}</td></tr>")
    body.append("</table></div>")

    return (f'<!doctype html><html lang="{current_language()}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{_e(title)}</title><style>{css}</style></head><body><main>{''.join(body)}</main></body></html>")


# -- entry point -------------------------------------------------------------------------------------------------


def export_run(store: ArenaStore, run_id: str, fmt: str) -> tuple[bytes, str, str] | None:
    """(the file, its media type, its name), or None if the run does not exist. ``fmt``: json, csv, md or html."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format: {fmt}")
    report = build_report(store, run_id)
    if report is None:
        return None
    builders = {"json": to_json, "csv": to_csv, "md": to_markdown, "html": to_html}
    text = builders[fmt](store, run_id, report)
    run = report["run"]
    name = f"arena-{(run['started_at'] or run['created_at'])[:10]}-{_slug(run['title'])}.{fmt}"
    data = text.encode("utf-8-sig") if fmt == "csv" else text.encode("utf-8")
    return data, FORMATS[fmt], name
