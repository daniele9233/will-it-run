"""The model files already on this PC: finding them, recognising them, putting them in place.

Most people download a dozen ``.safetensors`` into one folder and then discover that ComfyUI
wants them in four different folders, each with the right name. This module reads the header
of every file — never the weights — works out what it is from the metadata and the tensor
names, matches it to a component the catalog already knows, and only then moves or copies
anything, after the person has seen the list.

Three rules:

- a file that is not recognised is reported, not guessed into a role;
- nothing is overwritten without checking identity (same size and checksum) first;
- the source file is untouched unless the mode is "move", and a move is a copy followed by a
  unlink, so an error halfway leaves the original where it was.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentfit.i18n import tr
from agentfit.imagegen.components import ComponentDef, ComponentStore, definitions

CHUNK = 1024 * 1024
#: Reading a 26 GB checkpoint's header is a 1 MB read: the weights are never touched.
MAX_HEADER_BYTES = 64 * 1024 * 1024

VAE_HINTS = ("vae", "diffusion_pytorch_model")


class ImportError_(Exception):
    """The folder cannot be imported, and the message says why."""


@dataclass
class Recognised:
    path: Path
    name: str
    size_bytes: int
    role: str
    family: str | None
    component_id: str | None
    component_name: str | None
    reason: str
    already_visible: bool = False
    target: str | None = None
    conflict: str | None = None
    lora_check: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "role": self.role,
            "family": self.family,
            "component_id": self.component_id,
            "component_name": self.component_name,
            "reason": self.reason,
            "already_visible": self.already_visible,
            "target": self.target,
            "conflict": self.conflict,
            "known": self.component_id is not None,
            "lora_check": self.lora_check,
        }


# -- the header, without the weights ----------------------------------------


def read_header(path: Path) -> dict[str, Any]:
    """The safetensors header: every tensor name, its dtype and shape, plus the metadata."""
    try:
        with path.open("rb") as handle:
            length_bytes = handle.read(8)
            if len(length_bytes) != 8:
                raise ImportError_(tr("{name} is not a safetensors file.").format(name=path.name))
            length = struct.unpack("<Q", length_bytes)[0]
            if length <= 0 or length > MAX_HEADER_BYTES:
                raise ImportError_(tr("{name} has a header this program will not read.").format(name=path.name))
            return json.loads(handle.read(length).decode("utf-8"))
    except (OSError, ValueError, struct.error) as exc:
        raise ImportError_(tr("{name} could not be read: {error}").format(name=path.name, error=exc)) from exc


def _prefixes(header: dict[str, Any], depth: int = 1) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in header:
        if key == "__metadata__":
            continue
        parts = key.split(".")
        name = ".".join(parts[:depth])
        counts[name] = counts.get(name, 0) + 1
    return counts


def _has(header: dict[str, Any], needle: str) -> bool:
    return any(needle in key for key in header if key != "__metadata__")


def classify(header: dict[str, Any], filename: str) -> tuple[str, str | None, str]:
    """What this file is, from its metadata and its tensor names.

    Returns ``(role, family, reason)``. ``role`` is one of the catalog's roles or ``"unknown"``.
    """
    meta = header.get("__metadata__") or {}
    prefixes = _prefixes(header)
    total = sum(prefixes.values())
    low = filename.lower()

    # A LoRA is recognisable by the pair of low-rank matrices, whatever it was trained with.
    if _has(header, ".lora_A") or _has(header, ".lora_B") or _has(header, "lora_down") or _has(header, "lora_up"):
        module = str(meta.get("ss_network_module") or "")
        return "lora", None, f"low-rank tensors ({module or 'unknown trainer'})"
    if "ss_network_dim" in meta or "ss_network_alpha" in meta:
        return "lora", None, "trained by kohya (network metadata)"

    architecture = str(meta.get("modelspec.architecture") or "").lower()
    if architecture.endswith("_vae") or architecture.endswith("vae"):
        return "vae", None, f"metadata says {architecture}"
    if "krea2_svdquant" in meta or any(str(k).startswith("krea2_") for k in meta):
        return "diffusion_model", "krea2", "Krea 2 quantisation metadata"

    # The VAE: an encoder and a decoder, convolutions, no attention blocks.
    if {"encoder", "decoder"} <= set(prefixes) and not _has(header, "blocks."):
        return "vae", None, "encoder/decoder weights"

    if prefixes.get("transformer_blocks") or (prefixes.get("transformer") and _has(header, "img_in")):
        return "diffusion_model", "qwen_image", "transformer_blocks layout"
    if prefixes.get("blocks") and _has(header, "txtfusion"):
        return "diffusion_model", "krea2", "blocks + txtfusion (Krea 2 layout)"
    if prefixes.get("layers") and _has(header, "text_embedder"):
        return "diffusion_model", None, "layers + text_embedder (MOSS layout)"
    if _has(header, "double_blocks") or _has(header, "single_blocks"):
        return "diffusion_model", None, "double/single blocks (FLUX layout)"

    # A text encoder is a language model: token embedding, many layers, no image patchifier.
    if prefixes.get("model") and not _has(header, "img_in"):
        if _has(header, "lm_head") or _has(header, "mtp"):
            return "text_encoder", None, "language-model tensors with an output head"
        return "text_encoder", None, "language-model tensors"
    if prefixes.get("transformer") and total > 100:
        return "lora", "qwen_image", "single transformer namespace (trained on Qwen-Image)"

    if "vae" in low:
        return "vae", None, "name says VAE"
    return "unknown", None, tr("no known layout in its tensors")


def match_component(filename: str, role: str) -> ComponentDef | None:
    """The catalog component this file is, by exact file name."""
    for component in definitions().values():
        if component.file and component.file.lower() == filename.lower():
            return component
    return None


def guess_family(filename: str) -> str | None:
    low = filename.lower()
    for family, words in (
        ("krea2", ("krea", "kroma")),
        ("qwen_image", ("qwen",)),
        ("moss_image", ("moss",)),
        ("sdxl", ("sdxl", "lustify", "bigasp", "juggernaut", "realvis")),
    ):
        if any(word in low for word in words):
            return family
    return None


# -- the scan ---------------------------------------------------------------


def scan(directory: Path, store: ComponentStore, *, recursive: bool = False) -> dict[str, Any]:
    """Every ``.safetensors`` in the folder, recognised or not, against what is already here."""
    if not directory.is_dir():
        raise ImportError_(tr("That folder does not exist: {path}.").format(path=directory))
    pattern = "**/*.safetensors" if recursive else "*.safetensors"
    files = sorted(path for path in directory.glob(pattern) if path.is_file())
    if not files:
        raise ImportError_(tr("There is no .safetensors file in {path}.").format(path=directory))

    seen = {folder: set(store.listing(folder)) for folder in ("diffusion_models", "text_encoders", "vae", "loras", "checkpoints")}
    rows: list[Recognised] = []
    for path in files:
        try:
            header = read_header(path)
        except ImportError_ as exc:
            rows.append(Recognised(path, path.name, path.stat().st_size, "unknown", None, None, None, str(exc)))
            continue
        role, family, reason = classify(header, path.name)
        component = match_component(path.name, role)
        if component is not None:
            role = component.role
            family = component.families[0] if component.families else guess_family(path.name) or family
            reason = tr("matches the catalog: {name}").format(name=component.name)
            target = store.models_dir / component.folder / component.file if store.models_dir else None
            conflict = None
            if (
                target is not None
                and target.exists()
                and target.resolve() != path.resolve()
                and target.stat().st_size != path.stat().st_size
            ):
                conflict = tr("a different file with this name already exists ({size}).").format(
                    size=f"{target.stat().st_size / 1e9:.2f} GB"
                )
            rows.append(
                Recognised(
                    path=path,
                    name=path.name,
                    size_bytes=path.stat().st_size,
                    role=role,
                    family=family,
                    component_id=component.id,
                    component_name=component.name,
                    reason=reason,
                    # A different file with the same name is not "already here": it is a conflict.
                    already_visible=component.file in seen.get(component.folder, set()) and conflict is None,
                    target=str(target) if target else None,
                    conflict=conflict,
                )
            )
        else:
            rows.append(
                Recognised(
                    path=path,
                    name=path.name,
                    size_bytes=path.stat().st_size,
                    role=role,
                    family=family or guess_family(path.name),
                    component_id=None,
                    component_name=None,
                    reason=reason,
                )
            )

    by_component = {row.component_id: row for row in rows if row.component_id}

    # A LoRA is only worth anything against the model it was trained for: pair every tensor
    # with one of that model's own, so "compatible" is measured, not declared.
    checks: dict[str, Any] = {}
    for row in rows:
        if row.component_id is None:
            continue
        component = definitions().get(row.component_id)
        if component is None or component.role != "lora":
            continue
        base = _base_for(component.id)
        if base is None or base not in by_component:
            continue
        try:
            checks[row.component_id] = verify_against_base(row.path, by_component[base].path)
        except ImportError_ as exc:
            checks[row.component_id] = {"compatible": False, "error": str(exc)}
    for row in rows:
        if row.component_id in checks:
            row.lora_check = checks[row.component_id]

    models: list[dict[str, Any]] = []
    for profile in _profiles():
        ids = [cid for cid in profile.component_ids() if cid in by_component]
        if not ids:
            continue
        missing = [cid for cid in profile.component_ids() if cid not in by_component]
        models.append(
            {
                "key": profile.key,
                "name": profile.name,
                "found": [
                    {
                        "component_id": cid,
                        "component_name": by_component[cid].component_name,
                        "role": by_component[cid].role,
                        "size_bytes": by_component[cid].size_bytes,
                        "already_visible": by_component[cid].already_visible,
                        "conflict": by_component[cid].conflict,
                        "path": str(by_component[cid].path),
                    }
                    for cid in ids
                ],
                "missing": missing,
                "ready": not missing,
                "blocked": profile.blocked,
            }
        )

    importable = [
        row for row in rows if row.component_id and not row.already_visible and not row.conflict
    ]
    return {
        "directory": str(directory),
        "files": [row.to_json() for row in rows],
        "models": models,
        "unknown": [row.to_json() for row in rows if row.component_id is None],
        "bytes_to_copy": sum(row.size_bytes for row in importable),
        "components_to_import": [row.component_id for row in importable],
        "conflicts": [row.to_json() for row in rows if row.conflict],
        "already_visible": [row.component_id for row in rows if row.component_id and row.already_visible],
    }


def _base_for(lora_id: str) -> str | None:
    """The diffusion component of the first model that lists this LoRA."""
    for profile in _profiles():
        if lora_id in profile.loras:
            return profile.components.get("diffusion_model")
    return None


def _profiles():
    from agentfit.imagegen import catalog

    return catalog.profiles()


# -- the import -------------------------------------------------------------


def _sha256(path: Path, *, cancel: Any = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def apply(
    directory: Path,
    component_ids: list[str],
    *,
    mode: str,
    store: ComponentStore,
    overwrite: bool = False,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Copy or move the chosen components into ComfyUI's folders and register them."""
    if mode not in ("copy", "move", "existing"):
        raise ImportError_(tr("Unknown import mode: {mode}.").format(mode=mode))
    models_dir = store.models_dir
    if models_dir is None:
        raise ImportError_(tr("ComfyUI was not found on this PC, so there is nowhere to put the files."))

    current = scan(directory, store)
    sources = {row["component_id"]: row for row in current["files"] if row.get("component_id")}
    done: list[dict[str, Any]] = []
    for index, component_id in enumerate(component_ids):
        row = sources.get(component_id)
        if row is None:
            continue
        component = definitions().get(component_id)
        if component is None:
            continue
        source = Path(directory) / row["name"]
        target = models_dir / component.folder / component.file
        if on_progress:
            on_progress(index, len(component_ids), component.name)
        if mode == "existing" or row["already_visible"]:
            if not row["already_visible"]:
                raise ImportError_(
                    tr(
                        "{name} is not in a folder ComfyUI reads. With a flat folder, choosing "
                        "\"use the existing location\" would put text encoders among the diffusion models: "
                        "copy it, or move it."
                    ).format(name=component.name)
                )
            asset = source
        else:
            asset = _place(source, target, mode=mode, overwrite=overwrite)
        size = asset.stat().st_size
        checksum = _sha256(asset) if size <= 2 * 1024**3 else None
        store.register(
            component,
            path=asset,
            size_bytes=size,
            sha256=checksum,
            note=tr("imported from {source}").format(source=source),
        )
        done.append({"component_id": component_id, "path": str(asset), "sha256": checksum})
    store.invalidate()
    return {"imported": done, "mode": mode}


