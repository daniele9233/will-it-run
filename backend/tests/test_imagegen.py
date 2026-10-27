"""The image studio: templates, components, downloads, plans and a full generation.

ComfyUI is faked at its HTTP surface and on disk (a temp ``models`` folder), which is where
the mistakes are: a link pointing at the wrong output, a component counted as installed when
its file is not there, a shared dependency downloaded twice.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from agentfit import storage
from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.imagegen import GenerationRequest, ImageStudio, catalog, plan, templates
from agentfit.imagegen.comfy import ComfyError, ComfyUnavailable
from agentfit.imagegen.components import ComponentError, ComponentStore
from agentfit.imagegen.engine import Install, find_install

STORED_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeComfy:
    """A ComfyUI that says yes, lists what it was told to list, and returns a PNG."""

    base_url = "http://fake-comfy:8188"
    client_id = "test-client"

    def __init__(self, nodes: set[str] | None = None) -> None:
        self.nodes = nodes if nodes is not None else {
            "CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage",
            "LatentUpscaleBy", "LoadImage", "VAEEncode", "ImageScale", "UnetLoaderGGUF", "UNETLoader",
            "CLIPLoader", "VAELoader", "TextEncodeQwenImage21", "ConditioningZeroOut",
        }
        self.queued: list[dict] = []
        self.offline = False
        self._history: dict[str, dict] = {}

    def _guard(self) -> None:
        if self.offline:
            raise ComfyUnavailable("ComfyUI is not responding at http://fake-comfy:8188.")

    def available(self) -> bool:
        self._guard()
        return True

    def vram(self) -> dict:
        self._guard()
        return {"name": "cuda:0", "total_gb": 16.0, "free_gb": 12.0, "torch_total_gb": 0.0}

    def queue_state(self) -> dict:
        self._guard()
        return {"running": 1, "pending": 0}

    def object_info(self) -> dict:
        self._guard()
        return {name: {} for name in self.nodes}

    def missing_nodes(self, classes: list[str]) -> list[str]:
        self._guard()
        return [name for name in classes if name not in self.nodes]

    def queue(self, graph: dict) -> str:
        self._guard()
        self.queued.append(graph)
        prompt_id = f"prompt-{len(self.queued)}"
        batch = 1
        for node in graph.values():
            if node.get("class_type") == "EmptyLatentImage":
                batch = int(node["inputs"].get("batch_size") or 1)
        images = [
            {"filename": f"{prompt_id}-{index}.png", "subfolder": "WillItRun", "type": "output"}
            for index in range(batch)
        ]
        self._history[prompt_id] = {
            "outputs": {"7": {"images": images}},
            "status": {"status_str": "success", "messages": []},
        }
        return prompt_id

    def history(self, prompt_id: str) -> dict | None:
        self._guard()
        return self._history.get(prompt_id)

    def view(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        self._guard()
        return STORED_PNG

    def interrupt(self) -> None:
        return None

    def free_memory(self) -> None:
        return None

    def upload(self, data: bytes, filename: str, subfolder: str = "WillItRun") -> str:
        return f"{subfolder}/{filename}"


class FakeEngine:
    """A ComfyUI install on disk that never actually runs a process."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._install = Install(root=root, python=Path("python.exe"), models_dir=root / "models", kind="manual")
        self.settings = None
        self.started = 0

    def install(self, refresh: bool = False) -> Install:
        return self._install

    def forget_install(self) -> None:
        return None

    def status(self, refresh: bool = False) -> dict:
        return {
            "installed": True,
            "running": True,
            "url": "http://127.0.0.1:8188",
            "version": "0.38.0",
            "install": self._install.to_json(),
            "started_by_us": False,
            "download_url": "https://comfy.org/download",
            "portable_url": "",
            "gguf_nodes_url": "",
            "log": str(self.root / "comfyui.log"),
        }

    def start(self, timeout: float = 180.0) -> dict:
        self.started += 1
        return self.status()

    def stop(self, timeout: float = 15.0) -> dict:
        return {**self.status(), "stopped": True}


