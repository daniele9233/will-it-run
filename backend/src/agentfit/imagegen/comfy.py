"""A small client for ComfyUI's HTTP API.

ComfyUI is a graph runner: a workflow is a JSON object of nodes, POSTing it to
``/prompt`` queues it, ``/history/<id>`` says whether it ran, ``/view`` returns the
file. That is the whole surface used here — no websocket, no custom node.

Two things the rest of the app relies on:

- every failure carries a message a person can act on (ComfyUI reports node errors as
  nested JSON, which is unreadable raw);
- ``object_info`` and ``/models/<folder>`` are the only sources of truth about what the
  engine can actually do. Nothing here guesses that a model file exists.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx


class ComfyError(Exception):
    """The engine answered, and refused: a bad graph, a node error, a missing model."""


class ComfyUnavailable(ComfyError):
    """ComfyUI is not running, or not at the address the settings hold."""


def _error_text(payload: Any) -> str:
    """Turn ComfyUI's error body into one line a person can read."""
    if not isinstance(payload, dict):
        return str(payload)[:400]
    parts: list[str] = []
    error = payload.get("error")
    if isinstance(error, dict):
        parts.append(str(error.get("message") or ""))
        if error.get("details"):
            parts.append(str(error["details"]))
    elif error:
        parts.append(str(error))
    for node in (payload.get("node_errors") or {}).values():
        for item in node.get("errors", []):
            parts.append(f"{item.get('message', '')}: {item.get('details', '')}".strip(": "))
    text = " | ".join(part for part in parts if part)
    return (text or "The engine refused the workflow.")[:600]