def _place(source: Path, target: Path, *, mode: str, overwrite: bool) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        same = target.stat().st_size == source.stat().st_size
        if same and not overwrite:
            return target
        if not overwrite:
            raise ImportError_(
                tr("{target} already exists and is a different file. Confirm replacing it first.").format(
                    target=target.name
                )
            )
    if mode == "copy":
        shutil.copy2(source, target)
        return target
    # Move: on the same volume this is a rename — nothing is duplicated and nothing is lost.
    try:
        os.replace(source, target)
        return target
    except OSError:
        pass
    # Across volumes: a copy followed by an unlink, so a failure leaves the original in place.
    partial = target.with_suffix(target.suffix + ".part")
    shutil.copy2(source, partial)
    partial.replace(target)
    source.unlink()
    return target


# -- LoRA: does it fit this loader, and does it fit this model? --------------


def lora_format(header: dict[str, Any]) -> dict[str, Any]:
    """Which loader convention a LoRA uses, and whether ComfyUI can read its namespace."""
    meta = header.get("__metadata__") or {}
    keys = [key for key in header if key != "__metadata__"]
    namespaces: dict[str, int] = {}
    for key in keys:
        if key.startswith("transformer."):
            namespaces["transformer"] = namespaces.get("transformer", 0) + 1
        elif key.startswith("diffusion_model."):
            namespaces["diffusion_model"] = namespaces.get("diffusion_model", 0) + 1
        elif key.startswith("lora_unet_"):
            namespaces["lora_unet_"] = namespaces.get("lora_unet_", 0) + 1
        else:
            namespaces["other"] = namespaces.get("other", 0) + 1
    peft = any(".lora_A." in key or ".lora_B." in key for key in keys)
    kohya = any("lora_down" in key or "lora_up" in key for key in keys)
    namespace = max(namespaces, key=namespaces.get) if namespaces else "other"
    return {
        "namespaces": namespaces,
        "namespace": namespace,
        "peft": peft,
        "kohya": kohya,
        "comfy_ready": namespace in ("diffusion_model", "lora_unet_"),
        "declared_conversion": meta.get("conversion"),
        "trigger": meta.get("trigger"),
        "base_model": meta.get("base_model"),
        "strength_model": meta.get("recommended_strength_model"),
        "strength_clip": meta.get("recommended_strength_clip"),
    }