def write(path: Path, size: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    """The KreaNoct trio, as this PC actually has it."""
    models = tmp_path / "models"
    write(models / "diffusion_models" / "KreaNoct_V4_int8_convrot.safetensors")
    write(models / "text_encoders" / "qwen3vl_4b_fp8_scaled.safetensors")
    write(models / "vae" / "qwen_image_vae.safetensors")
    write(models / "checkpoints" / "realvisxlV50_v50Bakedvae.safetensors")
    (models / "loras").mkdir(parents=True, exist_ok=True)
    # ComfyUI keeps custom nodes next to the models folder, not inside it.
    (models.parent / "custom_nodes" / "ComfyUI-GGUF").mkdir(parents=True, exist_ok=True)
    return models


@pytest.fixture
def studio(tmp_path: Path, models_dir: Path) -> ImageStudio:
    db = Database(":memory:")
    settings = Settings()
    engine = FakeEngine(tmp_path)
    store = ComponentStore(db, settings, EventBus(), engine, drives=lambda: [])
    return ImageStudio(
        db, settings, EventBus(), client=FakeComfy(), images_dir=tmp_path / "images",
        engine=engine, components=store, vram_budget_gb=lambda: 14.2, lmstudio_loaded=lambda: False,
    )


def wait_for(studio: ImageStudio, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = studio.job(job_id)
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


# -- templates --------------------------------------------------------------


def test_every_template_binds_and_keeps_no_placeholder() -> None:
    values = {
        "prompt": "a portrait", "negative": "blurry", "width": 1024, "height": 1024, "seed": 7, "seed2": 8,
        "steps": 20, "cfg": 5.0, "sampler": "euler", "scheduler": "simple", "denoise": 1.0, "batch_size": 1,
        "filename_prefix": "WillItRun/test", "checkpoint": "model.safetensors", "unet": "model.gguf",
        "clip": "clip.safetensors", "vae": "vae.safetensors", "unet_loader": "UnetLoaderGGUF",
        "weight_dtype": None, "clip_type": "qwen_image", "resolution": 1024, "image": "in.png",
        "image2": None, "image3": None, "image4": None, "hires_scale": 1.5, "hires_denoise": 0.45,
    }
    for template in templates.templates():
        graph = templates.bind(template, values)
        assert graph, template.key
        for node in graph.values():
            assert "{{" not in str(node), template.key
            assert node["inputs"], template.key


def test_lora_chain_sits_between_the_checkpoint_and_the_sampler() -> None:
    template = templates.by_key("sdxl_txt2img")
    graph = templates.bind(template, {"prompt": "x", "negative": "", "width": 1024, "height": 1024, "seed": 1,
                                      "steps": 20, "cfg": 5, "sampler": "euler", "scheduler": "normal",
                                      "denoise": 1.0, "batch_size": 1, "filename_prefix": "t",
                                      "checkpoint": "m.safetensors"})
    graph = templates.apply_loras(graph, template, [{"name": "a.safetensors", "weight": 0.7},
                                                    {"name": "b.safetensors", "weight": 0.5}])
    assert graph["lora1"]["inputs"]["model"] == ["1", 0]
    assert graph["lora2"]["inputs"]["model"] == ["lora1", 0]
    assert graph["lora2"]["inputs"]["clip"] == ["lora1", 1]
    assert graph["5"]["inputs"]["model"] == ["lora2", 0]
    assert graph["2"]["inputs"]["clip"] == ["lora2", 1]
    assert graph["6"]["inputs"]["vae"] == ["1", 2]


def test_reference_pictures_that_were_not_given_are_removed_from_the_edit_graph() -> None:
    template = templates.by_key("qwen_image_edit")
    base = {"prompt": "change the jacket", "negative": "", "width": 1024, "height": 1024, "seed": 1, "steps": 20,
            "cfg": 2.5, "sampler": "euler", "scheduler": "simple", "denoise": 1.0, "filename_prefix": "t",
            "batch_size": 1, "unet": "u.gguf", "unet_loader": "UnetLoaderGGUF", "weight_dtype": None,
            "clip": "c.safetensors", "vae": "v.safetensors", "clip_type": "qwen_image", "resolution": 1024,
            "image": "one.png"}
    graph = templates.bind(template, {**base, "image2": None, "image3": None, "image4": None})
    assert "11" not in graph and "12" not in graph and "13" not in graph
    assert "images.image_1" in graph["4"]["inputs"]
    graph = templates.bind(template, {**base, "image2": "two.png", "image3": None, "image4": None})
    assert graph["4"]["inputs"]["images.image_2"] == ["11", 0]
    assert "images.image_3" not in graph["4"]["inputs"]


# -- components -------------------------------------------------------------


def test_a_model_is_installed_only_when_every_component_is_on_disk(studio: ImageStudio) -> None:
    rows = {row["key"]: row for row in studio.models()}
    assert rows["kreanoct-v4"]["installed"] is True
    assert rows["realdream-krea2-v3"]["installed"] is False
    missing = [item["id"] for item in rows["realdream-krea2-v3"]["missing"]]
    assert missing == ["krea2-realdream-v3"]  # encoder and VAE are shared and already there


def test_checkpoints_found_on_this_pc_become_models(studio: ImageStudio) -> None:
    rows = {row["key"]: row for row in studio.models()}
    found = rows["ckpt:realvisxlV50_v50Bakedvae.safetensors"]
    assert found["installed"] is True and found["discovered"] is True
    assert found["family"] == "sdxl"


def test_components_shared_by_two_models_are_named_once(studio: ImageStudio) -> None:
    store = studio.components
    # Every Krea 2 model shares one encoder: adding another did not add a second copy.
    krea = store.references("krea2-te")
    assert "KreaNoct V4 INT8" in krea and len(krea) == 6
    qwen = store.references("qwen21-vae")
    assert "Qwen-Image 2.1 UC (Q6_K, GGUF)" in qwen and "Noct Q V4 (Qwen-Image 2.1 UC, INT8)" in qwen


def test_the_catalog_has_no_cartoon_model() -> None:
    """The catalog is photographic: no anime checkpoint by name. Pony V6 XL is here because the person asked for it."""
    for profile in catalog.profiles():
        text = f"{profile.key} {profile.name}".lower()
        assert not any(word in text for word in ("anime", "illustrious", "noobai", "danbooru", "kroma")), profile.key
    assert catalog.by_key("kroma-v0.3-turbo") is None
    assert {profile.family for profile in catalog.profiles()} <= {"krea2", "qwen_image", "zimage", "sdxl", "moss_image"}


def test_a_shared_component_cannot_be_removed(studio: ImageStudio) -> None:
    with pytest.raises(ComponentError, match="6 models"):
        studio.components.remove("krea2-te")
    # A component only one model uses can go.
    result = studio.components.remove("krea2-kreanoct")
    assert result["was_present"] is True
    assert not (studio.components.models_dir / "diffusion_models" / "KreaNoct_V4_int8_convrot.safetensors").exists()


def test_a_model_in_a_shared_folder_is_remembered_while_the_engine_is_stopped(studio: ImageStudio) -> None:
    # ComfyUI lists the extra_model_paths folders, but it is not running now: what it said
    # last time is kept, so a model in D:\...\ComfyUI-Shared does not vanish from the list.
    studio.db.kv_set("imagegen_listing", "checkpoints", {"at": "2026-10-26T12:00:00+00:00",
                                                         "names": ["lustifyNSFWCheckpoint_zenithV9.safetensors"]})
    rows = {row["key"] for row in studio.models()}
    assert "ckpt:lustifyNSFWCheckpoint_zenithV9.safetensors" in rows


def test_listing_a_model_with_the_engine_stopped_is_quick(studio: ImageStudio) -> None:
    started = time.time()
    rows = studio.models()
    elapsed = time.time() - started
    assert len(rows) >= 11
    assert elapsed < 2.0, f"listing took {elapsed:.1f}s with no engine to ask"
    assert studio.components.engine_reachable() is False


def test_the_plan_counts_only_what_is_missing(studio: ImageStudio) -> None:
    result = studio.components.plan("realdream-krea2-v3")
    assert [item["id"] for item in result["missing"]] == ["krea2-realdream-v3"]
    assert result["download_bytes"] == 13_492_688_608


def test_the_plan_says_what_to_change_when_it_does_not_fit(studio: ImageStudio) -> None:
    studio._vram_budget_gb = lambda: 12.0  # a 12 GB card: 14.6 GB of Krea 2 weights are well past the slack
    result = studio.plan("moody-krea2-v9")
    estimate = result["estimate"]
    assert estimate["fits"] is False
    codes = {item["code"] for item in estimate["warnings"]}
    assert "does_not_fit" in codes
    suggestions = {item["code"] for item in estimate["suggestions"]}
    assert {"lower_resolution", "lighter_model"} <= suggestions
    # Everything smaller in the family is offered, in catalog order.
    assert [item["key"] for item in result["lighter"]] == [
        "kreanoct-v4", "biglove-kreaedit3", "realdream-krea2-v3", "pornmaster-krea2-v4-int4",
    ]


def test_a_model_that_fits_is_reported_as_fitting(studio: ImageStudio) -> None:
    estimate = studio.plan("qwen-image-2.1-uc")["estimate"]
    assert estimate["fits"] is True
    assert estimate["needed_gb"] < estimate["usable_gb"]


def test_a_small_overshoot_is_tight_not_impossible() -> None:
    """KreaNoct is 14.5 GB against 14.2: ComfyUI streams the surplus, so it runs (14 s here)."""
    parts = [
        {"role": "diffusion_model", "size_bytes": 13_492_706_520},
        {"role": "vae", "size_bytes": 253_806_246},
        {"role": "text_encoder", "size_bytes": 5_242_467_968},
    ]
    estimate = plan.estimate(
        family="krea2", vram_gb=19.0, components=parts, usable_gb=14.2, free_vram_gb=None,
        engine_running=True, lmstudio_loaded=False,
    )
    assert estimate["needed_gb"] == 14.5
    assert estimate["fits"] is True
    codes = {item["code"] for item in estimate["warnings"]}
    assert "does_not_fit" not in codes and "tight" in codes


def test_the_text_encoder_is_not_counted_with_the_diffusion_model() -> None:
    """The encoder reads the prompt and leaves the card: the peak is the bigger of the two."""
    parts = [
        {"role": "diffusion_model", "size_bytes": 5_876_556_576},
        {"role": "vae", "size_bytes": 675_509_688},
        {"role": "text_encoder", "size_bytes": 9_350_798_360},
        {"role": "custom_node", "size_bytes": None},
    ]
    result = plan.estimate(
        family="qwen_image", vram_gb=16.0, components=parts, usable_gb=14.2, free_vram_gb=None,
        engine_running=True, lmstudio_loaded=False,
    )
    assert result["needed_gb"] == round(9.35 * 1.02 + plan.OVERHEAD_GB, 1)
    assert result["fits"] is True
    # With an unknown size the estimate falls back to the catalog figure instead of guessing.
    parts[0]["size_bytes"] = 0
    fallback = plan.estimate(
        family="qwen_image", vram_gb=16.0, components=parts, usable_gb=14.2, free_vram_gb=None,
        engine_running=True, lmstudio_loaded=False,
    )
    assert fallback["needed_gb"] == 16.5 and fallback["fits"] is False


def test_an_out_of_memory_error_comes_back_with_hints(studio: ImageStudio) -> None:
    studio.client.queue = lambda graph: (_ for _ in ()).throw(  # type: ignore[method-assign]
        ComfyError("Allocation on device failed: CUDA out of memory")
    )
    job = studio.generate(GenerationRequest(model_key="kreanoct-v4", prompt="x"))
    done = wait_for(studio, job["id"])
    assert done["status"] == "failed"
    assert {item["code"] for item in done["hints"]} == {
        "lower_resolution", "lighter_model", "close_lmstudio", "enable_offload",
    }


# -- downloading ------------------------------------------------------------


def test_a_download_is_resolved_verified_and_recorded(studio: ImageStudio) -> None:
    payload = b"x" * 2048
    digest = hashlib.sha256(payload).hexdigest()
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://huggingface.co/api/models/Comfy-Org/Qwen-Image-2.1/tree/main").mock(
            return_value=httpx.Response(200, json=[
                {"type": "file", "path": "vae/qwen_image_2.1_vae_bf16.safetensors", "size": len(payload),
                 "lfs": {"oid": digest}},
            ])
        )
        mock.get(
            "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors"
        ).mock(return_value=httpx.Response(200, content=payload))
        studio.components.start_download(["qwen21-vae"])
        deadline = time.time() + 5
        while time.time() < deadline:
            row = studio.components.component_state("qwen21-vae")
            if row["status"] in ("present", "failed"):
                break
            time.sleep(0.02)
    row = studio.components.component_state("qwen21-vae")
    assert row["status"] == "present", row.get("error")
    target = studio.components.models_dir / "vae" / "qwen_image_2.1_vae_bf16.safetensors"
    assert target.read_bytes() == payload
    state = studio.components.state("qwen21-vae")
    assert state["sha256"] == digest and state["size_bytes"] == len(payload)


def test_a_checksum_that_does_not_match_leaves_no_file(studio: ImageStudio) -> None:
    payload = b"y" * 1000
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith="https://huggingface.co/api/models/").mock(
            return_value=httpx.Response(200, json=[
                {"type": "file", "path": "vae/qwen_image_2.1_vae_bf16.safetensors", "size": len(payload),
                 "lfs": {"oid": "0" * 64}},
            ])
        )
        mock.get(url__startswith="https://huggingface.co/Comfy-Org/").mock(
            return_value=httpx.Response(200, content=payload)
        )
        studio.components.start_download(["qwen21-vae"])
        deadline = time.time() + 5
        while time.time() < deadline:
            if studio.components.component_state("qwen21-vae")["status"] in ("present", "failed"):
                break
            time.sleep(0.02)
    row = studio.components.component_state("qwen21-vae")
    assert row["status"] == "failed"
    assert "checksum" in (row["error"] or "")
    assert not (studio.components.models_dir / "vae" / "qwen_image_2.1_vae_bf16.safetensors").exists()