class ComfyClient:
    """Talks to one ComfyUI instance."""

    def __init__(self, base_url: str, *, client_id: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or f"willitrun-{uuid.uuid4().hex[:8]}"
        self.timeout = timeout
        self._objects: tuple[float, dict[str, Any]] | None = None

    def __repr__(self) -> str:
        return f"ComfyClient({self.base_url})"

    # -- transport ----------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                params=params,
                timeout=timeout or self.timeout,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ComfyUnavailable(
                f"ComfyUI is not responding at {self.base_url}. Start it, or change the address in Settings > Images."
            ) from exc
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"ComfyUI at {self.base_url} could not be reached: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = _error_text(response.json())
            except ValueError:
                detail = response.text[:400]
            raise ComfyError(detail or f"ComfyUI answered {response.status_code}.")
        return response

    def _json(self, response: httpx.Response) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ComfyError("ComfyUI answered with something that is not JSON.") from exc

    # -- engine -------------------------------------------------------------
    def available(self) -> bool:
        try:
            self.stats()
        except ComfyError:
            return False
        return True

    def stats(self) -> dict[str, Any]:
        return self._json(self._request("GET", "/system_stats", timeout=5.0)) or {}

    def vram(self) -> dict[str, Any] | None:
        """The first device's memory, as ComfyUI reports it."""
        stats = self.stats()
        devices = stats.get("devices") or []
        if not devices:
            return None
        device = devices[0]
        return {
            "name": device.get("name"),
            "total_gb": round(float(device.get("vram_total") or 0) / 1024**3, 1),
            "free_gb": round(float(device.get("vram_free") or 0) / 1024**3, 1),
            "torch_total_gb": round(float(device.get("torch_vram_total") or 0) / 1024**3, 1),
        }

    def models(self, folder: str, *, timeout: float = 15.0) -> list[str]:
        """The files ComfyUI sees in one of its model folders.

        ``/models/<folder>`` lists every configured path for that folder, including the
        extra ones in ``extra_model_paths.yaml``, so this is the honest answer to
        "is this file installed".
        """
        data = self._json(self._request("GET", f"/models/{folder}", timeout=timeout))
        return [str(name) for name in data] if isinstance(data, list) else []

    def all_models(self, folders: list[str], *, timeout: float = 15.0) -> dict[str, list[str] | None]:
        """Each folder's list, or None when ComfyUI does not know that folder at all."""
        out: dict[str, list[str] | None] = {}
        for folder in dict.fromkeys(folders):
            try:
                out[folder] = self.models(folder, timeout=timeout)
            except ComfyError:
                out[folder] = None
        return out

    def object_info(self, *, max_age: float = 300.0) -> dict[str, Any]:
        """Every node class this ComfyUI has, custom nodes included. Cached: it is megabytes."""
        if self._objects is not None and time.time() - self._objects[0] < max_age:
            return self._objects[1]
        data = self._json(self._request("GET", "/object_info", timeout=60.0))
        if not isinstance(data, dict):
            raise ComfyError("ComfyUI returned an unexpected node list.")
        self._objects = (time.time(), data)
        return data

    def missing_nodes(self, classes: list[str]) -> list[str]:
        """Which of ``classes`` this engine does not have. Unknown classes cannot be queued."""
        available = self.object_info()
        return [name for name in classes if name not in available]

    # -- queue --------------------------------------------------------------
    def queue(self, graph: dict[str, Any]) -> str:
        payload = {"prompt": graph, "client_id": self.client_id}
        data = self._json(self._request("POST", "/prompt", json_body=payload, timeout=30.0))
        if not isinstance(data, dict) or not data.get("prompt_id"):
            raise ComfyError(_error_text(data) if isinstance(data, dict) else "ComfyUI did not queue the workflow.")
        if data.get("node_errors"):
            raise ComfyError(_error_text(data))
        return str(data["prompt_id"])

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        data = self._json(self._request("GET", f"/history/{prompt_id}", timeout=15.0))
        if not isinstance(data, dict):
            return None
        entry = data.get(prompt_id)
        return entry if isinstance(entry, dict) else None

    def queue_state(self) -> dict[str, Any]:
        data = self._json(self._request("GET", "/queue", timeout=10.0))
        if not isinstance(data, dict):
            return {"running": 0, "pending": 0}
        return {"running": len(data.get("queue_running") or []), "pending": len(data.get("queue_pending") or [])}

    def interrupt(self) -> None:
        self._request("POST", "/interrupt", json_body={}, timeout=10.0)

    def clear_queue(self) -> None:
        self._request("POST", "/queue", json_body={"clear": True}, timeout=10.0)

    def free_memory(self) -> None:
        """Unload the models ComfyUI holds and hand the VRAM back."""
        self._request("POST", "/free", json_body={"unload_models": True, "free_memory": True}, timeout=60.0)

    # -- files --------------------------------------------------------------
    def view(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        response = self._request(
            "GET",
            "/view",
            params={"filename": filename, "subfolder": subfolder, "type": folder_type},
            timeout=120.0,
        )
        return response.content

    def upload(self, data: bytes, filename: str, subfolder: str = "WillItRun") -> str:
        """Put a picture where a LoadImage node can find it. Returns the name to use."""
        files = {"image": (filename, data, "application/octet-stream")}
        form = {"overwrite": "true", "subfolder": subfolder}
        try:
            response = httpx.post(f"{self.base_url}/upload/image", files=files, data=form, timeout=120.0)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ComfyUnavailable(f"ComfyUI is not responding at {self.base_url}.") from exc
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"Could not upload to ComfyUI: {exc}") from exc
        if response.status_code >= 400:
            try:
                raise ComfyError(_error_text(response.json()))
            except ValueError:
                raise ComfyError(response.text[:400] or "ComfyUI refused the picture.") from None
        payload = response.json()
        name = str(payload.get("name") or filename)
        folder = str(payload.get("subfolder") or "")
        return f"{folder}/{name}" if folder else name


def produced_images(entry: dict[str, Any]) -> list[dict[str, str]]:
    """The images a finished history entry left behind, as ``/view`` arguments."""
    outputs = entry.get("outputs") or {}
    images: list[dict[str, str]] = []
    for node_output in outputs.values():
        for image in node_output.get("images") or []:
            if not image.get("filename"):
                continue
            images.append(
                {
                    "filename": str(image["filename"]),
                    "subfolder": str(image.get("subfolder") or ""),
                    "type": str(image.get("type") or "output"),
                }
            )
    return images


def status_message(entry: dict[str, Any]) -> str | None:
    """ComfyUI's own reason for a failed run, if it gave one."""
    status = entry.get("status") or {}
    if status.get("status_str") == "error":
        for message in status.get("messages") or []:
            if isinstance(message, list) and len(message) == 2 and message[0] == "execution_error":
                detail = message[1] if isinstance(message[1], dict) else {}
                node = detail.get("node_type") or detail.get("node_id") or "a node"
                text = detail.get("exception_message") or "the node failed"
                return f"{node}: {str(text).strip()[:400]}"
        return "The engine reported an error without details."
    return None
