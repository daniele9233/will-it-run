"""Workflow templates: a ComfyUI graph with holes, loaded from JSON.

Adding a model family is adding a file to ``data/workflows``: no Python, no schema
migration, and nothing is claimed about a model the template has not been written for.
That is deliberate — a graph that has not been matched to its loaders produces
garbage, and this app would rather show "no template for this family" than a wrong
picture.

A template is a ComfyUI graph whose string values may hold ``{{name}}`` placeholders.
``bind`` fills them from the parameters and the chosen model profile:

- a value that is exactly one placeholder keeps the parameter's type, so ``"{{seed}}"``
  becomes a number and ``"{{width}}"`` an integer, which is what ComfyUI validates;
- an input whose value ends up ``None`` or ``""`` is removed, so one template can carry
  the extra inputs of one loader variant (``weight_dtype``) without breaking the other;
- ``{{seed_name}}`` is not a thing: the list of allowed names is the parameters of the
  request plus the fields of the model profile, and anything else is an error, not a
  silent empty string.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_WHOLE = re.compile(r"^\{\{(\w+)\}\}$")


class TemplateError(Exception):
    """A template cannot be bound to these parameters."""


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    family: str
    kind: str  # "txt2img" | "edit" | "upscale"
    description: str
    roles: tuple[str, ...]  # model roles the graph needs: checkpoint / unet / clip / vae
    lora_mode: str  # "model_and_clip" | "model" | "none"
    lora_source: str  # node whose MODEL (and CLIP) output feeds the LoRAs
    defaults: dict[str, Any] = field(default_factory=dict)
    graph: dict[str, Any] = field(default_factory=dict)
    #: Engine features the graph relies on, for the compatibility note in the interface.
    needs: tuple[str, ...] = ()
    #: The one to pick when the person has not: the plainest workflow of its family and kind.
    default: bool = False

    def classes(self, graph: dict[str, Any] | None = None) -> list[str]:
        """Every node class the graph uses, custom nodes included."""
        source = graph if graph is not None else self.graph
        found = {str(node.get("class_type")) for node in source.values() if isinstance(node, dict)}
        return sorted(found)

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "family": self.family,
            "kind": self.kind,
            "description": self.description,
            "roles": list(self.roles),
            "lora_mode": self.lora_mode,
            "defaults": self.defaults,
            "needs": list(self.needs),
        }


DATA_DIR = Path(__file__).with_name("data") / "workflows"


def _load() -> list[Template]:
    out: list[Template] = []
    for entry in sorted(DATA_DIR.glob("*.json"), key=lambda e: e.name):
        raw = json.loads(entry.read_text(encoding="utf-8"))
        out.append(
            Template(
                key=str(raw["key"]),
                name=str(raw["name"]),
                family=str(raw["family"]),
                kind=str(raw["kind"]),
                description=str(raw.get("description", "")),
                roles=tuple(raw.get("roles") or ()),
                lora_mode=str(raw.get("lora_mode") or "none"),
                lora_source=str(raw.get("lora_source") or "1"),
                defaults=dict(raw.get("defaults") or {}),
                graph=dict(raw["graph"]),
                needs=tuple(raw.get("needs") or ()),
                default=bool(raw.get("default", False)),
            )
        )
    return out


_TEMPLATES: list[Template] | None = None


def templates() -> list[Template]:
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = _load()
    return _TEMPLATES


def by_key(key: str) -> Template:
    for template in templates():
        if template.key == key:
            return template
    raise TemplateError(f"There is no workflow template called {key!r}.")


def for_family(family: str, kind: str | None = None) -> list[Template]:
    found = [t for t in templates() if t.family == family and (kind is None or t.kind == kind)]
    return sorted(found, key=lambda t: (not t.default, t.name.lower()))


# -- binding ----------------------------------------------------------------


def _coerce(text: str, values: dict[str, Any], name: str) -> Any:
    if name not in values:
        raise TemplateError(f"The template needs {name!r} and it was not given.")
    return values[name]


def _substitute(value: Any, values: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = _WHOLE.match(value)
        if whole:
            return _coerce(value, values, whole.group(1))
        return _PLACEHOLDER.sub(lambda m: str(_coerce(value, values, m.group(1))), value)
    if isinstance(value, list):
        return [_substitute(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, values) for key, item in value.items()}
    return value


def bind(template: Template, values: dict[str, Any]) -> dict[str, Any]:
    """Fill the placeholders, then drop the nodes and inputs that stayed empty.

    A template can describe optional reference pictures: with no picture given, its
    ``LoadImage`` has no input, is removed, and whatever read from it loses that input
    too (the fourth reference of an edit). A link to a node that the template never had
    is a typo, and raises instead of producing a graph that quietly ignores it.
    """
    graph = _substitute(copy.deepcopy(template.graph), values)
    original = set(graph)
    for node_id, node in list(graph.items()):
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for name in [n for n, v in inputs.items() if v is None or v == ""]:
            del inputs[name]
        if not inputs:
            del graph[node_id]
    for node_id, node in graph.items():
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, value in list(inputs.items()):
            if not (isinstance(value, list) and len(value) == 2):
                continue
            source = str(value[0])
            if source in graph and graph[source].get("inputs"):
                continue
            if source in original:
                # An optional node that this request did not fill: its reader loses that input.
                del inputs[key]
                continue
            raise TemplateError(f"{template.key}: node {node_id}.{key} reads from node {source}, which is not in it.")
    if not graph:
        raise TemplateError(f"The template {template.key} is empty.")
    return graph


def apply_loras(graph: dict[str, Any], template: Template, loras: list[dict[str, Any]]) -> dict[str, Any]:
    """Chain LoRA loaders onto the checkpoint/unet node, before anything reads it.

    SDXL checkpoints carry both a MODEL and a CLIP, and a LoRA usually modifies both;
    a diffusion-model loader has only a MODEL. ``lora_mode`` says which, per family.
    """
    if not loras or template.lora_mode == "none":
        return graph
    source = template.lora_source
    model_only = template.lora_mode == "model"
    chain: list[str] = []
    for index, lora in enumerate(loras):
        name = str(lora.get("name") or "").strip()
        if not name:
            continue
        weight = float(lora.get("weight", 0.8))
        node_id = f"lora{index + 1}"
        chain.append(node_id)
        graph[node_id] = {
            "class_type": "LoraLoaderModelOnly" if model_only else "LoraLoader",
            "inputs": {
                "lora_name": name,
                "strength_model": weight,
                "model": [source, 0] if len(chain) == 1 else [chain[-2], 0],
            },
        }
        if not model_only:
            graph[node_id]["inputs"]["clip"] = [source, 1] if len(chain) == 1 else [chain[-2], 1]
            graph[node_id]["inputs"]["strength_clip"] = float(lora.get("weight_clip", weight))
    if not chain:
        return graph
    last = chain[-1]
    for node_id, node in graph.items():
        if node_id in chain:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, value in inputs.items():
            if not (isinstance(value, list) and len(value) == 2 and value[0] == source):
                continue
            if value[1] == 0:
                inputs[key] = [last, 0]
            elif value[1] == 1 and not model_only:
                inputs[key] = [last, 1]
    return graph
