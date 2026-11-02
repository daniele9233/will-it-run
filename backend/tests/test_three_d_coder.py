"""Integration contracts with a fake gateway; the downloaded Qwen model is never run."""
from types import SimpleNamespace

import pytest

from agentfit.lmstudio.models import LMStudioInstall, LoadedInstance
from agentfit.three_d_coder import (
    PREVIEW_CSP,
    ThreeDCodeRuntime,
    ThreeDCodeStudio,
    complete_document,
    syntax_error,
)


@pytest.fixture(autouse=True)
def bundled_runtime(tmp_path, monkeypatch):
    """Preview contracts use a tiny local runtime, never the ignored build output."""
    import agentfit.three_d_coder as coder

    data = tmp_path / "data"
    data.mkdir()
    (data / "three_runtime.js").write_text("const THREE = {};", encoding="utf-8")
    monkeypatch.setattr(coder, "__file__", str(tmp_path / "three_d_coder.py"))


def test_inventory_does_not_load_or_prompt(tmp_path):
    model = SimpleNamespace(model_key="qwen3.5-4b-3d-coder", display_name="Qwen3.5 4B 3D Coder", vision=True)
    def forbidden(*args):
        pytest.fail("Inventory must not start inference")
    studio = ThreeDCodeStudio(lambda: [model], forbidden, forbidden, tmp_path)
    assert studio.models()[0]["model_key"] == model.model_key
    assert studio.gallery() == []


def test_generation_uses_shared_slot_and_no_tools(tmp_path):
    calls = []
    model = SimpleNamespace(model_key="qwen3.5-4b-3d-coder", display_name="Qwen3.5 4B 3D Coder", vision=True)
    identifier = "e" * 32
    def complete(instance, messages, **kwargs):
        calls.append(kwargs)
        assert "tools" not in kwargs
        return {"choices": [{"message": {"content": "```html\n<html><head></head><body>fake</body></html>\n```"}}]}
    gateway = SimpleNamespace(acquire=lambda *a, **k: SimpleNamespace(instance_id="fake", context_length=32768),
                              runtime=SimpleNamespace(complete=complete), close=lambda: calls.append("closed"))
    def start(kind, work):
        assert kind == "code"
        work(identifier, lambda _: None)
        return {"id": identifier}
    studio = ThreeDCodeStudio(lambda: [model], lambda _: gateway, start, tmp_path)
    assert studio.generate(model.model_key, "A cube")["id"] == identifier
    assert calls[-1] == "closed"
    assert studio.result(identifier)["html"].startswith("<html>")
    assert studio.gallery()[0]["prompt"] == "A cube"
    assert "connect-src 'none'" in PREVIEW_CSP
    assert "sandbox allow-scripts" in PREVIEW_CSP
    with pytest.raises(KeyError):
        studio.file("../private")
    with pytest.raises(ValueError):
        studio.generate("missing", "prompt")


def test_coder_load_explicitly_disables_mtp_with_fake_cli(monkeypatch):
    calls = []
    cli = SimpleNamespace(available=True, run=lambda args, **kwargs: (calls.append(args) or (0, "", "")))
    runtime = ThreeDCodeRuntime(cli=cli, install=LMStudioInstall(), client=SimpleNamespace())
    monkeypatch.setattr(runtime, "list_loaded", lambda: [LoadedInstance(model_key="fake", instance_id="fake-loaded", context_length=32768)])
    assert runtime.load("fake", context_length=32768).instance_id == "fake-loaded"
    assert "--no-speculative-draft-mtp" in calls[0]
    assert "--speculative-draft-mtp" not in calls[0]
    assert calls[0][calls[0].index("--parallel") + 1] == "1"


@pytest.mark.parametrize("content", ["<html><body><script>box(0.78", "<html><body><script>/* </body></html>", "<html><body>unfinished"])
def test_incomplete_document_is_rejected(content):
    assert not complete_document(content)


def test_continuation_recovers_a_copy_and_preserves_original(tmp_path):
    import json
    original, recovered = "a" * 32, "b" * 32
    partial = '<html><head><title>Garden</title></head><body><script>\nconst value = 1;\nbox(0.78'
    (tmp_path / f"{original}.html").write_text(partial, encoding="utf-8")
    (tmp_path / f"{original}.json").write_text(json.dumps({"model": "qwen3.5-3d-coder", "prompt": "Garden", "id": original}), encoding="utf-8")
    model = SimpleNamespace(model_key="qwen3.5-3d-coder", display_name="Qwen", vision=False)
    calls = []
    def complete(instance, messages, **kwargs):
        calls.append(messages)
        assert messages[-2]["content"].endswith("const value = 1;\n")
        return {"choices": [{"message": {"content": "</script></body></html>"}, "finish_reason": "stop"}]}
    gateway = SimpleNamespace(acquire=lambda *a, **k: SimpleNamespace(instance_id="fake", context_length=65536), runtime=SimpleNamespace(complete=complete), close=lambda: None)
    def start(kind, work):
        work(recovered, lambda _: None)
        return {"id": recovered}
    studio = ThreeDCodeStudio(lambda: [model], lambda _: gateway, start, tmp_path)
    with pytest.raises(ValueError, match="incomplete"):
        studio.preview(original)
    assert studio.repair(original)["id"] == recovered
    assert (tmp_path / f"{original}.html").read_text(encoding="utf-8") == partial
    assert complete_document(studio.result(recovered)["html"])
    assert next(s for s in studio.gallery() if s["id"] == recovered)["parent_id"] == original
    assert len(calls) == 1
    standalone = studio.standalone(recovered)
    assert "wir-scene-preview" in standalone
    assert "unhandledrejection" in standalone
    assert "securitypolicyviolation" in standalone
    assert "connect-src 'none'" in standalone
    assert "Authorization" not in standalone