def test_a_component_without_a_source_is_not_downloadable(studio: ImageStudio) -> None:
    row = studio.components.component_state("moss-diffusion-sft")
    assert row["downloadable"] is False
    with pytest.raises(ComponentError, match="no download source"):
        studio.components.start_download(["moss-diffusion-sft"])


def test_the_uncensored_qwen_build_has_a_verified_source() -> None:
    from agentfit.imagegen.components import definition

    unet = definition("qwen21uc-unet")
    assert unet is not None and unet.downloadable
    assert unet.source["repo"] == "abenzerps/Qwen-Image-2.1-Uncensored-Quantized"
    assert unet.file == "qwen-image-2.1-UC-Q6_K.gguf"
    node = definition("node-gguf")
    assert node is not None and "leejet" in node.source["repo"]


def test_the_qwen_uc_flow_is_the_one_that_works_in_comfyui(studio: ImageStudio) -> None:
    """Unet Loader (GGUF) + CLIP qwen_image + VAE 2.1, KSampler 25 steps, CFG 1, euler / simple."""
    profile = catalog.by_key("qwen-image-2.1-uc")
    assert profile is not None
    template = templates.by_key(profile.template)
    request = GenerationRequest(model_key=profile.key, prompt="a photograph", seed=7).normalised()
    for cid in profile.component_ids():
        part = studio.components.component_state(cid)
        if part["role"] != "custom_node":
            write(studio.components.models_dir / part["folder"] / part["file"])
    graph = studio.graph_for(profile, template, request)
    assert graph["1"]["class_type"] == "UnetLoaderGGUF"
    assert graph["1"]["inputs"] == {"unet_name": "qwen-image-2.1-UC-Q6_K.gguf"}
    assert graph["2"]["inputs"] == {
        "clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image", "device": "default",
    }
    assert graph["3"]["inputs"] == {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}
    assert graph["4"]["class_type"] == "TextEncodeQwenImage21"
    sampler = graph["6"]["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"], sampler["scheduler"]) == (
        25, 1.0, "euler", "simple",
    )


