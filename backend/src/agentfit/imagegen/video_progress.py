"""Observed ComfyUI node/step progress; stage weights are not time estimates."""
from __future__ import annotations

import contextlib
import json
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect


class VideoProgress:
    def __init__(self, graph: dict[str, Any]) -> None:
        self.samplers = [key for key, node in graph.items() if node["class_type"] in ("KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced")]
        self.phases = {key: (("decoding", 82) if node["class_type"].startswith("VAEDecode") else
                             ("upscaling", 82) if node["class_type"] == "VSRDecode" else
                             ("encoding", 88 if node["class_type"] == "SaveVideo" else 86))
                       for key, node in graph.items() if node["class_type"] in ("VAEDecode", "VAEDecodeAudio", "VSRDecode", "CreateVideo", "SaveVideo")}
        self.frame_decoders = {key for key, node in graph.items() if node["class_type"] == "VSRDecode"}
        self.percent = 0
        self.node: str | None = None
        self.prompt_id: str | None = None

    def event(self, message: Any) -> dict[str, Any]:
        if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
            return {}
        data = message["data"]
        # Never borrow progress from an image or another application's prompt.
        if not self.prompt_id or data.get("prompt_id") != self.prompt_id:
            return {}
        kind = message.get("type")
        stage, percent, step, total = "loading", 2, None, None
        if kind == "execution_cached":
            cached = data.get("nodes", [])
            if not isinstance(cached, list):
                return {}
            for index, node in enumerate(self.samplers):
                if node in cached:
                    percent = max(percent, 10 + round(70 * (index + 1) / len(self.samplers)))
            stage = "sampling" if percent > 2 else "loading"
        elif kind in ("executing", "progress"):
            node = data.get("node", self.node)
            if node is None:
                return {}
            node = str(node)
            if kind == "executing":
                self.node = node
            if node in self.samplers:
                index = self.samplers.index(node)
                fraction = 0.0
                if kind == "progress":
                    value, maximum = data.get("value"), data.get("max")
                    if (isinstance(value, bool) or isinstance(maximum, bool)
                            or not isinstance(value, int) or not isinstance(maximum, int)
                            or maximum <= 0 or not 0 <= value <= maximum):
                        return {}
                    step, total = value, maximum
                    fraction = value / maximum
                stage = "sampling"
                percent = 10 + round(70 * (index + fraction) / len(self.samplers))
            elif kind == "progress" and node in self.frame_decoders:
                value, maximum = data.get("value"), data.get("max")
                if (isinstance(value, bool) or isinstance(maximum, bool) or not isinstance(value, int)
                        or not isinstance(maximum, int) or maximum <= 0 or not 0 <= value <= maximum):
                    return {}
                stage, percent, step, total = "upscaling", 82 + round(5 * value / maximum), value, maximum
            elif kind == "progress":
                return {}
            elif node in self.phases:
                stage, percent = self.phases[node]
        elif kind != "execution_start":
            return {}
        self.percent = max(self.percent, percent)
        return {"stage": stage, "percent": self.percent, "step": step, "total": total}


class ProgressStream:
    """Optional, bounded observer: socket failure never fails a generation."""
    def __init__(self, client: Any) -> None:
        self.socket: Any = None
        if not isinstance(getattr(client, "base_url", None), str):
            return
        url = urlsplit(client.base_url)
        address = urlunsplit(("wss" if url.scheme == "https" else "ws", url.netloc,
                              url.path.rstrip("/") + "/ws", urlencode({"clientId": client.client_id}), ""))
        with contextlib.suppress(OSError, WebSocketException, TimeoutError):
            self.socket = connect(address, open_timeout=3, close_timeout=1, proxy=None, max_size=2**20)

    def poll(self) -> list[Any]:
        events = []
        if self.socket is None:
            return events
        for _ in range(100):
            try:
                message = self.socket.recv(timeout=0.001)
            except TimeoutError:
                break
            except (OSError, WebSocketException):
                self.close()
                break
            if isinstance(message, str):
                with contextlib.suppress(ValueError):
                    events.append(json.loads(message))
        return events

    def close(self) -> None:
        if self.socket is not None:
            with contextlib.suppress(OSError, WebSocketException):
                self.socket.close()
            self.socket = None
