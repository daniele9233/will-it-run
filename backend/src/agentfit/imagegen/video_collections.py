"""Prompt collections: finished videos kept with every prompt and setting that made them.

A collection is a folder ``<videos>/collections/<id>/`` with ``collection.json``, the MP4 of each entry and its
keyframe pictures. Each entry records the video request (model, prompt, duration, keyframe times, seed, Turbo,
generation size) and, for each keyframe picture, the image prompt and workflow that produced it, so the whole
video can be made again. Collections are read only here; they are written by whoever assembled them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

SAFE_ID = r"^[a-z0-9][a-z0-9-]{0,80}$"
PICTURES = (".png", ".jpg", ".jpeg", ".webp")


class CollectionKeyframe(BaseModel):
    file: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=40)
    #: Second of the video this picture is pinned to.
    time: float | None = None
    prompt: str = Field(min_length=1, max_length=10000)
    #: The image workflow that made it: a text-to-image or an edit template key.
    workflow: str = Field(min_length=1, max_length=80)
    #: The label of the keyframe this one is an edit of; None for a picture made from text.
    edited_from: str | None = None
    image_model: str = Field(min_length=1, max_length=120)
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    steps: int | None = None
    cfg: float | None = None


class CollectionEntry(BaseModel):
    id: str = Field(pattern=SAFE_ID)
    title: str = Field(min_length=1, max_length=200)
    video: str = Field(min_length=1, max_length=200)
    model_key: str = Field(min_length=1, max_length=80)
    model_name: str = ""
    prompt: str = Field(min_length=1, max_length=10000)
    duration_seconds: int = Field(ge=1, le=60)
    keyframe_times: list[float] = Field(default_factory=list)
    turbo: bool = False
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    #: Seconds the generation took, as recorded by the app; None when not recorded.
    elapsed: float | None = None
    output: dict[str, Any] | None = None
    notes: str = ""
    keyframes: list[CollectionKeyframe] = Field(min_length=1, max_length=9)


class VideoCollection(BaseModel):
    id: str = Field(pattern=SAFE_ID)
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    created_at: float = Field(gt=0, allow_inf_nan=False)
    entries: list[CollectionEntry] = Field(default_factory=list)


def _safe_file(folder: Path, name: str, suffixes: tuple[str, ...]) -> Path:
    """A file directly inside ``folder`` with one of these suffixes; anything else is unknown."""
    if Path(name).name != name or name.startswith(".") or Path(name).suffix.lower() not in suffixes:
        raise KeyError(name)
    path = folder / name
    if not path.is_file():
        raise KeyError(name)
    return path


class VideoCollectionStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _folder(self, collection_id: str) -> Path:
        if not re.fullmatch(SAFE_ID, collection_id):
            raise KeyError(collection_id)
        return self.directory / collection_id

    def collection(self, collection_id: str) -> VideoCollection:
        path = self._folder(collection_id) / "collection.json"
        try:
            collection = VideoCollection.model_validate_json(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValidationError) as exc:
            raise KeyError(collection_id) from exc
        if collection.id != collection_id:  # the folder is the address: a copied folder must carry its own id
            raise KeyError(collection_id)
        return collection

    def list(self) -> list[dict[str, Any]]:
        """Every readable collection, newest first, each file marked as on disk or missing."""
        if not self.directory.is_dir():
            return []
        rows = []
        for folder in self.directory.iterdir():
            if not folder.is_dir():
                continue
            try:
                collection = self.collection(folder.name)
            except KeyError:
                continue
            row = json.loads(collection.model_dump_json())
            for entry in row["entries"]:
                entry["video_ready"] = (folder / entry["video"]).is_file() and Path(entry["video"]).name == entry["video"]
                for keyframe in entry["keyframes"]:
                    keyframe["ready"] = (folder / keyframe["file"]).is_file() and Path(keyframe["file"]).name == keyframe["file"]
            rows.append(row)
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    def entry(self, collection_id: str, entry_id: str) -> CollectionEntry:
        found = next((e for e in self.collection(collection_id).entries if e.id == entry_id), None)
        if found is None:
            raise KeyError(entry_id)
        return found

    def video(self, collection_id: str, entry_id: str) -> Path:
        return _safe_file(self._folder(collection_id), self.entry(collection_id, entry_id).video, (".mp4",))

    def keyframe(self, collection_id: str, entry_id: str, index: int) -> Path:
        keyframes = self.entry(collection_id, entry_id).keyframes
        if not 0 <= index < len(keyframes):
            raise KeyError(index)
        return _safe_file(self._folder(collection_id), keyframes[index].file, PICTURES)