def test_a_model_negative_prompt_is_used_when_nobody_writes_one(studio: ImageStudio) -> None:
    profile = catalog.by_key("noctq-v4")
    assert profile is not None and "blurry" in profile.negative
    template = templates.by_key(profile.template)
    plain = studio.graph_for(profile, template, GenerationRequest(model_key=profile.key, prompt="x").normalised())
    assert plain["4"]["inputs"]["negative_prompt"] == profile.negative
    own = studio.graph_for(
        profile, template, GenerationRequest(model_key=profile.key, prompt="x", negative="my own").normalised()
    )
    assert own["4"]["inputs"]["negative_prompt"] == "my own"


def test_a_civitai_file_is_refused_without_a_token(studio: ImageStudio) -> None:
    row = studio.components.component_state("krea2-realdream-v3")
    assert row["downloadable"] is True and row["needs_token"] is True
    studio.components.start_download(["krea2-realdream-v3"])
    deadline = time.time() + 5
    while time.time() < deadline:
        if studio.components.component_state("krea2-realdream-v3")["status"] in ("present", "failed"):
            break
        time.sleep(0.02)
    row = studio.components.component_state("krea2-realdream-v3")
    assert row["status"] == "failed" and "token" in (row["error"] or "")
    studio.settings.images.civitai_token = "abc"
    assert studio.components.component_state("krea2-realdream-v3")["needs_token"] is False


