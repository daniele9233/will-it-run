"""The verifiers that read structure: JSON, tool calls, enterprise documents, puzzles."""

from __future__ import annotations

import json

import pytest

from agentfit.arena.model import Answer, Question, ToolCall
from agentfit.arena.verifiers import Context, verify


def q(check: dict, **kw) -> Question:
    return Question(id="t.001", category=kw.pop("category", "json"), title="t", lang="it", prompt="p", check=check, expected=kw.pop("expected", ""), reference="", **kw)


def run(check: dict, text: str = "", calls: list[ToolCall] | None = None, finish: str | None = "stop", **kw):
    return verify(q(check, **kw), Answer(text=text, raw=text, tool_calls=calls or [], finish_reason=finish), Context())


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(name=name, arguments=arguments, raw=json.dumps(arguments))


# -- JSON ---------------------------------------------------------------------------------------------

SCHEMA = {
    "type": "object", "required": ["cliente", "totale", "righe"], "additionalProperties": False,
    "properties": {
        "cliente": {"type": "string"}, "totale": {"type": "number"},
        "righe": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["sku", "qty"], "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}}}},
    },
}
EXPECT = {"cliente": "Rossi Srl", "totale": 125.5, "righe": [{"sku": "A-1", "qty": 2}, {"sku": "B-7", "qty": 1}]}
GOOD = {"cliente": "Rossi Srl", "totale": 125.5, "righe": [{"sku": "A-1", "qty": 2}, {"sku": "B-7", "qty": 1}]}
JSON_SPEC = {"type": "json", "schema": SCHEMA, "expect": EXPECT}


def test_a_json_reply_that_fits_the_schema_and_the_values_passes():
    assert run(JSON_SPEC, json.dumps(GOOD)).status == "passed"
    fenced = "```json\n" + json.dumps(GOOD, indent=2) + "\n```"
    assert run(JSON_SPEC, fenced).status == "passed"


def test_text_around_the_json_is_tolerated_unless_the_question_is_strict():
    wrapped = "Ecco il risultato:\n" + json.dumps(GOOD) + "\nSpero sia utile."
    lenient = run(JSON_SPEC, wrapped)
    assert lenient.status == "passed" and lenient.format_ok is False
    strict = run({**JSON_SPEC, "strict": True}, wrapped)
    assert (strict.status, strict.kind) == ("failed", "format")
    assert run({**JSON_SPEC, "strict": True}, "```json\n" + json.dumps(GOOD) + "\n```").status == "passed"


def test_invalid_json_is_a_format_failure_with_the_parser_s_complaint():
    v = run(JSON_SPEC, "{'cliente': 'Rossi Srl', 'totale': 125.5}")
    assert (v.status, v.kind) == ("failed", "format")
    assert v.reason is not None and v.checks and not v.checks[0].ok
    assert run(JSON_SPEC, "Non ho potuto estrarre i dati.").kind == "format"


def test_each_wrong_value_is_a_check_and_the_score_is_the_share_that_was_right():
    wrong = {**GOOD, "totale": 99.0, "righe": [{"sku": "A-1", "qty": 3}, {"sku": "B-7", "qty": 1}]}
    v = run(JSON_SPEC, json.dumps(wrong))
    assert v.status == "partial" and 0.5 < v.score < 1
    failing = [c.reason.render() for c in v.checks if not c.ok]
    assert any("totale" in f and "125.5" in f and "99" in f for f in failing)
    assert any("qty" in f for f in failing)


def test_schema_violations_are_reported_and_cost_a_check():
    extra = {**GOOD, "note": "x"}
    v = run(JSON_SPEC, json.dumps(extra))
    assert v.status == "partial"
    assert any("note" in c.reason.render() and not c.ok for c in v.checks)
    typed = {**GOOD, "totale": "125,5"}
    v = run(JSON_SPEC, json.dumps(typed))
    assert v.status == "partial" and any("totale" in c.reason.render() and not c.ok for c in v.checks)


def test_numbers_compare_with_a_tolerance_and_strings_can_ignore_case():
    spec = {"type": "json", "expect": {"nome": "Mario Rossi", "importo": 10.0}, "fold": True, "tolerance": 0.01}
    assert run(spec, '{"nome": "mario rossi", "importo": 10.004}').status == "passed"
    assert run(spec, '{"nome": "mario rossi", "importo": 10.5}').status == "partial"


def test_unordered_lists_match_by_content():
    spec = {"type": "json", "expect": {"tag": ["a", "b", "c"]}, "unordered": True}
    assert run(spec, '{"tag": ["c", "a", "b"]}').status == "passed"
    assert run(spec, '{"tag": ["c", "a"]}').status != "passed"
    assert run({"type": "json", "expect": {"tag": ["a", "b"]}}, '{"tag": ["b", "a"]}').status != "passed"


def test_a_json_array_can_be_the_whole_answer():
    spec = {"type": "json", "schema": {"type": "array", "items": {"type": "integer"}}, "expect": [1, 2, 3]}
    assert run(spec, "[1, 2, 3]").status == "passed"
    assert run(spec, "[1, 2, 4]").status == "partial"


