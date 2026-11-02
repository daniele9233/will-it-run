"""Prompt pairs persist as text, without starting a model or touching owner data."""
import json
from pathlib import Path

import pytest

from agentfit.imagegen.video_prompts import VideoPromptInput, VideoPromptStore


def test_named_pairs_preserve_exact_text_after_reopening(tmp_path):
    store = VideoPromptStore(tmp_path / "videos" / "prompts")
    positive = '  Locked camera.\nSeparate the "GPU" components. ✨\n'
    negative = "\nno melting, no flicker\n"
    saved = store.save(VideoPromptInput(name="  GPU effect  ", prompt=positive, negative=negative))
    assert saved["name"] == "GPU effect"
    assert saved["prompt"] == positive and saved["negative"] == negative
    assert VideoPromptStore(store.directory).list() == [saved]
    store.delete(saved["id"])
    assert not store.list()
    with pytest.raises(KeyError):
        store.delete(saved["id"])


@pytest.mark.parametrize("values", [{"name": " "}, {"prompt": "\n "}, {"name": "x" * 121}, {"negative": "x" * 10001}])
def test_invalid_pairs_are_rejected(values):
    with pytest.raises(ValueError):
        VideoPromptInput(**({"name": "effect", "prompt": "movement"} | values))


def test_names_are_never_paths_and_invalid_records_are_not_listed(tmp_path):
    store = VideoPromptStore(tmp_path / "prompts")
    saved = store.save(VideoPromptInput(name="../effect", prompt="move"))
    assert (store.directory / f"{saved['id']}.json").is_file()
    (store.directory / "broken.json").write_text("not JSON", encoding="utf-8")
    (store.directory / "wrong-id.json").write_text(json.dumps(saved), encoding="utf-8")
    assert store.list() == [saved]
    with pytest.raises(KeyError):
        store.delete("../effect")
    assert store.list() == [saved]


def test_failed_write_does_not_publish_or_leave_a_partial_pair(tmp_path, monkeypatch):
    store = VideoPromptStore(tmp_path / "prompts")

    def refuse(self, target):
        raise PermissionError("disk refused")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", refuse)
        with pytest.raises(PermissionError):
            store.save(VideoPromptInput(name="effect", prompt="move"))
    assert not store.list()
    assert not list(store.directory.iterdir())