def test_exhausted_continuations_never_create_completed_gallery_entry(tmp_path):
    model = SimpleNamespace(model_key="qwen3.5-3d-coder", display_name="Qwen", vision=False)
    closed = []
    gateway = SimpleNamespace(acquire=lambda *a, **k: SimpleNamespace(instance_id="fake", context_length=65536), runtime=SimpleNamespace(complete=lambda *a, **k: {"choices": [{"message": {"content": "<html><body><script>\nconst x = ("}, "finish_reason": "length"}]}), close=lambda: closed.append(True))
    def start(kind, work):
        work("c" * 32, lambda _: None)
    studio = ThreeDCodeStudio(lambda: [model], lambda _: gateway, start, tmp_path)
    with pytest.raises(ValueError, match="incomplete"):
        studio.generate(model.model_key, "Cube")
    assert studio.gallery() == []
    assert (tmp_path / f"{'c' * 32}.partial.html").is_file()
    assert closed == [True]


def test_anime_duplicate_declaration_is_detected_without_execution():
    import shutil
    if not shutil.which("node"):
        pytest.skip("Local Node syntax checker unavailable")
    assert "already been declared" in syntax_error('<html><body><script>const localX = 1; const localX = 2;</script></body></html>')
    assert syntax_error('<script>throw new Error("must never execute")</script>') == ""


def test_generation_retries_syntax_errors(tmp_path):
    model = SimpleNamespace(model_key="qwen3.5-3d-coder", display_name="Qwen", vision=False)
    bad = '<html><body><script>const localX = 1; const localX = 2;</script></body></html>'
    good = '<html><body><script>const localX = 1;</script></body></html>'
    import shutil
    if not shutil.which("node"):
        pytest.skip("Local Node syntax checker unavailable")
    calls = []
    def complete(instance, messages, **kwargs):
        calls.append(messages)
        return {"choices": [{"message": {"content": bad if len(calls) == 1 else good}}]}
    gateway = SimpleNamespace(acquire=lambda *a, **k: SimpleNamespace(instance_id="fake", context_length=65536),
                              runtime=SimpleNamespace(complete=complete), close=lambda: None)
    def start(kind, work):
        work("d" * 32, lambda _: None)
        return {"id": "d" * 32}
    studio = ThreeDCodeStudio(lambda: [model], lambda _: gateway, start, tmp_path)
    studio.generate(model.model_key, "crea un anime 3d")
    assert len(calls) == 2
    assert "already been declared" in calls[1][-1]["content"]
    assert studio.result("d" * 32)["html"] == good


def test_delete_scene_and_strip_redundant_cdn_loader(tmp_path):
    identifier = "f" * 32
    studio = ThreeDCodeStudio(lambda: [], lambda: None, lambda: None, tmp_path)
    html = '<html><head></head><body><script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/0.160.0/three.module.min.js"></script><script>const scene = new THREE.Scene();</script></body></html>'
    studio.file(identifier, False).write_text(html, encoding="utf-8")
    (tmp_path / f"{identifier}.json").write_text('{}', encoding="utf-8")
    sibling = tmp_path / f"{'e' * 32}.html"
    sibling.write_text("preserved", encoding="utf-8")
    standalone = studio.standalone(identifier)
    assert 'src="https://cdnjs' not in standalone
    assert 'const scene = new THREE.Scene()' in standalone
    assert studio.result(identifier)["html"] == html
    with pytest.raises(KeyError):
        studio.forget("../private")
    studio.forget(identifier)
    assert not (tmp_path / f"{identifier}.json").exists()
    assert sibling.read_text(encoding="utf-8") == "preserved"
    with pytest.raises(KeyError):
        studio.file(identifier)


def test_delete_scene_route(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentfit.api.trellis_routes import install_routes

    identifier = "a" * 32
    studio = ThreeDCodeStudio(lambda: [], lambda: None, lambda: None, tmp_path)
    studio.file(identifier, False).write_text('<html><body></body></html>', encoding="utf-8")
    app = FastAPI()
    install_routes(app, [], studio=SimpleNamespace(), coder=studio)
    with TestClient(app) as client:
        assert client.delete(f"/api/3d/coder/{identifier}").json() == {"deleted": True}
        assert client.delete(f"/api/3d/coder/{identifier}").status_code == 404
