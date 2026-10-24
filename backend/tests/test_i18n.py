"""Localization of text the core produces, and the accuracy reference table."""

from __future__ import annotations

import ast
import re
import string
from pathlib import Path

import pytest

from agentfit import i18n
from agentfit.fit import accuracy
from agentfit.fit.accuracy import accuracy_for
from agentfit.locales.it import MESSAGES

PACKAGE = Path(i18n.__file__).parent


def _keys_in_source() -> set[str]:
    keys: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        if "locales" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Name) and node.func.id in ("tr", "why", "tl"):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
    return keys


def _fields(text: str) -> list[str]:
    """Placeholders with their format specs, e.g. ``['gb:.1f', 'drive:']``."""
    return sorted(f"{name}:{spec}" for _, name, spec, _ in string.Formatter().parse(text) if name)


def test_every_core_string_has_an_italian_translation():
    missing = sorted(_keys_in_source() - set(MESSAGES))
    assert not missing, f"untranslated: {missing}"


def test_the_names_of_the_arena_are_in_the_catalog():
    """Categories, areas, verdicts and kinds of failure reach ``tr`` as variables when a report is written."""
    from agentfit.arena import labels

    shown = [text for names in (labels.CATEGORY_LABELS, labels.AREA_LABELS, labels.STATUS_LABELS, labels.KIND_LABELS) for text in names.values()]
    missing = sorted(set(shown) - set(MESSAGES))
    assert not missing, f"untranslated: {missing}"


def test_text_translated_through_a_variable_is_in_the_catalog():
    """Template names and check descriptions reach ``tr`` as variables, so the scan above cannot see them."""
    from agentfit.teams import templates
    from agentfit.workflows import checks

    shown = [text for factory in templates.BUILTIN.values() for text in (factory().workflow.name, factory().workflow.description)]
    shown += [text for spec in checks.CHECKS.values() for text in (spec.label, spec.description)]
    missing = sorted(set(shown) - set(MESSAGES))
    assert not missing, f"untranslated: {missing}"


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_translations_keep_placeholders_and_format_specs(key):
    assert _fields(key) == _fields(MESSAGES[key])


def test_request_language_overrides_the_default():
    i18n.set_default_language("en")
    assert i18n.tr("Fits completely on your GPU.") == "Fits completely on your GPU."
    token = i18n.set_request_language("it")
    try:
        assert i18n.tr("Fits completely on your GPU.") == "Entra completamente nella tua GPU."
        assert i18n.tr("Leaves {gb:.1f} GB of VRAM headroom for Windows and other apps.", gb=1.64).startswith("Lascia 1.6 GB")
    finally:
        i18n.reset_request_language(token)
    assert i18n.current_language() == "en"


def test_unknown_language_and_unknown_text_fall_back_to_english():
    assert i18n.set_request_language("zz") is None
    assert i18n.resolve("en") == "en" and i18n.resolve("it-IT") == "it"
    assert i18n.resolve("system") in i18n.SUPPORTED
    token = i18n.set_request_language("it")
    try:
        assert i18n.tr("A string nobody translated {n}", n=3) == "A string nobody translated 3"
    finally:
        i18n.reset_request_language(token)


# -- accuracy by quantization -----------------------------------------------------


def test_reference_figures_match_the_llama_cpp_table():
    # tools/quantize/quantize.cpp, QUANT_OPTIONS: "+0.1754 ppl @ Llama-3-8B" for Q4_K_M, and so on.
    published = {"Q8_0": 0.0026, "Q6_K": 0.0217, "Q5_K_M": 0.0569, "Q4_K_M": 0.1754, "Q3_K_M": 0.6569, "Q2_K": 3.5199}
    for quant, delta in published.items():
        result = accuracy_for(quant)
        assert result.basis == "measured" and result.ppl_delta == delta


def test_accuracy_never_improves_as_files_get_smaller():
    levels = [row.level for row in accuracy.reference_table()]
    assert levels == sorted(levels, reverse=True)
    assert levels[0] == 5 and levels[-1] == 0


@pytest.mark.parametrize(
    ("label", "basis", "reference"),
    [
        ("UD-Q4_K_XL", "nearest", "Q4_K_M"),
        ("Q3_K_XL", "nearest", "Q3_K_L"),
        ("Q6_K_L", "nearest", "Q6_K"),
        ("IQ4_XS", "bits", "Q4_K_S"),
        ("IQ3_S", "bits", "Q3_K_S"),
        ("BF16", "original", None),
        ("not-a-quant", "unknown", None),
    ],
)
def test_unmeasured_quantizations_are_marked_not_invented(label, basis, reference):
    result = accuracy_for(label)
    assert result.basis == basis and result.reference == reference
    if basis == "bits":
        # No published perplexity: none is shown.
        assert result.ppl_delta is None
    if basis == "unknown":
        assert result.label == "Unknown" and result.level == 0


def test_bits_per_weight_come_from_the_published_file_sizes():
    # 4.58 GiB for an 8.03B-parameter model is 4.9 bits per weight.
    assert accuracy_for("Q4_K_M").bits_per_weight == pytest.approx(4.9, abs=0.01)
    assert re.fullmatch(r"\+0\.\d{4} perplexity over the original, measured by llama\.cpp on Llama-3-8B\.", accuracy_for("Q5_K_M").note)


def test_score_penalty_follows_measured_loss_below_four_bits():
    from agentfit.fit import quant as q

    # Four bits and up keep the original gentle penalties.
    assert [q.quality_penalty(x) for x in ("Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q4_0")] == [0.0, -1.0, -2.0, -5.0, -5.0]
    # Below that, the penalty tracks how much quality was actually measured to be lost.
    assert q.quality_penalty("Q3_K_M") == -9.0  # noticeable loss
    assert q.quality_penalty("Q3_K_S") == q.quality_penalty("IQ3_XXS") == -16.0  # high loss
    assert q.quality_penalty("Q2_K") == q.quality_penalty("IQ2_XS") == -30.0  # severe loss
    assert q.quality_penalty("Q3_K_XL") == -9.0 and q.quality_penalty("UD-Q2_K_XL") == -30.0

