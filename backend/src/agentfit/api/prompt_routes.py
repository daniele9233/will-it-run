"""HTTP routes for the library of master prompts: the list, and the text of one prompt.

Every route is behind the same token guard as the rest of the API. They are installed before the clone routes, so that ``/api/clone/prompts`` is not
taken by ``/api/clone/{study_id}``.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agentfit.app import AppServices
from agentfit.clone import compose, library
from agentfit.clone.studio import CloneError
from agentfit.i18n import current_language, tr


class ComposeBody(BaseModel):
    picked: list[str] | None = None


class SaveBody(BaseModel):
    text: str
    used: list[str] = []
    # The id of the clone whose folder receives the files; none: the latest clone of the same site that holds a project, else the study's own folder.
    target: str | None = None


def _folder_for(svc: AppServices, study_id: str, target: str | None) -> tuple[Path, str]:
    """The project folder a save goes to, and the host of the studied site. ``CloneError`` when the study or the chosen target does not exist."""
    entries = {e["id"]: e for e in svc.clone.list()}
    study = entries.get(study_id)
    if study is None:
        raise CloneError("Clone not found")
    host = str(study.get("host") or "")
    if target is not None:
        chosen = entries.get(target)
        if chosen is None:
            raise HTTPException(status_code=404, detail=tr("The project to save into does not exist."))
        return svc.clone.workspace(chosen), host
    for entry in entries.values():  # ``list()`` is the latest first
        folder = svc.clone.workspace(entry)
        if entry["id"] != study_id and entry.get("host") == host and (folder / "package.json").is_file():
            return folder, host
    return svc.clone.workspace(study), host


def install_routes(app: FastAPI, svc: AppServices, guard: list[Any]) -> None:
    # The signature is the one of every route module. The library travels with the program and belongs to no service; ``svc`` serves only the DNA route.
    # The library is read through the module (``library.get_library()``), at the time of the request, so that a test can hand the routes another one.
    # What it returns is shared by every caller: nothing of it goes out as it is, and nothing of it is changed.
    @app.get("/api/clone/prompts", dependencies=guard)
    def prompts_list() -> dict[str, Any]:
        """Every prompt of the library, in the language of the interface: what a card shows, and the proof that it was tried (``None``: not yet)."""
        found = library.get_library()
        lang = current_language()
        return {
            "version": library.LIBRARY_VERSION,
            "prompts": [{**prompt.view(lang), "proof": copy.deepcopy(found.proofs.get(prompt.id))} for prompt in found.prompts],
        }

    @app.get("/api/clone/prompts/{prompt_id}", dependencies=guard)
    def prompt_get(prompt_id: str) -> dict[str, Any]:
        """One prompt: what a card shows, and its text on its own with every placeholder unknown (the text is English whatever the interface speaks)."""
        found = library.get_library()
        prompt = found.get(prompt_id)
        if prompt is None:
            raise HTTPException(status_code=404, detail=tr("This master prompt does not exist."))
        return {**prompt.view(current_language()), "text": library.assemble(prompt, found.shared)}

    @app.get("/api/clone/{study_id}/dna", dependencies=guard)
    def study_dna(study_id: str) -> dict[str, Any]:
        """The Site DNA a study measured. 404 when the clone does not exist or its study has not finished (no file yet)."""
        try:
            found = svc.clone.dna(study_id)
        except CloneError as exc:
            raise HTTPException(status_code=404, detail=tr("This study does not exist.")) from exc
        if found is None:
            raise HTTPException(status_code=404, detail=tr("The study has not finished yet: there is no Site DNA to show."))
        return found.model_dump(mode="json")

    @app.post("/api/clone/{study_id}/compose", dependencies=guard)
    def study_compose(study_id: str, body: ComposeBody) -> dict[str, Any]:
        """One prompt composed from the study's Site DNA: the recommendation, or exactly the prompts in ``picked`` (plus the always-on ones)."""
        try:
            found = svc.clone.dna(study_id)
        except CloneError as exc:
            raise HTTPException(status_code=404, detail=tr("This study does not exist.")) from exc
        if found is None:
            raise HTTPException(status_code=404, detail=tr("The study has not finished yet: there is no Site DNA to show."))
        try:
            return compose.compose(found, body.picked)
        except ValueError as exc:
            missing = str(exc).partition(": ")[2]
            raise HTTPException(status_code=400, detail=tr("These master prompts do not exist: {ids}", ids=missing)) from exc

    @app.post("/api/clone/{study_id}/prompts/save", dependencies=guard)
    def study_prompt_save(study_id: str, body: SaveBody) -> dict[str, Any]:
        """Save a composed prompt into the project (``docs/prompts/<host>-master.md``) and copy the skills the used prompts cite into its ``.skills/``
        and ``.claude/skills/``. A skill whose pack is not on this PC is ``missing``: nothing is fetched here (``POST /api/clone/skills/install`` does that)."""
        if not body.text.strip():
            raise HTTPException(status_code=400, detail=tr("Write the prompt text to save."))
        found = library.get_library()
        unknown = [i for i in body.used if found.get(i) is None]
        if unknown:
            raise HTTPException(status_code=400, detail=tr("These master prompts do not exist: {ids}", ids=", ".join(unknown)))
        try:
            folder, host = _folder_for(svc, study_id, body.target)
        except CloneError as exc:
            raise HTTPException(status_code=404, detail=tr("This study does not exist.")) from exc
        names = [skill for i in body.used if (prompt := found.get(i)) is not None for skill in prompt.skills]
        path = compose.save(folder, host, body.text)
        skills = svc.skills.install_into(folder, list(dict.fromkeys(names)))
        return {"file": path.relative_to(folder).as_posix(), "folder": str(folder), "skills": skills}
