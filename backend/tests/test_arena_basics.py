"""The Arena's building blocks: the portable random generator, text and number parsing, JSON Schema."""

from __future__ import annotations

import pytest

from agentfit.arena import jsonschema, text
from agentfit.arena.rng import Rng

# -- the random generator -----------------------------------------------------


def test_splitmix64_matches_the_published_vector():
    # The reference output of splitmix64 started from state 0 (Vigna's own test vector).
    rng = Rng.from_state(0)
    assert [rng.next_u64() for _ in range(3)] == [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F]


def test_same_seed_and_key_give_the_same_numbers_and_other_keys_do_not():
    a = [Rng(42, "math.001").randint(0, 10**9) for _ in range(1)]
    b = [Rng(42, "math.001").randint(0, 10**9) for _ in range(1)]
    c = [Rng(42, "math.002").randint(0, 10**9) for _ in range(1)]
    d = [Rng(43, "math.001").randint(0, 10**9) for _ in range(1)]
    assert a == b and a != c and a != d


def test_randint_is_inclusive_and_roughly_uniform():
    rng = Rng(7)
    values = [rng.randint(3, 6) for _ in range(4000)]
    assert set(values) == {3, 4, 5, 6}
    assert all(800 < values.count(v) < 1200 for v in (3, 4, 5, 6))


def test_shuffle_and_sample_are_permutations_and_reproducible():
    items = list(range(20))
    first = list(items)
    Rng(1, "x").shuffle(first)
    second = list(items)
    Rng(1, "x").shuffle(second)
    assert first == second and sorted(first) == items and first != items
    picked = Rng(1, "y").sample(items, 5)
    assert len(set(picked)) == 5 and set(picked) <= set(items)


def test_random_is_in_the_unit_interval_and_choice_picks_a_member():
    rng = Rng("seed", "k")
    assert all(0.0 <= rng.random() < 1.0 for _ in range(200))
    assert rng.choice(["a", "b", "c"]) in {"a", "b", "c"}
    with pytest.raises(IndexError):
        rng.choice([])


# -- think tags, answers, numbers ----------------------------------------------


def test_strip_think_removes_the_reasoning_a_model_writes_inline():
    assert text.strip_think("<think>let me see</think>Risposta: 4") == ("Risposta: 4", "let me see")
    # A template that only prints the closing tag.
    assert text.strip_think("hidden thoughts</think>\n\nCanberra") == ("Canberra", "hidden thoughts")
    # A thought that never ends: nothing is left to judge.
    assert text.strip_think("<think>still thinking") == ("", "still thinking")
    assert text.strip_think("plain") == ("plain", "")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234", [1234.0]),
        ("1.234,56", [1234.56]),
        ("1,234.56", [1234.56]),
        ("274,5", [274.5]),
        ("274.5", [274.5]),
        ("-3,5", [-3.5]),
        ("−3.5", [-3.5]),  # noqa: RUF001
        ("1 234 567", [1234567.0]),
        ("1.234.567", [1234567.0]),
        ("1'250", [1250.0]),
        ("1e3", [1000.0]),
    ],
)
def test_number_candidates_read_italian_and_english_formats(raw, expected):
    assert text.number_candidates(raw)[: len(expected)] == expected


def test_a_lone_separator_with_three_digits_stays_ambiguous():
    # "1.234" is 1234 in Italian and 1.234 in English: both are offered, never guessed.
    assert set(text.number_candidates("1.234")) == {1234.0, 1.234}
    assert set(text.number_candidates("1,234")) == {1234.0, 1.234}


def test_find_numbers_returns_every_number_with_its_position():
    hits = text.find_numbers("Costa 12,50 € per 3 pezzi, cioè 37,5 in totale")
    assert [h.raw for h in hits] == ["12,50", "3", "37,5"]
    assert text.find_numbers("nessun numero qui") == []
    assert [h.raw for h in text.find_numbers("sconto del 15% su 240")] == ["15", "240"]


def test_final_answer_takes_the_last_marked_line_then_boxed_then_nothing():
    assert text.final_answer("ragiono...\nRisposta: 42\n") == ("42", "marker")
    assert text.final_answer("**Risposta finale:** 7,5 euro") == ("7,5 euro", "marker")
    assert text.final_answer("Final answer = Paris\nAnswer: Rome") == ("Rome", "marker")
    assert text.final_answer("quindi \\boxed{12}") == ("12", "boxed")
    assert text.final_answer("niente di marcato") == (None, "none")


