"""A small JSON Schema validator: the part of the standard that answers to structured-output prompts need.

Supported: ``type`` (one or several), ``enum``, ``const``, ``properties``, ``required``, ``additionalProperties``
(boolean or schema), ``items``, ``minItems``, ``maxItems``, ``uniqueItems``, ``minLength``, ``maxLength``,
``pattern``, ``minimum``, ``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``, ``anyOf``, ``oneOf``,
``allOf`` and local ``$ref``. Nothing is fetched and no format is checked. It is not the full standard and does not
pretend to be: ``validate`` returns the errors it can find, each with the path of the value it is about.
"""

from __future__ import annotations

import math
import re
from typing import Any


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value) and value.is_integer())


_TYPES = {
    "string": lambda v: isinstance(v, str),
    "number": _is_number,
    "integer": _is_integer,
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object" if isinstance(value, dict) else type(value).__name__


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#"):
        raise ValueError(f"only local references are supported: {ref}")
    node: Any = root
    for part in [p for p in ref[1:].split("/") if p]:
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Every way the instance breaks the schema, each as ``"<path>: <what is wrong>"``. An empty list means it is valid."""
    errors: list[str] = []
    _check(instance, schema, schema, "$", errors)
    return errors


def _check(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), root, path, errors)
        return
    expected = schema.get("type")
    if expected is not None:
        names = [expected] if isinstance(expected, str) else list(expected)
        if not any(_TYPES.get(name, lambda _v: False)(value) for name in names):
            errors.append(f"{path}: type: expected {' or '.join(names)}, got {_type_name(value)}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: enum: {value!r} is not one of {schema['enum']!r}")
    for key in ("allOf", "anyOf", "oneOf"):
        subs = schema.get(key)
        if not subs:
            continue
        results = []
        for sub in subs:
            sub_errors: list[str] = []
            _check(value, sub, root, path, sub_errors)
            results.append(sub_errors)
        valid = sum(1 for r in results if not r)
        if key == "allOf":
            for r in results:
                errors.extend(r)
        elif key == "anyOf" and valid == 0:
            errors.append(f"{path}: anyOf: matches none of the {len(subs)} alternatives")
        elif key == "oneOf" and valid != 1:
            errors.append(f"{path}: oneOf: matches {valid} of the {len(subs)} alternatives, exactly one is required")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: minLength: {len(value)} characters, at least {schema['minLength']} required")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: maxLength: {len(value)} characters, at most {schema['maxLength']} allowed")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: pattern: {value!r} does not match {schema['pattern']!r}")
    if _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: minimum: {value} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: maximum: {value} is above {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: exclusiveMinimum: {value} is not above {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: exclusiveMaximum: {value} is not below {schema['exclusiveMaximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: minItems: {len(value)} items, at least {schema['minItems']} required")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: maxItems: {len(value)} items, at most {schema['maxItems']} allowed")
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for item in value:
                if item in seen:
                    errors.append(f"{path}: uniqueItems: {item!r} appears more than once")
                    break
                seen.append(item)
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _check(item, items, root, f"{path}[{index}]", errors)
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{path}: required: property {name!r} is missing")
        for name, sub in properties.items():
            if name in value:
                _check(value[name], sub, root, f"{path}.{name}", errors)
        extra = schema.get("additionalProperties")
        if extra is not None:
            for name in value:
                if name in properties:
                    continue
                if extra is False:
                    errors.append(f"{path}.{name}: additionalProperties: property {name!r} is not allowed")
                elif isinstance(extra, dict):
                    _check(value[name], extra, root, f"{path}.{name}", errors)