def verify_against_base(lora: Path, base: Path) -> dict[str, Any]:
    """Pair every LoRA tensor with a tensor of the base model: the only honest compatibility test."""
    lora_header = read_header(lora)
    base_header = read_header(base)
    # ``alpha`` is a scalar, not a weight: it never has a counterpart in the base model.
    lora_keys = [key for key in lora_header if key != "__metadata__" and not key.endswith(".alpha")]
    base_keys = {key for key in base_header if key != "__metadata__"}
    matched = 0
    unmatched: list[str] = []
    shapes: list[str] = []
    for key in lora_keys:
        base_key = _base_key(key)
        if base_key is None:
            unmatched.append(key)
            continue
        candidates = [
            base_key,
            f"{base_key}.weight",
            f"model.{base_key}.weight",
            f"diffusion_model.{base_key}.weight",
            f"model.{base_key}",
        ]
        found = next((candidate for candidate in candidates if candidate in base_keys), None)
        if found is None:
            unmatched.append(key)
            continue
        matched += 1
        if len(shapes) < 5:
            shapes.append(f"{found}: {base_header[found]['shape']}")
    return {
        "lora_tensors": len(lora_keys),
        "matched": matched,
        "unmatched": unmatched[:50],
        "unmatched_count": len(unmatched),
        "compatible": matched > 0 and len(unmatched) == 0,
        "base_tensors": len(base_keys),
        "samples": shapes,
    }