def test_civitai_resolves_the_file_the_catalog_names(studio: ImageStudio) -> None:
    from agentfit.imagegen.components import Resolver, definition

    studio.settings.images.civitai_token = "abc"
    component = definition("krea2-realdream-v3")
    assert component is not None
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://civitai.com/api/v1/model-versions/3396368").mock(return_value=httpx.Response(200, json={
            "files": [
                {"id": 3286566, "name": "fp8.safetensors", "primary": True, "sizeKB": 100.0,
                 "downloadUrl": "https://civitai.com/api/download/models/3396368?fileId=3286566"},
                {"id": 3304483, "name": "realDream_krea2V3_3304483.safetensors", "sizeKB": 2.0,
                 "hashes": {"SHA256": "AB" * 32},
                 "downloadUrl": "https://civitai.com/api/download/models/3396368?fileId=3304483"},
            ],
        }))
        resolved = Resolver(studio.settings).resolve(component)
    assert resolved.filename == "realDream_krea2V3_3304483.safetensors"
    assert "fileId=3304483" in resolved.url and "token=abc" in resolved.url
    assert resolved.sha256 == "ab" * 32


def test_downloading_a_model_fetches_only_its_missing_shared_parts(studio: ImageStudio) -> None:
    plan_payload = studio.components.plan("realdream-krea2-v3")
    wanted = [item["id"] for item in plan_payload["missing"] if item["downloadable"]]
    assert wanted == ["krea2-realdream-v3"]