# -- tool calls --------------------------------------------------------------------------------------------

TOOLS = ({"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}, "unit": {"type": "string"}}}}},
         {"type": "function", "function": {"name": "set_alarm", "parameters": {"type": "object", "properties": {"minutes": {"type": "integer"}}}}})
WEATHER = {"type": "toolcall", "mode": "call", "calls": [{"name": "get_weather", "args": {"city": {"eq": "Milano"}, "unit": {"in": ["celsius", "c"]}}}]}


def test_the_right_function_with_the_right_arguments_passes():
    assert run(WEATHER, calls=[call("get_weather", city="Milano", unit="celsius")], tools=TOOLS).status == "passed"
    assert run(WEATHER, calls=[call("get_weather", city="milano", unit="C")], tools=TOOLS).status == "passed"  # case does not matter in free text


def test_a_wrong_argument_a_missing_one_or_a_wrong_function_fails_and_says_which():
    wrong_value = run(WEATHER, calls=[call("get_weather", city="Roma", unit="celsius")], tools=TOOLS)
    assert (wrong_value.status, wrong_value.kind) == ("failed", "wrong") and "city" in wrong_value.reason.render()
    missing = run(WEATHER, calls=[call("get_weather", unit="celsius")], tools=TOOLS)
    assert missing.status == "failed" and "city" in missing.reason.render()
    other = run(WEATHER, calls=[call("set_alarm", minutes=5)], tools=TOOLS)
    assert other.status == "failed" and "get_weather" in other.reason.render()


def test_a_tool_that_was_not_offered_and_arguments_that_are_not_json_are_called_out():
    ghost = run(WEATHER, calls=[call("fetch_forecast", city="Milano")], tools=TOOLS)
    assert ghost.status == "failed" and "fetch_forecast" in ghost.reason.render()
    broken = run(WEATHER, calls=[ToolCall(name="get_weather", arguments=None, raw='{"city": "Milano"')], tools=TOOLS)
    assert (broken.status, broken.kind) == ("failed", "format")


def test_answering_in_words_when_a_call_was_needed_is_a_format_failure():
    v = run(WEATHER, text="A Milano ci sono 21 gradi.", tools=TOOLS)
    assert (v.status, v.kind) == ("failed", "format")


def test_types_matter_a_number_written_as_text_is_not_a_number():
    spec = {"type": "toolcall", "mode": "call", "calls": [{"name": "set_alarm", "args": {"minutes": {"eq": 5}}}]}
    assert run(spec, calls=[call("set_alarm", minutes=5)], tools=TOOLS).status == "passed"
    assert run(spec, calls=[call("set_alarm", minutes="5")], tools=TOOLS).status == "failed"


def test_several_calls_match_in_any_order_and_an_extra_call_costs_points():
    spec = {"type": "toolcall", "mode": "call", "calls": [{"name": "get_weather", "args": {"city": {"eq": "Milano"}}}, {"name": "get_weather", "args": {"city": {"eq": "Torino"}}}]}
    both = run(spec, calls=[call("get_weather", city="Torino"), call("get_weather", city="Milano")], tools=TOOLS)
    assert both.status == "passed"
    one = run(spec, calls=[call("get_weather", city="Milano")], tools=TOOLS)
    assert one.status == "partial" and one.score == pytest.approx(0.5)
    extra = run(spec, calls=[call("get_weather", city="Milano"), call("get_weather", city="Torino"), call("get_weather", city="Roma")], tools=TOOLS)
    assert extra.status == "partial" and extra.score == pytest.approx(2 / 3, abs=1e-3)


def test_when_no_tool_is_needed_a_call_is_a_mistake_and_the_answer_is_checked():
    spec = {"type": "toolcall", "mode": "no_call", "answer": {"type": "number", "value": 4}}
    assert run(spec, text="Risposta: 4", tools=TOOLS).status == "passed"
    called = run(spec, calls=[call("get_weather", city="Milano")], tools=TOOLS)
    assert called.status == "failed" and "get_weather" in called.reason.render()
    assert run(spec, text="Risposta: 5", tools=TOOLS).status == "failed"


def test_when_a_detail_is_missing_the_model_must_ask_and_not_invent_it():
    spec = {"type": "toolcall", "mode": "ask", "missing": "city"}
    assert run(spec, text="In quale città vuoi sapere il meteo?", tools=TOOLS).status == "passed"
    assert run(spec, text="Which city do you mean?", tools=TOOLS).status == "passed"
    invented = run(spec, calls=[call("get_weather", city="Roma")], tools=TOOLS)
    assert (invented.status, invented.kind) == ("failed", "fabricated") and "city" in invented.reason.render()
    silent = run(spec, text="Il meteo è bello.", tools=TOOLS)
    assert silent.status == "failed"


# -- enterprise documents ---------------------------------------------------------------------------------------

ORDER = """# Ordine d'acquisto ORD-2026-0417

## Fornitore
Rossi Srl, via Roma 1, Milano

## Articoli
| Articolo | Quantità | Prezzo unitario | Totale |
|---|---|---|---|
| Tastiera | 4 | 25,00 | 100,00 |
| Monitor | 2 | 150,00 | 300,00 |

## Riepilogo
Imponibile: 400,00 €
IVA 22%: 88,00 €
Totale ordine: 488,00 €

## Condizioni
Pagamento a 30 giorni.
"""

DOC = {
    "type": "document",
    "sections": ["Fornitore", "Articoli", "Riepilogo", "Condizioni"],
    "facts": ["Rossi Srl", "ORD-2026-0417"],
    "tables": [{"columns": ["Articolo", "Quantità", "Prezzo unitario", "Totale"], "min_rows": 2, "column_values": {"Totale": [100, 300]}}],
    "totals": [{"label": "Imponibile", "value": 400}, {"label": "IVA", "value": 88}, {"label": "Totale ordine", "value": 488}],
    "numbers_allowed": [4, 2, 25, 150, 100, 300, 400, 88, 488, 22, 30, 1, 2026, 417, 0],
    "no_placeholders": True,
}


def test_a_document_with_every_section_fact_table_and_total_passes():
    v = run(DOC, ORDER, category="documents")
    assert v.status == "passed", [c.reason.render() for c in v.checks if not c.ok]


def test_a_missing_section_a_wrong_total_and_an_invented_number_each_cost_a_check():
    broken = ORDER.replace("## Condizioni\nPagamento a 30 giorni.\n", "").replace("488,00", "498,00").replace("Pagamento", "Pagamento")
    v = run(DOC, broken, category="documents")
    assert v.status == "partial"
    failing = " | ".join(c.reason.render() for c in v.checks if not c.ok)
    assert ("Condizioni" in failing and "498" in failing) or "488" in failing
    invented = run(DOC, ORDER + "\nSpedizione prevista in 17 giorni, costo 55,00 €.\n", category="documents")
    assert invented.status == "partial" and any("55" in c.reason.render() and not c.ok for c in invented.checks)


def test_sections_out_of_order_and_placeholders_are_found():
    swapped = ORDER.replace("## Fornitore\nRossi Srl, via Roma 1, Milano\n\n## Articoli", "## Articoli").replace("## Condizioni", "## Fornitore\nRossi Srl\n\n## Condizioni")
    v = run(DOC, swapped, category="documents")
    assert any("order" in c.reason.render().lower() and not c.ok for c in v.checks)
    placeholder = run(DOC, ORDER + "\nReferente: [inserire nome]\n", category="documents")
    assert placeholder.status == "partial" and any("placeholder" in c.reason.render().lower() and not c.ok for c in placeholder.checks)


def test_a_word_limit_is_a_check_too():
    spec = {"type": "document", "sections": [], "words": {"max": 20}}
    assert run(spec, "uno due tre", category="documents").status == "passed"
    assert run(spec, " ".join(["parola"] * 30), category="documents").status == "failed"


# -- puzzles -------------------------------------------------------------------------------------------------------

KNIGHTS = {"type": "puzzle", "kind": "mapping", "solution": {"A": "cavaliere", "B": "furfante", "C": "cavaliere"},
           "values": {"cavaliere": ["cavaliere", "knight", "sincero"], "furfante": ["furfante", "knave", "bugiardo"]}}


def test_a_mapping_is_read_from_the_marked_line_in_any_separator():
    assert run(KNIGHTS, "Risposta: A=cavaliere, B=furfante, C=cavaliere", category="logic").status == "passed"
    assert run(KNIGHTS, "ragiono...\nRisposta: A è cavaliere; B è furfante; C è cavaliere", category="logic").status == "passed"
    assert run(KNIGHTS, "Risposta: A: knight, B: knave, C: knight", category="logic").status == "passed"


def test_a_wrong_or_incomplete_mapping_names_the_mistakes():
    v = run(KNIGHTS, "Risposta: A=cavaliere, B=cavaliere, C=cavaliere", category="logic")
    assert (v.status, v.kind) == ("failed", "wrong") and "B" in v.reason.render()
    incomplete = run(KNIGHTS, "Risposta: A=cavaliere", category="logic")
    assert incomplete.status == "failed" and "B" in incomplete.reason.render()
    assert run(KNIGHTS, "Non saprei.", category="logic").kind == "format"


ORDERING = {"type": "puzzle", "kind": "order", "solution": ["Anna", "Carla", "Bruno"], "names": ["Anna", "Bruno", "Carla"]}


def test_an_ordering_is_the_names_in_the_order_they_are_written():
    assert run(ORDERING, "Risposta: Anna, Carla, Bruno", category="logic").status == "passed"
    assert run(ORDERING, "Risposta: Anna > Carla > Bruno", category="logic").status == "passed"
    v = run(ORDERING, "Risposta: Anna, Bruno, Carla", category="logic")
    assert (v.status, v.kind) == ("failed", "wrong")
    assert run(ORDERING, "Risposta: Anna e Carla", category="logic").status == "failed"