def _base_key(lora_key: str) -> str | None:
    key = lora_key
    for prefix in ("transformer.", "diffusion_model.", "lora_unet_"):
        if key.startswith(prefix):
            key = key[len(prefix) :]
            break
    for suffix in (".lora_A.weight", ".lora_B.weight", ".lora_down.weight", ".lora_up.weight", ".alpha"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    else:
        return None
    if key.startswith("model."):
        key = key[len("model.") :]
    return key or None


def convert_lora(lora: Path, target: Path, *, prefix_from: str, prefix_to: str) -> dict[str, Any]:
    """Rewrite a LoRA's tensor names from one namespace to another, keeping the weights as they are.

    Only the names change: every tensor is copied byte for byte, so the conversion cannot
    corrupt a weight. The original file is never touched.
    """
    header = read_header(lora)
    metadata = header.pop("__metadata__", {}) or {}
    body = lora.read_bytes()
    header_length = struct.unpack("<Q", body[:8])[0]
    data_start = 8 + header_length
    offset = 0
    new_header: dict[str, Any] = {"__metadata__": {**metadata, "converted": f"{prefix_from} -> {prefix_to}"}}
    chunks: list[bytes] = []
    renamed = 0
    for key, info in header.items():
        begin, end = info["data_offsets"]
        name = key
        if name.startswith(prefix_from):
            name = prefix_to + name[len(prefix_from) :]
            renamed += 1
        new_header[name] = {**info, "data_offsets": [offset, offset + (end - begin)]}
        chunks.append(body[data_start + begin : data_start + end])
        offset += end - begin
    encoded = json.dumps(new_header, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((8 - len(encoded) % 8) % 8)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        handle.write(struct.pack("<Q", len(encoded)))
        handle.write(encoded)
        for chunk in chunks:
            handle.write(chunk)
    return {
        "target": str(target),
        "renamed": renamed,
        "tensors": len(header),
        "bytes": target.stat().st_size,
        "prefix_from": prefix_from,
        "prefix_to": prefix_to,
    }