# -- the engine -------------------------------------------------------------


def test_the_engine_is_found_where_it_is_installed(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("print('hi')", encoding="utf-8")
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    monkeypatch.setattr("agentfit.imagegen.engine._candidate_roots", lambda configured: [root])
    install = find_install("")
    assert install is not None and install.root == root and install.kind == "manual"


def test_a_folder_without_main_py_is_not_an_install(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "NotComfy"
    root.mkdir()
    monkeypatch.setattr("agentfit.imagegen.engine._candidate_roots", lambda configured: [root])
    assert find_install("") is None


def test_starting_without_an_install_says_so(monkeypatch) -> None:
    from agentfit.imagegen.engine import ComfyEngine, EngineError

    monkeypatch.setattr("agentfit.imagegen.engine._candidate_roots", lambda configured: [])
    engine = ComfyEngine(Settings(), EventBus())
    monkeypatch.setattr(engine, "running", lambda: None)
    with pytest.raises(EngineError, match="not installed"):
        engine.start()


# -- generating -------------------------------------------------------------


def test_generate_draws_saves_and_records_the_picture(studio: ImageStudio) -> None:
    job = studio.generate(GenerationRequest(
        model_key="ckpt:realvisxlV50_v50Bakedvae.safetensors", prompt="a studio portrait", width=1024, height=1536,
        seed=42, steps=12,
    ))
    done = wait_for(studio, job["id"])
    assert done["status"] == "done", done["error"]
    assert done["template"] == "sdxl_txt2img"
    graph = studio.client.queued[0]  # type: ignore[attr-defined]
    assert graph["1"]["inputs"]["ckpt_name"] == "realvisxlV50_v50Bakedvae.safetensors"
    assert graph["4"]["inputs"] == {"width": 1024, "height": 1536, "batch_size": 1}
    assert graph["5"]["inputs"]["seed"] == 42 and graph["5"]["inputs"]["steps"] == 12
    gallery = studio.gallery()
    assert len(gallery) == 1
    assert gallery[0]["width"] == 1024 and gallery[0]["prompt"] == "a studio portrait"
    stored = studio.image_path(done["image_id"])
    assert stored.exists() and stored.read_bytes() == STORED_PNG


def test_one_run_can_make_several_pictures(studio: ImageStudio) -> None:
    job = studio.generate(GenerationRequest(model_key="kreanoct-v4", prompt="x", batch=3))
    done = wait_for(studio, job["id"])
    assert done["status"] == "done", done["error"]
    assert len(done["image_ids"]) == 3
    assert len(studio.gallery()) == 3
    assert studio.client.queued[0]["5"]["inputs"]["batch_size"] == 3  # type: ignore[attr-defined]


def test_an_incomplete_model_is_refused_before_queueing(studio: ImageStudio) -> None:
    with pytest.raises(Exception, match="not complete"):
        studio.generate(GenerationRequest(model_key="realdream-krea2-v3", prompt="x"))
    assert studio.client.queued == []  # type: ignore[attr-defined]


def test_an_edit_needs_a_picture(studio: ImageStudio) -> None:
    with pytest.raises(Exception, match="needs a picture"):
        studio.generate(
            GenerationRequest(model_key="ckpt:realvisxlV50_v50Bakedvae.safetensors", template_key="sdxl_img2img", prompt="x")
        )


def test_sizes_are_rounded_to_multiples_of_eight(studio: ImageStudio) -> None:
    job = studio.generate(GenerationRequest(model_key="kreanoct-v4", prompt="x", width=1021, height=1003))
    wait_for(studio, job["id"])
    assert studio.client.queued[0]["5"]["inputs"]["width"] == 1016  # type: ignore[attr-defined]
    assert studio.client.queued[0]["5"]["inputs"]["height"] == 1000  # type: ignore[attr-defined]


# -- HTTP -------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch, tmp_path, models_dir):
    monkeypatch.setattr("agentfit.catalog.service.load_seed", lambda: ())
    monkeypatch.setattr(storage, "free_gb_at", lambda path: 400.0)
    monkeypatch.delenv("AGENTFIT_TOKEN", raising=False)
    services = AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())
    engine = FakeEngine(tmp_path)
    store = ComponentStore(services.db, services.settings, services.bus, engine, drives=lambda: [])
    services._images = ImageStudio(
        services.db, services.settings, services.bus, client=FakeComfy(), images_dir=tmp_path / "images",
        engine=engine, components=store, vram_budget_gb=lambda: 14.2, lmstudio_loaded=lambda: False,
    )
    client = TestClient(create_app(services))
    client.services = services  # type: ignore[attr-defined]
    return client


def test_the_api_lists_the_engine_models_and_components(api) -> None:
    engine = api.get("/api/images/engine").json()
    assert engine["installed"] is True
    assert api.get("/api/images/engine?refresh=1").json()["version"] == "0.38.0"
    models = api.get("/api/images/models").json()["models"]
    assert any(row["key"] == "kreanoct-v4" and row["installed"] for row in models)
    components = api.get("/api/images/components").json()["components"]
    assert any(item["id"] == "krea2-te" and item["status"] == "present" for item in components)


def test_the_api_starts_the_engine(api) -> None:
    response = api.post("/api/images/engine/start")
    assert response.status_code == 200, response.text
    assert api.services.images.engine.started == 1  # type: ignore[attr-defined]


def test_the_api_plans_a_download(api) -> None:
    api.services.images._vram_budget_gb = lambda: 12.0
    plan_payload = api.get("/api/images/models/moody-krea2-v9/plan").json()
    assert plan_payload["estimate"]["fits"] is False
    assert [item["id"] for item in plan_payload["missing"]] == ["krea2-moody-v9"]


def test_the_api_refuses_to_download_what_has_no_source(api) -> None:
    response = api.post("/api/images/models/moss-image-sft/download")
    assert response.status_code == 200
    body = response.json()
    assert body["started"] == []
    assert [item["id"] for item in body["skipped"]] == ["moss-diffusion-sft", "moss-te-9b", "moss-vae"]


def test_the_api_generates_and_serves_the_file(api) -> None:
    created = api.post("/api/images/generate", json={
        "model_key": "kreanoct-v4", "prompt": "a portrait", "seed": 5,
    })
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]
    for _ in range(200):
        job = api.get(f"/api/images/jobs/{job_id}").json()["job"]
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert job["status"] == "done", job
    gallery = api.get("/api/images/gallery").json()["images"]
    assert gallery[0]["seed"] == 5
    file = api.get(f"/api/images/gallery/{job['image_id']}/file")
    assert file.status_code == 200 and file.content == STORED_PNG


