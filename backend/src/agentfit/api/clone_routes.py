"""HTTP routes for the site cloner: what is installed, the clones, their files, the agent's question and the result.

Every route is behind the same token guard as the rest of the API.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from agentfit.app import AppServices
from agentfit.clone.browser import find_browser
from agentfit.clone.install import CloneSetupError
from agentfit.clone.skills import PACK_BY_ID
from agentfit.clone.studio import CloneError
from agentfit.cloud import is_cloud, parse_key
from agentfit.workflows import sources
from agentfit.workflows.runner import RunError


class CloneBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    # The website's address. Not needed for a new run of an existing clone.
    url: str = ""
    model_key: str
    # A model that can see, used by the look tool when the team's model cannot. Empty: none.
    vision_key: str = ""
    language: str = "en"
    workspace: str | None = None
    repo_url: str | None = None
    repo_branch: str | None = None
    protect_existing: bool = True
    approval: Literal["ask", "bypass"] = "ask"
    # Extra instructions from the person ("keep the dark theme", "only the home page").
    extra: str = ""
    # "replica": an exact copy of the page as a browser rendered it, measured against the original. "agent": a model rebuilds it
    # following the cloning kit's skill. A new run of a clone keeps the mode it was made in.
    mode: Literal["replica", "agent"] = "replica"


class AnswerBody(BaseModel):
    answer: str


class SkillsBody(BaseModel):
    # The packs to fetch; none given: all of them.
    packs: list[str] | None = None


class PictureFile(BaseModel):
    name: str = "reference.png"
    # The picture as base64: what the browser's file chooser gives, so no multipart upload is needed.
    data: str


class PictureBody(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    reference: PictureFile
    # What the person wants, in their words: the site, its content, its motion.
    brief: str = ""
    model_key: str
    # A model that sees, when the one above cannot: the whole job is done by it.
    vision_key: str = ""
    language: str = "en"
    # The skills copied into the project for the model to read; none given: every skill that is installed.
    skills: list[str] | None = None
    approval: Literal["ask", "bypass"] = "ask"


def install_routes(app: FastAPI, svc: AppServices, guard: list[Any]) -> None:
    def setup() -> dict[str, Any]:
        status = svc.browser_install.status()
        browser = find_browser()
        problems = []
        if not status["node"]["ok"]:
            problems.append(status["node"]["detail"] or "Node.js is needed.")
        if not status["npm"]:
            problems.append("npm is not on this PC. It comes with Node.js.")
        if browser is None:
            problems.append("Neither Chrome nor Edge was found on this PC.")
        # Only the agent that follows the kit's skill fetches the kit, and needs Git for it.
        kit_problems = [] if status["git"] else ["Git is not installed: the cloning kit is fetched with it."]
        ready = status["installed"] and not problems
        return {**status, "browser": browser, "kit": svc.kits.describe(), "problems": problems, "kit_problems": kit_problems,
                "ready": ready, "agent_ready": ready and not kit_problems}

    def model_sees(model_key: str) -> bool:
        if is_cloud(model_key):
            try:
                provider, _ = parse_key(model_key)
            except ValueError:
                return False
            return provider.vision
        return any(m.model_key == model_key and bool(m.vision) for m in svc.installed_models())

    def check_model(model_key: str) -> None:
        if is_cloud(model_key):
            try:
                provider, _ = parse_key(model_key)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not svc.cloud.key_source(provider)[0] and provider.wire != "anthropic":
                raise HTTPException(status_code=409, detail=f"No API key for {provider.label}. Add one in Settings > Cloud models.")
            return
        if not any(m.model_key == model_key for m in svc.installed_models()):
            raise HTTPException(status_code=409, detail="Choose a model that is installed in LM Studio, or a cloud model.")
        if not svc.runtime.ensure_server():
            raise HTTPException(status_code=503, detail="LM Studio's local server is not running and could not be started.")

    @app.get("/api/clone", dependencies=guard)
    def clone_list() -> dict[str, Any]:
        return {"clones": svc.clone.list(), "setup": setup()}

    @app.post("/api/clone/install", dependencies=guard)
    def clone_install() -> dict[str, Any]:
        try:
            return {"job": svc.browser_install.install()}
        except CloneSetupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/clone/install/cancel", dependencies=guard)
    def clone_install_cancel() -> dict[str, Any]:
        return {"cancelled": svc.browser_install.cancel()}

    @app.get("/api/clone/skills", dependencies=guard)
    def skills_status() -> dict[str, Any]:
        """The agent skills: which packs are on this PC (GSAP, frontend design, Vercel, Three.js, Playwright), at which commit and licence."""
        return svc.skills.status()

    @app.post("/api/clone/skills/install", dependencies=guard)
    def skills_install(body: SkillsBody) -> dict[str, Any]:
        """Fetch the skill packs at their pinned commits, in the background; ``GET /api/clone/skills`` follows the job."""
        unknown = [i for i in body.packs or [] if i not in PACK_BY_ID]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown skill pack: {', '.join(unknown)}.")
        return {"job": svc.skills.install_in_background(body.packs or None)}

    def clone_start(study_id: str | None, body: CloneBody) -> dict[str, Any]:
        check_model(body.model_key)
        if svc.browser_install.installed_version() is None:
            raise HTTPException(status_code=409, detail="The browser engine is not installed yet: press Prepare.")
        node = svc.browser_install.node()
        if not node["ok"]:
            raise HTTPException(status_code=409, detail=node["detail"] or "Node.js is needed.")
        if find_browser() is None:
            raise HTTPException(status_code=409, detail="Neither Chrome nor Edge was found on this PC: install one of them.")
        sees = model_sees(body.model_key)
        vision_key = ""
        if not sees and body.vision_key:
            check_model(body.vision_key)
            if not model_sees(body.vision_key):
                raise HTTPException(status_code=400, detail="That model cannot see pictures: choose one with vision.")
            vision_key = body.vision_key
        try:
            place = sources.resolve(folder=body.workspace, repo_url=body.repo_url, repo_branch=body.repo_branch,
                                    protect_existing=body.protect_existing)
        except sources.SourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            entry, record = svc.clone.start(
                body.url, model_key=body.model_key, sees=sees, vision_key=vision_key, language=body.language,
                workspace=place.workspace, protect_existing=place.protect_existing, source=place.source, approval=body.approval,
                extra=body.extra, study_id=study_id, mode=body.mode,
            )
        except (CloneError, RunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"study": entry["id"], "run": record.id}

    @app.post("/api/clone/picture", dependencies=guard)
    def clone_picture(body: PictureBody) -> dict[str, Any]:
        """Build a site from a picture: a model that sees writes it region by region, and the page is measured against the picture."""
        check_model(body.model_key)
        if svc.browser_install.installed_version() is None:
            raise HTTPException(status_code=409, detail="The browser engine is not installed yet: press Prepare.")
        if not svc.browser_install.node()["ok"] or find_browser() is None:
            raise HTTPException(status_code=409, detail="Node.js, and Chrome or Edge, are needed.")
        _ = svc.skills  # the run copies the skills it finds there into the project
        sees = model_sees(body.model_key)
        vision_key = ""
        if not sees and body.vision_key:
            check_model(body.vision_key)
            if not model_sees(body.vision_key):
                raise HTTPException(status_code=400, detail="That model cannot see pictures: choose one with vision.")
            vision_key = body.vision_key
        try:
            entry, record = svc.clone.start_picture(body.reference.model_dump(), brief=body.brief, model_key=body.model_key, sees=sees, vision_key=vision_key,
                                                   language=body.language, skills=body.skills, approval=body.approval)
        except (CloneError, RunError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"study": entry["id"], "run": record.id}

    @app.post("/api/clone", dependencies=guard)
    def clone_create(body: CloneBody) -> dict[str, Any]:
        if not body.url.strip():
            raise HTTPException(status_code=400, detail="Write the address of a website.")
        return clone_start(None, body)

    @app.post("/api/clone/{study_id}/runs", dependencies=guard)
    def clone_again(study_id: str, body: CloneBody) -> dict[str, Any]:
        return clone_start(study_id, body)

    @app.get("/api/clone/{study_id}", dependencies=guard)
    def clone_get(study_id: str) -> dict[str, Any]:
        try:
            return svc.clone.get(study_id)
        except CloneError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/clone/{study_id}/answer", dependencies=guard)
    def clone_answer(study_id: str, body: AnswerBody) -> dict[str, Any]:
        """The person's answer to the question the agent is asking (which pages to clone, for one)."""
        try:
            if not svc.clone.answer(study_id, body.answer):
                raise HTTPException(status_code=409, detail="Nothing is waiting for an answer.")
        except CloneError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/clone/{study_id}", dependencies=guard)
    def clone_delete(study_id: str) -> dict[str, Any]:
        try:
            svc.clone.delete(study_id)
        except CloneError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": study_id}

    @app.get("/api/clone/{study_id}/file", dependencies=guard)
    def clone_file(study_id: str, path: str) -> FileResponse:
        try:
            target = svc.clone.file(study_id, path)
        except CloneError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(target, media_type=svc.clone.media(target), headers={"Cache-Control": "no-store"})

    @app.post("/api/clone/{study_id}/preview", dependencies=guard)
    def clone_preview(study_id: str) -> dict[str, Any]:
        """Serve the project on 127.0.0.1 so the clone opens in the browser like a real site."""
        try:
            return {"url": svc.clone.preview(study_id)}
        except CloneError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
