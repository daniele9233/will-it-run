"""Prompt collections: finished videos kept with the prompts that made them. No ComfyUI, GPU or user data."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentfit.events import EventBus
from agentfit.imagegen.video import VideoStudio
from agentfit.imagegen.video_collections import VideoCollectionStore


def keyframe(file: str, label: str, time: float, **extra) -> dict:
    return {"file": file, "label": label, "time": time, "prompt": f"picture {label}", "workflow": "qwen_image_txt2img",
            "edited_from": None, "image_model": "qwen-image-2.1-uc", "width": 1344, "height": 768, "seed": 42,
            "steps": 25, "cfg": 1.0, **extra}


def entry(entry_id: str, **extra) -> dict:
    return {"id": entry_id, "title": f"Video {entry_id}", "video": f"{entry_id}.mp4", "model_key": "minimax-h3-fl2va",
            "model_name": "MiniMax H3", "prompt": "The headphones disassemble.", "duration_seconds": 6,
            "keyframe_times": [0, 6], "turbo": True, "seed": 42, "width": 1344, "height": 768, "elapsed": 457.0,
            "keyframes": [keyframe(f"{entry_id}-first.png", "first", 0),
                          keyframe(f"{entry_id}-last.png", "last", 6, workflow="qwen_image_edit", edited_from="first", cfg=2.5)],
            **extra}


def write_collection(root: Path, collection_id: str = "scroll-heroes", entries: list[dict] | None = None,
                     files: tuple[str, ...] = ()) -> Path:
    folder = root / collection_id
    folder.mkdir(parents=True)
    body = {"id": collection_id, "title": "Scroll Heroes", "description": "Twenty videos.", "created_at": 1794925168.0,
            "entries": entries if entries is not None else [entry("01-headphones")]}
    (folder / "collection.json").write_text(json.dumps(body), encoding="utf-8")
    for name in files:
        (folder / name).write_bytes(f"bytes of {name}".encode())
    return folder


def test_a_collection_lists_its_entries_and_says_which_files_are_on_disk(tmp_path):
    root = tmp_path / "collections"
    write_collection(root, entries=[entry("01-headphones"), entry("02-watch")],
                     files=("01-headphones.mp4", "01-headphones-first.png", "01-headphones-last.png", "02-watch-first.png"))
    [collection] = VideoCollectionStore(root).list()
    first, second = collection["entries"]
    assert collection["title"] == "Scroll Heroes" and first["keyframes"][1]["edited_from"] == "first"
    assert first["video_ready"] and all(k["ready"] for k in first["keyframes"])
    assert not second["video_ready"] and [k["ready"] for k in second["keyframes"]] == [True, False]


def test_a_broken_or_misplaced_collection_is_left_out(tmp_path):
    root = tmp_path / "collections"
    write_collection(root, "good", files=("01-headphones.mp4",))
    broken = root / "broken"
    broken.mkdir()
    (broken / "collection.json").write_text("{not json", encoding="utf-8")
    write_collection(root, "copied")
    body = json.loads((root / "copied" / "collection.json").read_text(encoding="utf-8"))
    (root / "copied" / "collection.json").write_text(json.dumps(body | {"id": "good"}), encoding="utf-8")
    write_collection(root, "no-entries", entries=[entry("x", keyframes=[])])
    assert [c["id"] for c in VideoCollectionStore(root).list()] == ["good"]
    assert VideoCollectionStore(tmp_path / "absent").list() == []


@pytest.mark.parametrize("video, picture", [("../outside.mp4", "01-first.png"), ("01.mp4", "..\\secret.png"),
                                            ("01.exe", "01-first.png"), ("01.mp4", "01-first.txt")])
def test_collection_files_never_leave_their_folder_or_change_kind(tmp_path, video, picture):
    root = tmp_path / "collections"
    folder = write_collection(root, entries=[entry("01", video=video, keyframes=[keyframe(picture, "first", 0)])],
                              files=("01.mp4", "01-first.png"))
    (folder.parent / "outside.mp4").write_bytes(b"x")
    store = VideoCollectionStore(root)
    with pytest.raises(KeyError):
        if video == "01.mp4":
            store.keyframe("scroll-heroes", "01", 0)
        else:
            store.video("scroll-heroes", "01")
    with pytest.raises(KeyError):
        store.video("../videos", "01")
    with pytest.raises(KeyError):
        store.keyframe("scroll-heroes", "01", 5)


class UploadClient:
    def __init__(self):
        self.uploads: list[tuple[str, bytes]] = []

    def upload(self, data: bytes, name: str) -> str:
        self.uploads.append((name, data))
        return f"WillItRun/{name}"


def test_the_collection_api_plays_saves_and_hands_the_keyframes_to_the_engine(tmp_path, monkeypatch):
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient

    from agentfit.api import video_routes

    def guard(authorization: str = Header(default="")):
        if authorization != "Bearer test":
            raise HTTPException(401)

    client_fake = UploadClient()
    images = SimpleNamespace(client=client_fake, bus=EventBus())
    studio = VideoStudio(images, directory=tmp_path / "videos", models_dir=tmp_path / "models")
    write_collection(studio.directory / "collections",
                     files=("01-headphones.mp4", "01-headphones-first.png", "01-headphones-last.png"))
    monkeypatch.setattr(video_routes, "VideoStudio", lambda _: studio)
    app = FastAPI()
    video_routes.install_routes(app, SimpleNamespace(images=images), [Depends(guard)])
    base = "/api/videos/collections/scroll-heroes/entries/01-headphones"
    with TestClient(app) as client:
        assert client.get("/api/videos/collections").status_code == 401
        assert client.post(f"{base}/use").status_code == 401
        client.headers["Authorization"] = "Bearer test"
        [listed] = client.get("/api/videos/collections").json()["collections"]
        assert listed["entries"][0]["video_ready"] is True
        video = client.get(f"{base}/file")
        assert video.headers["content-type"] == "video/mp4" and video.content == b"bytes of 01-headphones.mp4"
        assert client.get(f"{base}/keyframes/1/file").content == b"bytes of 01-headphones-last.png"
        assert client.get(f"{base}/keyframes/2/file").status_code == 404
        assert client.get("/api/videos/collections/scroll-heroes/entries/absent/file").status_code == 404
        target = tmp_path / "hero"
        saved = client.post(f"{base}/save", json={"path": str(target)})
        assert saved.json() == {"path": str(target.with_suffix(".mp4")), "bytes": 26}
        assert client.post(f"{base}/save", json={"path": "relative.mp4"}).status_code == 400
        used = client.post(f"{base}/use").json()
        assert used == {"images": ["WillItRun/collection-scroll-heroes-01-headphones-first.png",
                                   "WillItRun/collection-scroll-heroes-01-headphones-last.png"]}
        assert [data for _, data in client_fake.uploads] == [b"bytes of 01-headphones-first.png",
                                                             b"bytes of 01-headphones-last.png"]
