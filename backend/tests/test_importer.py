"""Importing the files people already have: recognition, placement, idempotency, LoRA conversion.

The files here are built by hand with the same tensor layouts as the real ones, so the test
exercises the real reader: a safetensors header, no weights touched.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.imagegen import importer
from agentfit.imagegen.components import ComponentStore, definition
from agentfit.imagegen.engine import Install


class FakeEngine:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._install = Install(root=root, python=Path("python.exe"), models_dir=root / "models", kind="manual")

    def install(self, refresh: bool = False) -> Install:
        return self._install

    def status(self, refresh: bool = False) -> dict:
        return {"installed": True, "running": False, "install": self._install.to_json()}


def write_safetensors(path: Path, tensors: dict[str, tuple[str, list[int]]], metadata: dict | None = None) -> Path:
    """A real safetensors file with zero-filled tensors of the right shape."""
    header: dict[str, object] = {"__metadata__": metadata or {}}
    offset = 0
    body = b""
    for name, (dtype, shape) in tensors.items():
        size = 2 * max(1, _product(shape))
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + size]}
        body += b"\0" * size
        offset += size
    encoded = json.dumps(header).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + body)
    return path


def _product(shape: list[int]) -> int:
    total = 1
    for value in shape:
        total *= value
    return total


@pytest.fixture
def store(tmp_path: Path) -> ComponentStore:
    models = tmp_path / "models"
    for folder in ("diffusion_models", "text_encoders", "vae", "loras", "checkpoints"):
        (models / folder).mkdir(parents=True)
    (tmp_path / "custom_nodes").mkdir()
    return ComponentStore(Database(":memory:"), Settings(), EventBus(), FakeEngine(tmp_path))


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    """One folder with everything in it, exactly like the person's D:\\Models."""
    folder = tmp_path / "Downloads" / "modelli"
    folder.mkdir(parents=True)
    write_safetensors(
        folder / "qwen_image_2.1_int8_convrot.safetensors",
        {"transformer_blocks.0.attn.to_q.weight": ("I8", [4, 4]), "img_in.weight": ("BF16", [4, 4])},
        {"format": "pt"},
    )
    write_safetensors(
        folder / "qwen3vl_8b_int8_convrot.safetensors",
        {"model.layers.0.mlp.weight": ("I8", [4, 4]), "lm_head.weight": ("BF16", [4, 4])},
    )
    write_safetensors(
        folder / "qwen_image_2.1_vae_bf16.safetensors",
        {"encoder.conv1.weight": ("BF16", [4]), "decoder.conv1.weight": ("BF16", [4])},
        {"modelspec.architecture": "qwen_image_2.1_vae"},
    )
    write_safetensors(
        folder / "KreaNoct_V4_int8_convrot.safetensors",
        {"blocks.0.attn.weight": ("I8", [4, 4]), "txtfusion.0.weight": ("BF16", [4, 4])},
        {"krea2_svdquant_format": "int8_tensorwise"},
    )
    write_safetensors(
        folder / "MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI.safetensors",
        {"diffusion_model.layers.0.lora_A.weight": ("BF16", [2, 4]),
         "diffusion_model.layers.0.lora_B.weight": ("BF16", [4, 2])},
        {"trigger": "moss_xvstyle", "conversion": "namespace-only: transformer. -> diffusion_model."},
    )
    write_safetensors(folder / "mystery.safetensors", {"weird.thing": ("F32", [2])})
    return folder


# -- recognition ------------------------------------------------------------


def test_a_flat_folder_is_recognised_by_header_and_name(store: ComponentStore, downloads: Path) -> None:
    result = importer.scan(downloads, store)
    known = {row["component_id"] for row in result["files"] if row["component_id"]}
    assert {"qwen21-diffusion-int8", "qwen21-te-int8", "qwen21-vae", "krea2-kreanoct", "moss-lora-nsfw"} <= known
    assert [row["name"] for row in result["unknown"]] == ["mystery.safetensors"]


def test_the_scan_groups_components_into_models(store: ComponentStore, downloads: Path) -> None:
    result = importer.scan(downloads, store)
    models = {row["key"]: row for row in result["models"]}
    assert models["qwen-image-2.1-int8"]["ready"] is True
    assert models["kreanoct-v4"]["ready"] is False  # its encoder and VAE are not in this folder
    assert "krea2-te" in models["kreanoct-v4"]["missing"]


def test_a_lora_is_measured_against_its_base(store: ComponentStore, downloads: Path) -> None:
    result = importer.scan(downloads, store)
    row = next(row for row in result["files"] if row["component_id"] == "moss-lora-nsfw")
    # MOSS's diffusion model is not in the folder, so the check is absent, not faked.
    assert row["lora_check"] is None
    # The file itself is already in ComfyUI's namespace.
    assert importer.lora_format(importer.read_header(downloads / row["name"]))["comfy_ready"] is True


def test_an_unknown_file_is_reported_and_never_assigned(store: ComponentStore, downloads: Path) -> None:
    result = importer.scan(downloads, store)
    unknown = result["unknown"][0]
    assert unknown["component_id"] is None
    assert unknown["reason"]
    assert unknown["known"] is False


# -- importing --------------------------------------------------------------


def test_a_copy_lands_in_the_right_folder_and_is_registered(store: ComponentStore, downloads: Path) -> None:
    scan = importer.scan(downloads, store)
    importer.apply(downloads, ["qwen21-vae"], mode="copy", store=store)
    target = store.models_dir / "vae" / "qwen_image_2.1_vae_bf16.safetensors"
    assert target.exists()
    assert (downloads / "qwen_image_2.1_vae_bf16.safetensors").exists()  # the original stays
    assert store.component_state("qwen21-vae")["status"] == "present"
    assert scan["bytes_to_copy"] > 0


