"""Named positive/negative video prompt pairs, stored independently of gallery videos."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from agentfit.i18n import tr


class VideoPromptInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=10000)
    negative: str = Field(default="", max_length=10000)

    @field_validator("name", "prompt")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(tr("Enter a name and a video prompt."))
        return value


class SavedVideoPrompt(VideoPromptInput):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    created_at: float = Field(gt=0, allow_inf_nan=False)


class VideoPromptStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.lock = threading.RLock()

    def list(self) -> list[dict]:
        rows = []
        with self.lock:
            for path in self.directory.glob("*.json"):
                try:
                    item = SavedVideoPrompt.model_validate_json(path.read_text(encoding="utf-8"))
                    if path.stem == item.id:
                        rows.append(item.model_dump())
                except (OSError, ValueError):
                    continue
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    def save(self, body: VideoPromptInput) -> dict:
        item = SavedVideoPrompt(**body.model_dump(), id=uuid.uuid4().hex, created_at=time.time())
        item.name = item.name.strip()
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as stream:
                pending = Path(stream.name)
            try:
                pending.write_text(json.dumps(item.model_dump(), ensure_ascii=False), encoding="utf-8")
                pending.replace(self.directory / f"{item.id}.json")
            finally:
                pending.unlink(missing_ok=True)
        return item.model_dump()

    def delete(self, prompt_id: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", prompt_id):
            raise KeyError(prompt_id)
        with self.lock:
            path = self.directory / f"{prompt_id}.json"
            if not path.is_file():
                raise KeyError(prompt_id)
            path.unlink()
