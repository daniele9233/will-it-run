"""List every string the core passes to ``tr()`` and report the ones without an Italian translation.

    python scripts/list_core_strings.py            # print untranslated keys
    python scripts/list_core_strings.py --all      # print every key
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "backend" / "src" / "agentfit"

# Values translated through a variable, e.g. ``tr(fit.category.value)``.
DYNAMIC = [
    "HIGH", "MEDIUM", "LOW", "UNKNOWN",
    "PERFECT", "EXCELLENT", "GOOD", "ACCEPTABLE", "SLOW", "MEMORY TIGHT", "NOT RECOMMENDED", "IMPOSSIBLE",
]


def source_keys() -> dict[str, str]:
    sys.path.insert(0, str(PACKAGE.parent))
    keys: dict[str, str] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "locales" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            first = node.args[0]
            # ``why`` and ``tl`` are the Arena's: a reason is an English template that is translated when it is read.
            if name in ("tr", "why", "tl") and isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.setdefault(first.value, path.name)
    for key in DYNAMIC:
        keys.setdefault(key, "(dynamic)")
    from agentfit.arena import labels

    for names in (labels.CATEGORY_LABELS, labels.AREA_LABELS, labels.STATUS_LABELS, labels.KIND_LABELS):
        for key in names.values():
            keys.setdefault(key, "(arena labels)")
    return keys


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.path.insert(0, str(PACKAGE.parent))
    from agentfit.locales.it import MESSAGES

    keys = source_keys()
    show_all = "--all" in sys.argv
    missing = [k for k in keys if k not in MESSAGES]
    for key in keys if show_all else missing:
        print(f"[{keys[key]}] {key}")
    print(f"{len(keys)} strings, {len(missing)} untranslated")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
