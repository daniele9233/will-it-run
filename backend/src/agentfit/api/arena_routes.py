"""HTTP routes for the Arena: what a run could ask, the models it could ask, the runs and their reports, and the files.

Every route is behind the same token guard as the rest of the API. A request that needs the person's agreement (models to
unload, a cloud model that costs money) is answered with 409 and a ``code`` that says which, and is sent again with the
agreement in it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from agentfit.app import AppServices
from agentfit.arena import SUITE_ID, SUITE_VERSION, quants
from agentfit.arena.categories import AREAS, CATEGORIES, GROUPS, PRESETS
from agentfit.arena.export import FORMATS, export_run
from agentfit.arena.report import build_report, render_verdict
from agentfit.arena.runner import ArenaError, RunRequest
from agentfit.arena.suite import CANONICAL_SEED, build_suite
from agentfit.i18n import tr


class ResumeBody(BaseModel):
    confirm_unload: bool = False


class RenameBody(BaseModel):
    title: str | None = None
    note: str | None = None


class SaveBody(BaseModel):
    format: str
    # Where the person chose to save it, in the window's Save dialog.
    path: str


class ManualBody(BaseModel):
    model: str
    question: str
    repeat: int = 0
    # "passed" or "failed" is a person's own reading of an answer no code could judge. None takes the mark away.
    mark: str | None = None
    note: str = ""


def install_routes(app: FastAPI, svc: AppServices, guard: list[Any]) -> None:
    @app.exception_handler(ArenaError)
    async def _arena_error(_: Request, exc: ArenaError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc), "code": exc.code, **exc.extra})

    # -- what a run could ask -------------------------------------------------
    @app.get("/api/arena/overview", dependencies=guard)
    def arena_overview(seed: int = CANONICAL_SEED) -> dict[str, Any]:
        full = build_suite(seed, "full")
        counts = {name: sum(1 for q in full.questions if q.tier <= tier) for name, tier in PRESETS.items()}
        return {
            "suite": {"id": SUITE_ID, "version": SUITE_VERSION, "seed": seed, "canonical_seed": CANONICAL_SEED, "bank_hash": full.bank_hash},
            "presets": counts,
            "categories": [
                {"id": c.id, "group": c.group, "area": c.area, "scored": c.scored, "needs": list(c.needs), "counts": list(c.counts)}
                for c in CATEGORIES.values()
            ],
            "groups": list(GROUPS), "areas": list(AREAS), "tools": svc.arena.tools.report(), "busy": svc.arena.busy(),
        }

    @app.get("/api/arena/models", dependencies=guard)
    def arena_models() -> dict[str, Any]:
        try:
            loaded = [{"instance_id": i.instance_id, "model_key": i.model_key, "type": i.type} for i in svc.runtime.list_loaded()]
        except Exception:
            loaded = []
        held = {i["model_key"] for i in loaded}
        installed = [m for m in svc.installed_models() if m.type == "llm"]
        # Which of them are one model in different quantizations: the form suggests how to compare those.
        families = {
            c["key"]: c["family"]
            for c in quants.candidates([
                {"model_key": m.model_key, "label": m.display_name or m.model_key, "cloud": False, "position": i,
                 "info": {"quant": m.quant, "params": m.params_string, "architecture": m.architecture, "size_bytes": m.size_bytes}}
                for i, m in enumerate(installed)
            ])
        }
        local = [
            {"key": m.model_key, "label": m.display_name or m.model_key, "params": m.params_string, "quant": m.quant, "size_bytes": m.size_bytes,
             "vision": bool(m.vision), "tool_use": bool(m.tool_use), "reasoning": bool(m.reasoning), "max_context": m.max_context_length,
             "architecture": m.architecture, "loaded": m.model_key in held, "family": families.get(m.model_key)}
            for m in installed
        ]
        cloud = [
            {"key": c["key"], "label": f"{c['provider_label']} · {c['label']}", "vision": bool(c.get("vision")), "provider": c["provider"], "context": c.get("context")}
            for c in svc.cloud.available()
        ]
        return {"local": local, "cloud": cloud, "loaded": [i for i in loaded if i["type"] == "llm"], "busy": svc.arena.busy()}

    @app.post("/api/arena/plan", dependencies=guard)
    def arena_plan(body: RunRequest) -> dict[str, Any]:
        return svc.arena.describe(body)

    # -- runs ----------------------------------------------------------------
    @app.get("/api/arena/runs", dependencies=guard)
    def arena_runs() -> dict[str, Any]:
        rows = []
        for run in svc.arena.store.list_runs():
            models = svc.arena.store.models(run["id"])
            rows.append({
                "id": run["id"], "title": run["title"], "status": run["status"], "created_at": run["created_at"], "started_at": run["started_at"],
                "finished_at": run["finished_at"], "suite_version": run["suite_version"], "seed": run["seed"], "preset": run["preset"], "total": run["total"],
                "done": run["done"], "models": [{"key": m["model_key"], "label": m["label"], "status": m["status"], "cloud": m["cloud"]} for m in models],
                "note": run["note"],
            })
        return {"runs": rows, "busy": svc.arena.busy()}

    @app.post("/api/arena/runs", dependencies=guard)
    def arena_start(body: RunRequest) -> dict[str, Any]:
        return {"id": svc.arena.start(body)}

    @app.get("/api/arena/runs/{run_id}", dependencies=guard)
    def arena_run(run_id: str) -> dict[str, Any]:
        report = build_report(svc.arena.store, run_id)
        if report is None:
            raise HTTPException(status_code=404, detail=tr("This run does not exist."))
        return report

    @app.get("/api/arena/runs/{run_id}/quants", dependencies=guard)
    def arena_quants(run_id: str, family: str | None = None, models: str | None = None, reference: str | None = None) -> dict[str, Any]:
        """The quantizations of one model compared: ``family`` picks one of those found, ``models`` (comma-separated) chooses by hand."""
        picked = [m for m in (models or "").split(",") if m]
        result = quants.compare(svc.arena.store, run_id, members=picked or None, reference=reference, family=family)
        if result is None:
            raise HTTPException(status_code=404, detail=tr("This run does not exist."))
        return result

    @app.post("/api/arena/runs/{run_id}/stop", dependencies=guard)
    def arena_stop(run_id: str) -> dict[str, Any]:
        return {"stopping": svc.arena.stop(run_id)}

    @app.post("/api/arena/runs/{run_id}/resume", dependencies=guard)
    def arena_resume(run_id: str, body: ResumeBody | None = None) -> dict[str, Any]:
        svc.arena.resume(run_id, confirm_unload=bool(body and body.confirm_unload))
        return {"id": run_id}

    @app.patch("/api/arena/runs/{run_id}", dependencies=guard)
    def arena_rename(run_id: str, body: RenameBody) -> dict[str, Any]:
        if svc.arena.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail=tr("This run does not exist."))
        changes = {k: v for k, v in (("title", body.title), ("note", body.note)) if v is not None}
        svc.arena.store.update_run(run_id, **changes)
        return {"id": run_id, **changes}

    @app.delete("/api/arena/runs/{run_id}", dependencies=guard)
    def arena_delete(run_id: str) -> dict[str, Any]:
        if not svc.arena.delete(run_id):
            raise HTTPException(status_code=404, detail=tr("This run does not exist."))
        return {"deleted": run_id}

    # -- one question, and what every model answered ------------------------------------
    @app.get("/api/arena/runs/{run_id}/questions/{question_id}", dependencies=guard)
    def arena_question(run_id: str, question_id: str) -> dict[str, Any]:
        question = svc.arena.store.question(run_id, question_id)
        if question is None:
            raise HTTPException(status_code=404, detail=tr("This question is not in the run."))
        answers: dict[str, list[dict[str, Any]]] = {}
        for row in svc.arena.store.results(run_id, question_id=question_id):
            verdict = render_verdict(row["verdict"])
            answers.setdefault(row["model_key"], []).append({
                "repeat": row["repeat"], "status": row["status"], "score": row["score"], "kind": row["kind"], "method": row["method"],
                "reason": verdict.get("reason"), "checks": verdict.get("checks"), "extracted": verdict.get("extracted"), "expected": verdict.get("expected"),
                "format_ok": verdict.get("format_ok", True), "answer": row["answer"], "reasoning": row["reasoning"], "tool_calls": row["tool_calls"],
                "finish_reason": row["finish_reason"], "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                "reasoning_tokens": row["reasoning_tokens"], "ttft_s": row["ttft_s"], "duration_s": row["duration_s"], "tps": row["tps"],
                "error": row["error"], "manual": row["manual"], "manual_note": row["manual_note"],
            })
        shown = {k: v for k, v in question.items() if k not in ("check", "images", "attachments")}
        shown["check_type"] = (question.get("check") or {}).get("type")
        shown["images"] = [{"name": i["name"], "width": i["width"], "height": i["height"], "sha256": i["sha256"], "data_url": f"data:image/png;base64,{i['data_b64']}"} for i in question.get("images", [])]
        shown["attachments"] = [{"name": a["name"], "mime": a["mime"], "pages": a["pages"], "sha256": a["sha256"], "data_b64": a["data_b64"]} for a in question.get("attachments", [])]
        return {"question": shown, "answers": answers}

    @app.post("/api/arena/runs/{run_id}/manual", dependencies=guard)
    def arena_manual(run_id: str, body: ManualBody) -> dict[str, Any]:
        if body.mark not in (None, "passed", "failed"):
            raise HTTPException(status_code=400, detail=tr("A mark is passed, failed, or nothing."))
        if not svc.arena.store.set_manual(run_id, body.model, body.question, body.repeat, body.mark, body.note):
            raise HTTPException(status_code=404, detail=tr("There is no such answer."))
        return {"ok": True}

    # -- files ----------------------------------------------------------------
    @app.get("/api/arena/runs/{run_id}/export", dependencies=guard)
    def arena_export(run_id: str, format: str = "html") -> Response:
        if format not in FORMATS:
            raise HTTPException(status_code=400, detail=tr("Choose one of: {formats}.", formats=", ".join(FORMATS)))
        exported = export_run(svc.arena.store, run_id, format)
        if exported is None:
            raise HTTPException(status_code=404, detail=tr("This run does not exist."))
        data, media_type, name = exported
        return Response(content=data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.post("/api/arena/runs/{run_id}/export", dependencies=guard)
    def arena_export_save(run_id: str, body: SaveBody) -> dict[str, Any]:
        """The same file, written where the person chose to put it."""
        if body.format not in FORMATS:
            raise HTTPException(status_code=400, detail=tr("Choose one of: {formats}.", formats=", ".join(FORMATS)))
        target = Path(body.path)
        if not target.is_absolute() or not target.parent.is_dir():
            raise HTTPException(status_code=400, detail=tr("That place does not exist: {path}", path=body.path))
        exported = export_run(svc.arena.store, run_id, body.format)
        if exported is None:
            raise HTTPException(status_code=404, detail=tr("This run does not exist."))
        data, _, _ = exported
        try:
            target.write_bytes(data)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=tr("The file could not be saved: {error}", error=str(exc))) from exc
        return {"path": str(target), "bytes": len(data)}