def test_the_api_refuses_an_impossible_request(api) -> None:
    assert api.post("/api/images/generate", json={"model_key": "nope", "prompt": "x"}).status_code == 400
    assert api.post("/api/images/generate", json={"model_key": "kreanoct-v4", "prompt": ""}).status_code == 400


def test_the_api_says_the_engine_is_offline(api) -> None:
    api.services.images.client.offline = True  # type: ignore[attr-defined]
    created = api.post("/api/images/generate", json={"model_key": "kreanoct-v4", "prompt": "x"})
    assert created.status_code == 200
    job_id = created.json()["job"]["id"]
    for _ in range(200):
        job = api.get(f"/api/images/jobs/{job_id}").json()["job"]
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert job["status"] == "failed"
    assert "not responding" in job["error"]
    assert api.get("/api/images/status").json()["available"] is False


def test_the_plan_costs_are_labelled_as_estimates() -> None:
    result = plan.estimate(
        family="sdxl", vram_gb=7.0, components=[], usable_gb=14.2, free_vram_gb=13.0,
        engine_running=True, lmstudio_loaded=False,
    )
    assert result["basis"] == "estimate"
    assert result["fits"] is True
    assert result["max_megapixels"] and result["max_megapixels"] > 1.0


def test_the_catalog_only_names_components_that_exist() -> None:
    from agentfit.imagegen.components import definition

    for profile in catalog.profiles():
        for component_id in profile.component_ids():
            assert definition(component_id) is not None, f"{profile.key} -> {component_id}"


def test_the_civitai_krea_models_name_the_file_and_need_a_token() -> None:
    """Big Love, Real Dream and PornMaster each point at one exact Civitai file, not the version's first."""
    from agentfit.imagegen.components import definition

    expected = {
        "krea2-biglove-kreaedit3": 3314179,
        "krea2-realdream-v3": 3304483,
        "krea2-pornmaster-v4": 3262057,
        "krea2-pornmaster-v4-int4": 3262023,
    }
    for component_id, file_id in expected.items():
        component = definition(component_id)
        assert component is not None, component_id
        assert component.source["kind"] == "civitai" and component.source["file_id"] == file_id
        assert component.source["needs_token"] is True
    for key in ("biglove-kreaedit3", "realdream-krea2-v3", "pornmaster-krea2-v4", "pornmaster-krea2-v4-int4"):
        profile = catalog.by_key(key)
        assert profile is not None and profile.family == "krea2" and profile.nsfw == "native"
        assert profile.template == "krea2_txt2img"


