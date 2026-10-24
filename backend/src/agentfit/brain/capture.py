"""What a run leaves in the Project Brain, worked out from what happened in it.

Nothing here asks a model to summarise. The recorder watches the events of a run (tool
calls with their exit codes, the typed outputs of the agents, the user's decisions at a
gate) and writes down what they establish:

* which files were written or deleted, and why (the purpose the agent gave for each);
* a check that failed and later passed, as an error with the fix that came between;
  a check that never passed, as an error still open;
* the plan and the design the team settled on, and each design decision by itself;
* what the user said when approving or rejecting;
* the request and how it ended.

An agent adds to this with the ``remember`` tool when it knows something the events do
not show.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agentfit.brain import text

# Output fields that are a verdict, and the value that means "this is fine".
_VERDICTS = (("passed", True), ("approved", True), ("complete", True), ("success", True), ("needs_rewrite", False))
# Output fields that say what was wrong.
_PROBLEM_FIELDS = (
    "failures", "errors", "findings", "requested_changes", "required_changes", "security_issues", "inaccuracies",
    "missing_topics", "bottlenecks", "remaining",
)
_EXIT_CODE = re.compile(r"exit code (-?\d+)")
MAX_PROBLEMS = 6
MAX_DECISIONS = 8


@dataclass
class Draft:
    """A memory about to be written, with the key other drafts use to link to it."""

    kind: str
    title: str
    body: str
    files: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "engine"
    agent: str = ""
    key: str = ""
    # Key of the error draft this one fixes.
    fixes: str = ""


@dataclass
class _Problem:
    node: str
    role: str
    title: str
    detail: str
    tags: list[str]
    # Position in the timeline when it was first seen, and when it was seen to be solved.
    opened: int
    closed: int | None = None


def _short(value: Any, limit: int) -> str:
    cleaned = " ".join(str(value or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _lines(items: Any, limit: int = 12, width: int = 300) -> str:
    out = []
    for item in list(items or [])[:limit]:
        if isinstance(item, dict):
            where = item.get("location") or item.get("path") or item.get("title") or ""
            what = item.get("issue") or item.get("detail") or item.get("purpose") or item.get("statement") or ""
            entry = f"{where}: {what}" if where and what else str(where or what or item)
        else:
            entry = str(item)
        if entry.strip():
            out.append(f"- {_short(entry, width)}")
    return "\n".join(out)


def final_text(result: dict[str, Any] | None) -> str:
    """What a run delivered, in the words of its last step."""
    final = (result or {}).get("final")
    if isinstance(final, dict):
        for key in ("summary", "answer", "text", "markdown", "objective", "title"):
            if isinstance(final.get(key), str) and final[key].strip():
                return final[key].strip()
    return ""


class Recorder:
    def __init__(self, *, task: str, workflow_name: str, roles: dict[str, str], schemas: dict[str, str], conversation: bool = False) -> None:
        self.task = task.strip()
        self.workflow_name = workflow_name
        self.roles = roles
        self.schemas = schemas
        # A chat turn: only what it changed or fixed is worth keeping, not that a question was asked.
        self.conversation = conversation
        # path -> {"agent": role, "deleted": bool}
        self.changed: dict[str, dict[str, Any]] = {}
        # What happened, in order: ("file", path) and ("work", node, summary).
        self.timeline: list[tuple[str, ...]] = []
        self.outputs: dict[str, dict[str, Any]] = {}
        self.problems: list[_Problem] = []
        self._open: dict[str, _Problem] = {}
        self.human: list[tuple[str, bool, str]] = []
        self.failed_steps: list[str] = []

    # -- watching -----------------------------------------------------------
    def observe(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "tool.call":
            self._tool(payload)
        elif event_type == "node.completed" and payload.get("kind") != "check":
            self._output(str(payload.get("node") or ""), payload.get("output"))
        elif event_type == "approval.decided":
            comment = str(payload.get("comment") or "").strip()
            if payload.get("kind") == "gate" and comment and not payload.get("automatic"):
                self.human.append((str(payload.get("prompt") or ""), bool(payload.get("approved")), comment))
        elif event_type == "node.failed":
            self.failed_steps.append(f"{payload.get('role') or payload.get('node')}: {_short(payload.get('error'), 200)}")

    def _role(self, node: str) -> str:
        return self.roles.get(node, node)

    def _tool(self, payload: dict[str, Any]) -> None:
        tool, status, node = payload.get("tool"), payload.get("status"), str(payload.get("node") or "")
        arguments = payload.get("arguments") or {}
        if tool in ("write_file", "delete_file") and status == "success":
            path = text.clean_path(str(arguments.get("path") or ""))
            if path:
                self.changed[path] = {"agent": self._role(node), "deleted": tool == "delete_file"}
                self.timeline.append(("file", path))
        elif tool == "run_tests" and status in ("success", "error"):
            output = str(payload.get("output") or "")
            code = _EXIT_CODE.match(output)
            if code is None or int(code.group(1)) == 5:  # 5: pytest found no tests, which is not a failure of the code
                return
            if int(code.group(1)) == 0:
                self._solved(node)
                return
            closing = next((line.strip(" =") for line in reversed(output.splitlines()) if line.strip(" =")), "tests failed")
            self._problem(node, f"Tests failed: {_short(closing, 140)}", output[-1800:].strip(), ["tests"])

    def _output(self, node: str, output: Any) -> None:
        if not node or not isinstance(output, dict):
            return
        self.outputs[node] = output
        summary = output.get("summary")
        if isinstance(summary, str) and summary.strip():
            self.timeline.append(("work", node, summary.strip()))
        for name, good in _VERDICTS:
            if isinstance(output.get(name), bool):
                if output[name] == good:
                    self._solved(node)
                else:
                    detail = "\n".join(
                        f"{part.replace('_', ' ').capitalize()}:\n{_lines(output.get(part))}" for part in _PROBLEM_FIELDS if output.get(part)
                    )
                    self._problem(node, f"{self._role(node)}: {_short(summary or 'did not pass', 140)}", detail, [])
                return

    def _problem(self, node: str, title: str, detail: str, tags: list[str]) -> None:
        known = self._open.get(node)
        if known is not None:
            if tags or not known.tags:
                # The same report again: where it started is kept, the latest account replaces the earlier one.
                known.title, known.detail = title, detail or known.detail
                known.tags = known.tags or list(tags)
            elif detail and detail not in known.detail:
                # The agent's own account of a failed test run, under what the run printed.
                known.detail = f"{known.detail}\n\n{detail}"
            return
        if len(self.problems) >= MAX_PROBLEMS:
            return
        problem = _Problem(node=node, role=self._role(node), title=title, detail=detail, tags=list(tags), opened=len(self.timeline))
        self._open[node] = problem
        self.problems.append(problem)

    def _solved(self, node: str) -> None:
        problem = self._open.pop(node, None)
        if problem is not None:
            problem.closed = len(self.timeline)

    # -- writing ------------------------------------------------------------
    def _purposes(self) -> dict[str, str]:
        """Why each file was touched, where an agent's output said so."""
        purposes: dict[str, str] = {}
        for output in self.outputs.values():
            for item in output.get("files") or []:
                if isinstance(item, dict) and item.get("path") and item.get("purpose"):
                    purposes[text.clean_path(str(item["path"]))] = _short(item["purpose"], 200)
        return purposes

    def drafts(self, status: str, error: str | None, result: dict[str, Any] | None) -> list[Draft]:
        """Everything this run established, ready to be stored. The session comes first."""
        outputs = {**self.outputs, **{k: v for k, v in ((result or {}).get("outputs") or {}).items() if isinstance(v, dict)}}
        self.outputs = outputs
        drafts: list[Draft] = []
        drafts += self._design_drafts(outputs)
        drafts += self._change_drafts()
        drafts += self._problem_drafts()
        for prompt, approved, comment in self.human:
            verdict = "approved" if approved else "rejected"
            drafts.append(Draft(
                kind="decision", source="user", agent="You",
                title=f"You {verdict}: {_short(comment, 100)}",
                body=f"At the gate \"{_short(prompt, 200)}\" you {verdict} the work and wrote:\n{comment}",
            ))
        if self.conversation and not drafts:
            return []
        return [self._session_draft(status, error, result, outputs), *drafts]

    def _session_draft(self, status: str, error: str | None, result: dict[str, Any] | None, outputs: dict[str, Any]) -> Draft:
        lines = [f"Request: {_short(self.task, 700)}", f"Workflow: {self.workflow_name}. It ended: {status.replace('_', ' ')}."]
        if error and status != "completed":
            lines.append(f"Why: {_short(error, 400)}")
        delivered = final_text(result)
        if delivered:
            lines.append(f"Result: {_short(delivered, 900)}")
        for output in outputs.values():
            if output.get("objective"):
                lines.append(f"Objective: {_short(output['objective'], 300)}")
                for name in ("constraints", "acceptance_criteria", "out_of_scope"):
                    if output.get(name):
                        lines.append(f"{name.replace('_', ' ').capitalize()}:\n{_lines(output[name], 8, 200)}")
                break
        open_items: list[str] = []
        for output in outputs.values():
            for name in ("follow_ups", "pending_manual_steps", "unverified", "open_questions"):
                open_items += [str(item) for item in output.get(name) or []]
        if open_items:
            lines.append("Left open:\n" + _lines(list(dict.fromkeys(open_items)), 10, 220))
        if self.failed_steps:
            lines.append("Steps that failed:\n" + _lines(self.failed_steps, 6))
        if self.changed:
            lines.append(f"Files changed: {len(self.changed)}.")
        return Draft(kind="session", title=_short(self.task, 100) or "Session", body="\n".join(lines), files=sorted(self.changed)[:40], key="session")

    def _design_drafts(self, outputs: dict[str, Any]) -> list[Draft]:
        drafts: list[Draft] = []
        for node, output in outputs.items():
            schema, role = self.schemas.get(node, ""), self._role(node)
            if schema == "ArchitectureDesign":
                body = [str(output.get("summary") or "")]
                for name in ("components", "risks", "rollout_steps"):
                    if output.get(name):
                        body.append(f"{name.replace('_', ' ').capitalize()}:\n{_lines(output[name])}")
                if output.get("rollback_plan"):
                    body.append(f"Rollback: {_short(output['rollback_plan'], 400)}")
                drafts.append(Draft(kind="architecture", agent=role, title=f"Design: {_short(output.get('summary'), 100)}", body="\n".join(body)))
                for decision in list(output.get("decisions") or [])[:MAX_DECISIONS]:
                    if str(decision).strip():
                        drafts.append(Draft(kind="decision", agent=role, title=_short(decision, 110), body=str(decision).strip()))
            elif schema == "Plan":
                steps = [
                    f"{n}. {_short(step.get('title'), 120)}" + (f": {_short(step.get('detail'), 220)}" if step.get("detail") else "")
                    for n, step in enumerate(output.get("steps") or [], 1) if isinstance(step, dict)
                ]
                body = [str(output.get("summary") or ""), *steps[:20]]
                if output.get("risks"):
                    body.append("Risks:\n" + _lines(output["risks"], 8))
                named = [str(item) for item in output.get("files_or_components") or []]
                drafts.append(Draft(
                    kind="decision", agent=role, title=f"Plan: {_short(output.get('summary'), 100)}", body="\n".join(body),
                    files=[text.clean_path(n) for n in named if text.looks_like_path(n)][:30],
                    tags=[_short(n, 40) for n in named if not text.looks_like_path(n)][:8],
                ))
            elif schema == "SceneDesign":
                body = [str(output.get("concept") or "")]
                for name in ("scene_graph", "assets", "work_packages"):
                    if output.get(name):
                        body.append(f"{name.replace('_', ' ').capitalize()}:\n{_lines(output[name])}")
                if output.get("performance_budget"):
                    body.append(f"Performance budget: {_short(output['performance_budget'], 300)}")
                drafts.append(Draft(kind="architecture", agent=role, title=f"Scene design: {_short(output.get('concept'), 90)}", body="\n".join(body)))
            elif schema == "EditorialBrief":
                body = [f"Premise: {output.get('premise', '')}", f"Audience: {output.get('audience', '')}", f"Tone: {output.get('tone', '')}"]
                for name in ("structure", "constraints"):
                    if output.get(name):
                        body.append(f"{name.capitalize()}:\n{_lines(output[name])}")
                drafts.append(Draft(kind="decision", agent=role, title=f"Editorial brief: {_short(output.get('premise'), 90)}", body="\n".join(body)))
        return drafts

    def _change_drafts(self) -> list[Draft]:
        if not self.changed:
            return []
        purposes = self._purposes()
        rows = []
        for path in sorted(self.changed)[:60]:
            info = self.changed[path]
            action = "deleted" if info["deleted"] else purposes.get(path, "written")
            rows.append(f"- {path}: {action} ({info['agent']})")
        count = len(self.changed)
        agents = ", ".join(dict.fromkeys(str(info["agent"]) for info in self.changed.values()))
        return [Draft(
            kind="change", agent=agents, key="change",
            title=f"{count} file{'s' if count != 1 else ''} changed for: {_short(self.task, 90)}",
            body="\n".join(rows), files=sorted(self.changed)[:60],
        )]

    def _between(self, start: int, end: int | None) -> tuple[list[str], list[str]]:
        """(files written, summaries of the work done) between two points of the run."""
        files: list[str] = []
        work: list[str] = []
        for item in self.timeline[start:end]:
            if item[0] == "file":
                files.append(item[1])
            else:
                work.append(f"{self._role(item[1])}: {_short(item[2], 400)}")
        return list(dict.fromkeys(files)), list(dict.fromkeys(work))

    def _problem_drafts(self) -> list[Draft]:
        drafts: list[Draft] = []
        for index, problem in enumerate(self.problems):
            key = f"error{index}"
            fixed = problem.closed is not None
            state = "Fixed in the same session." if fixed else "Still open when the session ended."
            files, work = self._between(problem.opened, problem.closed)
            drafts.append(Draft(
                kind="error", agent=problem.role, key=key, title=problem.title,
                body="\n\n".join(part for part in (problem.detail, state) if part),
                files=files[:20], tags=[*problem.tags, *([] if fixed else ["open"])],
            ))
            if fixed and (files or work):
                body = []
                if work:
                    body.append("What was done:\n" + "\n".join(f"- {line}" for line in work[-4:]))
                if files:
                    body.append("Files changed to fix it: " + ", ".join(files[:20]))
                drafts.append(Draft(
                    kind="solution", agent=problem.role, fixes=key, title=f"Fix for: {_short(problem.title, 100)}",
                    body="\n".join(body), files=files[:20], tags=list(problem.tags),
                ))
        return drafts
