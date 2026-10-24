"""Persistence for workflows: built-in templates plus user workflows in SQLite."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from agentfit.db import Database, utcnow
from agentfit.i18n import tr
from agentfit.teams import templates as T
from agentfit.workflows.dsl import WorkflowDef, dump_yaml, load_yaml


class WorkflowInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    source: str  # "template" or "user"
    agents: int = 0
    models: int = 0
    runnable: bool = False
    updated_at: str | None = None
    category: str = "team"
    checks: int = 0
    # True when the workflow builds an evidence dossier, so a run can be given a documents folder.
    evidence: bool = False


def _shape(definition: WorkflowDef) -> dict[str, Any]:
    return {
        "category": definition.workflow.category,
        "checks": len(definition.checks),
        "evidence": any(c.kind == "evidence_dossier" for c in definition.checks),
    }


class WorkflowStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def templates(self) -> list[WorkflowDef]:
        return [factory() for factory in T.BUILTIN.values()]

    def list(self) -> list[WorkflowInfo]:
        out: list[WorkflowInfo] = []
        for row in self.db.query("SELECT id, name, description, yaml, updated_at FROM workflow ORDER BY updated_at DESC"):
            try:
                definition = load_yaml(row["yaml"])
            except Exception:
                continue
            out.append(
                WorkflowInfo(
                    id=row["id"], name=row["name"], description=row["description"] or "", source="user",
                    agents=len(definition.agents), models=len(definition.models),
                    runnable=all(s.model_key or s.repo for s in definition.models.values()),
                    updated_at=row["updated_at"], **_shape(definition),
                )
            )
        for definition in self.templates():
            meta = definition.workflow
            out.append(
                WorkflowInfo(
                    # Template names and descriptions are shown to the user; the workflow itself stays English.
                    id=f"template:{meta.id}", name=tr(meta.name), description=tr(meta.description), source="template",
                    agents=len(definition.agents), models=len(definition.models), **_shape(definition),
                )
            )
        return out

    def get(self, workflow_id: str) -> WorkflowDef | None:
        if workflow_id.startswith("template:"):
            factory = T.BUILTIN.get(workflow_id.split(":", 1)[1])
            return factory() if factory else None
        row = self.db.query_one("SELECT yaml, layout FROM workflow WHERE id = ?", (workflow_id,))
        if not row:
            factory = T.BUILTIN.get(workflow_id)
            return factory() if factory else None
        definition = load_yaml(row["yaml"])
        if row.get("layout") and not definition.layout:
            definition.layout = json.loads(row["layout"])
        return definition

    def save(self, definition: WorkflowDef, source: str = "user") -> WorkflowDef:
        now = utcnow()
        meta = definition.workflow
        self.db.execute(
            "INSERT INTO workflow(id, name, description, source, yaml, layout, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
            "description = excluded.description, yaml = excluded.yaml, layout = excluded.layout, updated_at = excluded.updated_at",
            (meta.id, meta.name, meta.description, source, dump_yaml(definition), json.dumps(definition.layout), now, now),
        )
        return definition

    def delete(self, workflow_id: str) -> None:
        self.db.execute("DELETE FROM workflow WHERE id = ?", (workflow_id,))

    def save_team(self, team_id: str, name: str, workflow_id: str, plan: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO team(id, name, workflow_id, plan, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, workflow_id = excluded.workflow_id, plan = excluded.plan",
            (team_id, name, workflow_id, json.dumps(plan, default=str), utcnow()),
        )

    def teams(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id, name, workflow_id, plan, created_at FROM team ORDER BY created_at DESC")
        return [{**r, "plan": json.loads(r["plan"])} for r in rows]

    def delete_team(self, team_id: str) -> None:
        self.db.execute("DELETE FROM team WHERE id = ?", (team_id,))