def test_the_zimage_models_use_the_lumina2_graph(studio: ImageStudio) -> None:
    """Unet Loader (GGUF) + CLIPLoader lumina2 + the 16-channel VAE; a zeroed negative; nine steps at CFG 1."""
    profile = catalog.by_key("zimage-nsfw-v2")
    assert profile is not None and profile.family == "zimage" and profile.requires == ("node-gguf",)
    assert profile.component_ids() == ["zimage-nsfw-v2-q8", "zimage-te", "zimage-vae", "node-gguf"]
    template = templates.by_key(profile.template)
    for cid in profile.component_ids():
        part = studio.components.component_state(cid)
        if part["role"] != "custom_node":
            write(studio.components.models_dir / part["folder"] / part["file"])
    graph = studio.graph_for(profile, template, GenerationRequest(model_key=profile.key, prompt="a photograph", seed=3).normalised())
    assert graph["1"]["class_type"] == "UnetLoaderGGUF"
    assert graph["1"]["inputs"] == {"unet_name": "z_image_nsfw_v2-Q8_0.gguf"}
    assert graph["2"]["inputs"] == {"clip_name": "qwen_3_4b_fp8_mixed.safetensors", "type": "lumina2", "device": "default"}
    assert graph["3"]["inputs"] == {"vae_name": "ae.safetensors"}
    assert graph["9"]["class_type"] == "ConditioningZeroOut" and graph["6"]["inputs"]["negative"] == ["9", 0]
    sampler = graph["6"]["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"], sampler["scheduler"]) == (9, 1.0, "euler", "simple")


def test_the_zimage_parts_have_free_hugging_face_sources() -> None:
    from agentfit.imagegen.components import definition

    for component_id in ("zimage-te", "zimage-vae", "zimage-nsfw-v2-q8", "zimage-cyberrealistic-v9-q6k"):
        component = definition(component_id)
        assert component is not None and component.source["kind"] == "huggingface", component_id
        assert not component.source.get("needs_token")


def test_pony_runs_with_clip_skip_two_and_its_score_tags(studio: ImageStudio) -> None:
    profile = catalog.by_key("pony-diffusion-v6-xl")
    assert profile is not None and profile.template == "pony_txt2img"
    assert profile.components == {"checkpoint": "sdxl-pony-v6"}
    template = templates.by_key(profile.template)
    write(studio.components.models_dir / "checkpoints" / "ponyDiffusionV6XL_v6StartWithThisOne.safetensors")
    request = GenerationRequest(model_key=profile.key, prompt="photo, a woman on a balcony", seed=5).normalised()
    graph = studio.graph_for(profile, template, request)
    assert graph["10"]["class_type"] == "CLIPSetLastLayer" and graph["10"]["inputs"]["stop_at_clip_layer"] == -2
    assert graph["2"]["inputs"]["clip"] == ["10", 0] and graph["3"]["inputs"]["clip"] == ["10", 0]
    assert graph["2"]["inputs"]["text"] == "score_9, score_8_up, score_7_up, photo, a woman on a balcony"
    assert "source_anime" in graph["3"]["inputs"]["text"]  # the photographic negative is the starting point
    # A prompt that already begins with the tags is not given them twice.
    again = studio.graph_for(
        profile, template,
        GenerationRequest(model_key=profile.key, prompt="score_9, score_8_up, score_7_up, a woman", seed=5).normalised(),
    )
    assert again["2"]["inputs"]["text"].count("score_9") == 1


def test_a_lora_on_pony_sits_before_the_clip_skip(studio: ImageStudio) -> None:
    template = templates.by_key("pony_txt2img")
    values = {
        "prompt": "x", "negative": "y", "width": 832, "height": 1216, "seed": 1, "steps": 25, "cfg": 7.0,
        "sampler": "euler_ancestral", "scheduler": "normal", "denoise": 1.0, "batch_size": 1,
        "filename_prefix": "t", "checkpoint": "pony.safetensors",
    }
    graph = templates.apply_loras(templates.bind(template, values), template, [{"name": "a.safetensors", "weight": 0.7}])
    assert graph["10"]["inputs"]["clip"] == ["lora1", 1]
    assert graph["5"]["inputs"]["model"] == ["lora1", 0]


def test_the_free_sdxl_checkpoints_download_without_a_token() -> None:
    from agentfit.imagegen.components import definition

    for component_id in ("sdxl-pony-v6", "sdxl-realvisxl-v5"):
        component = definition(component_id)
        assert component is not None and component.source["kind"] == "huggingface", component_id
        assert not component.source.get("needs_token")
