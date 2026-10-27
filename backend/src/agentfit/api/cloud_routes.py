"""HTTP routes for cloud models: the providers, their keys and the models each one offers.

Kept apart from ``server.py`` because they share nothing with the rest. Every route is behind
the same token guard.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agentfit.app import AppServices
from agentfit.cloud import CloudError
from agentfit.cloud.secrets import SecretError


class KeyBody(BaseModel):
    key: str


def install_routes(app: FastAPI, svc: AppServices, guard: list[Any]) -> None:
    # -- cloud models -------------------------------------------------------
    @app.get("/api/cloud", dependencies=guard)
    def cloud_status() -> dict[str, Any]:
        # A key with no model list yet is asked for it now, so its models reach every picker.
        svc.cloud.load_missing()
        return {"providers": svc.cloud.providers(), "models": svc.cloud.available(),
                "max_context": svc.settings.cloud.max_context}

    @app.put("/api/cloud/{provider_id}/key", dependencies=guard)
    def cloud_key_set(provider_id: str, body: KeyBody) -> dict[str, Any]:
        try:
            svc.cloud.set_key(provider_id, body.key)
        except (CloudError, SecretError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # The key is tried at once: the person learns now whether it works, and the model list arrives with it.
        try:
            return svc.cloud.test(provider_id)
        except CloudError as exc:
            return {**svc.cloud.status(provider_id), "ok": False, "error": str(exc)}

    @app.delete("/api/cloud/{provider_id}/key", dependencies=guard)
    def cloud_key_delete(provider_id: str) -> dict[str, Any]:
        try:
            return svc.cloud.delete_key(provider_id)
        except CloudError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/cloud/{provider_id}/test", dependencies=guard)
    def cloud_test(provider_id: str) -> dict[str, Any]:
        try:
            return svc.cloud.test(provider_id)
        except CloudError as exc:
            return {**svc.cloud.status(provider_id), "ok": False, "error": str(exc)}