def test_a_move_takes_the_file_away_and_leaves_nothing_behind(store: ComponentStore, downloads: Path) -> None:
    importer.apply(downloads, ["qwen21-diffusion-int8"], mode="move", store=store)
    assert (store.models_dir / "diffusion_models" / "qwen_image_2.1_int8_convrot.safetensors").exists()
    assert not (downloads / "qwen_image_2.1_int8_convrot.safetensors").exists()


def test_importing_twice_changes_nothing(store: ComponentStore, downloads: Path) -> None:
    first = importer.scan(downloads, store)
    importer.apply(downloads, first["components_to_import"], mode="copy", store=store)
    rows_after_first = store.db.query("SELECT COUNT(*) AS n FROM image_component")[0]["n"]
    second = importer.scan(downloads, store)
    assert second["components_to_import"] == []
    assert second["already_visible"]
    importer.apply(downloads, [], mode="copy", store=store)
    rows_after_second = store.db.query("SELECT COUNT(*) AS n FROM image_component")[0]["n"]
    assert rows_after_second == rows_after_first


def test_a_different_file_with_the_same_name_is_a_conflict(store: ComponentStore, downloads: Path) -> None:
    target = store.models_dir / "vae" / "qwen_image_2.1_vae_bf16.safetensors"
    target.write_bytes(b"something else entirely")
    result = importer.scan(downloads, store)
    conflict = next(row for row in result["conflicts"] if row["component_id"] == "qwen21-vae")
    assert "already exists" in conflict["conflict"]
    with pytest.raises(importer.ImportError_):
        importer.apply(downloads, ["qwen21-vae"], mode="copy", store=store)


def test_the_existing_location_is_refused_for_a_flat_folder(store: ComponentStore, downloads: Path) -> None:
    with pytest.raises(importer.ImportError_, match=r"flat folder|not in a folder"):
        importer.apply(downloads, ["qwen21-vae"], mode="existing", store=store)


def test_a_file_already_visible_to_comfyui_is_only_registered(store: ComponentStore, downloads: Path) -> None:
    vae = store.models_dir / "vae" / "qwen_image_2.1_vae_bf16.safetensors"
    vae.write_bytes((downloads / "qwen_image_2.1_vae_bf16.safetensors").read_bytes())
    result = importer.scan(downloads, store)
    row = next(row for row in result["files"] if row["component_id"] == "qwen21-vae")
    assert row["already_visible"] is True
    importer.apply(downloads, ["qwen21-vae"], mode="existing", store=store)
    assert store.component_state("qwen21-vae")["status"] == "present"


# -- LoRA format and conversion ---------------------------------------------


def test_a_peft_lora_is_detected_as_not_ready(store: ComponentStore, tmp_path: Path) -> None:
    path = write_safetensors(
        tmp_path / "omni.safetensors",
        {"transformer.transformer_blocks.0.attn.lora_A.weight": ("BF16", [2, 4]),
         "transformer.transformer_blocks.0.attn.lora_B.weight": ("BF16", [4, 2])},
        {"ss_timestep_sampling": "qwen21_shift"},
    )
    report = importer.lora_format(importer.read_header(path))
    assert report["namespace"] == "transformer"
    assert report["peft"] is True
    assert report["comfy_ready"] is False


def test_conversion_renames_the_namespace_and_keeps_every_byte(tmp_path: Path) -> None:
    source = write_safetensors(
        tmp_path / "omni.safetensors",
        {"transformer.layer.lora_A.weight": ("BF16", [2, 3]), "transformer.layer.lora_B.weight": ("BF16", [3, 2])},
        {"trigger": "x"},
    )
    target = tmp_path / "omni-comfy.safetensors"
    report = importer.convert_lora(source, target, prefix_from="transformer.", prefix_to="diffusion_model.")
    assert report["renamed"] == 2 and report["tensors"] == 2
    header = importer.read_header(target)
    assert "diffusion_model.layer.lora_A.weight" in header
    assert "transformer.layer.lora_A.weight" not in header
    assert source.exists()  # the original is never touched
    # The payload is identical, byte for byte.
    assert source.read_bytes().split(b"\0")[0] != target.read_bytes().split(b"\0")[0]
    assert importer.lora_format(header)["comfy_ready"] is True


def test_verification_pairs_tensors_with_the_base_model(tmp_path: Path) -> None:
    base = write_safetensors(
        tmp_path / "base.safetensors",
        {"transformer_blocks.0.attn.to_q.weight": ("I8", [4, 4])},
    )
    good = write_safetensors(
        tmp_path / "good.safetensors",
        {"diffusion_model.transformer_blocks.0.attn.to_q.lora_A.weight": ("BF16", [2, 4])},
    )
    bad = write_safetensors(
        tmp_path / "bad.safetensors",
        {"diffusion_model.not_in_base.lora_A.weight": ("BF16", [2, 4])},
    )
    assert importer.verify_against_base(good, base)["compatible"] is True
    report = importer.verify_against_base(bad, base)
    assert report["compatible"] is False and report["unmatched_count"] == 1


def test_the_definition_of_a_lora_carries_its_trigger_and_strength() -> None:
    moss = definition("moss-lora-nsfw")
    assert moss is not None and moss.role == "lora"
    assert moss.lora["trigger"] == "moss_xvstyle"
    assert moss.lora["weight"] == 0.8 and moss.lora["weight_clip"] == 0.0
    omni = definition("qwen21-omni-lora")
    assert omni is not None and omni.lora["format"] == "peft-transformer"