def test_fold_removes_case_accents_punctuation_and_articles():
    assert text.fold("L'Àquila, è  la CITTÀ!") == "l aquila e la citta"
    assert text.fold_answer("L'Aquila") == "aquila"
    assert text.fold_answer("The Eiffel Tower.") == "eiffel tower"
    assert text.fold_answer("Il Colosseo") == "colosseo"


def test_contains_phrase_respects_word_boundaries():
    assert text.contains_phrase("la capitale e canberra", "canberra")
    assert not text.contains_phrase("le canberrate", "canberra")
    assert text.contains_phrase("sydney non e la risposta", "sydney")


def test_code_blocks_keep_their_language_and_skip_prose():
    reply = "Ecco:\n```python\ndef f(x):\n    return x\n```\ne poi\n```\nplain\n```\n"
    blocks = text.code_blocks(reply)
    assert [(b.lang, b.code.strip()) for b in blocks] == [("python", "def f(x):\n    return x"), ("", "plain")]
    assert text.code_blocks("niente codice") == []


def test_word_and_sentence_counters():
    assert text.count_words("Uno, due; tre-quattro  cinque.") == 4
    assert text.count_words("") == 0
    assert text.count_sentences("Ciao. Come stai? Bene!") == 3
    assert text.count_sentences("Dr. Rossi arriva. Presto.") == 2


# -- JSON Schema ----------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "required": ["name", "total", "items"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "total": {"type": "number", "minimum": 0},
        "currency": {"enum": ["EUR", "USD"]},
        "items": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["sku"], "properties": {"sku": {"type": "string", "pattern": "^[A-Z]{3}-\\d+$"}, "qty": {"type": "integer"}}}},
    },
}


def test_a_valid_document_has_no_errors():
    doc = {"name": "Rossi", "total": 12.5, "currency": "EUR", "items": [{"sku": "ABC-1", "qty": 2}]}
    assert jsonschema.validate(doc, SCHEMA) == []


def test_errors_name_the_path_and_the_rule_that_failed():
    doc = {"name": "", "total": "12", "currency": "GBP", "extra": 1, "items": [{"sku": "abc", "qty": 1.5}]}
    errors = jsonschema.validate(doc, SCHEMA)
    joined = "\n".join(errors)
    assert "$.name" in joined and "$.total" in joined and "$.currency" in joined
    assert "$.extra" in joined and "$.items[0].sku" in joined and "$.items[0].qty" in joined


def test_missing_required_fields_and_empty_arrays_are_reported():
    errors = jsonschema.validate({"name": "x"}, SCHEMA)
    assert any("total" in e and "required" in e for e in errors)
    assert any("items" in e for e in errors)
    assert any("minItems" in e for e in jsonschema.validate({"name": "x", "total": 1, "items": []}, SCHEMA))


def test_booleans_are_not_numbers_and_integers_may_be_written_as_floats():
    assert jsonschema.validate(True, {"type": "integer"}) != []
    assert jsonschema.validate(3.0, {"type": "integer"}) == []
    assert jsonschema.validate(3.5, {"type": "integer"}) != []
    assert jsonschema.validate(None, {"type": ["string", "null"]}) == []


def test_any_of_one_of_const_and_refs():
    assert jsonschema.validate(5, {"anyOf": [{"type": "string"}, {"type": "integer"}]}) == []
    assert jsonschema.validate(5.5, {"anyOf": [{"type": "string"}, {"type": "integer"}]}) != []
    assert jsonschema.validate(1, {"oneOf": [{"type": "integer"}, {"minimum": 0}]}) != []
    assert jsonschema.validate("a", {"const": "a"}) == []
    schema = {"$defs": {"id": {"type": "integer", "minimum": 1}}, "type": "object", "properties": {"a": {"$ref": "#/$defs/id"}}}
    assert jsonschema.validate({"a": 0}, schema) != []
    assert jsonschema.validate({"a": 2}, schema) == []


def test_unique_items_and_additional_properties_with_a_schema():
    assert jsonschema.validate([1, 2, 2], {"uniqueItems": True}) != []
    schema = {"type": "object", "additionalProperties": {"type": "integer"}}
    assert jsonschema.validate({"a": 1, "b": "x"}, schema) != []
