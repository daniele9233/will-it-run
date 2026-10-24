"""Structured JSON: extracting fields with the right types, keeping to a schema, normalising dates, escaping, merging.

An answer is valid JSON, fits a JSON Schema, and holds the right values. Some questions say «only JSON»: any text around
the document is then a failure of format. One question asks for the answer through ``response_format``, the way an
application that needs structured output does.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from agentfit.arena.bank.kit import make
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng

_ONLY = "Rispondi esclusivamente con il JSON, senza altro testo."
_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
_FIRST = ["Giulia", "Marco", "Elena", "Paolo", "Sara", "Luca", "Chiara", "Davide"]
_LAST = ["Ferri", "Conti", "Serra", "Vitale", "Monti", "Greco", "Longo", "Bernardi"]
_CITIES = ["Perugia", "Trento", "Lecce", "Bergamo", "Ancona", "Cagliari", "Pisa", "Udine"]
_ITEMS = ["Webcam HD", "Hub USB-C", "Sedia ergonomica", "Tastiera meccanica", "Lampada da tavolo", "Cuffie con microfono"]


def _it(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _q(n: int, title: str, prompt: str, expect: Any, schema: dict | None, *, wrong: list[str], strict: bool = True, fold: bool = False, unordered: bool = False,
       tolerance: float = 1e-6, response_format: dict | None = None, difficulty: int = 2) -> Question:
    check: dict[str, Any] = {"type": "json", "expect": expect, "strict": strict, "fold": fold, "unordered": unordered, "tolerance": tolerance}
    if schema is not None:
        check["schema"] = schema
    shown = json.dumps(expect, ensure_ascii=False)
    return make("json", n, title, prompt, check, shown if len(shown) < 300 else shown[:297] + "…", _dump(expect), wrong=wrong, response_format=response_format, max_tokens=900, difficulty=difficulty)


def _merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        return {**{k: _merge(a[k], b[k]) if k in b else a[k] for k in a}, **{k: b[k] for k in b if k not in a}}
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return b


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    # 1. A person, from a sentence.
    rng = Rng(seed, "json.001")
    first, last, city, age = rng.choice(_FIRST), rng.choice(_LAST), rng.choice(_CITIES), rng.randint(19, 64)
    person = {"nome": first, "cognome": last, "eta": age, "citta": city}
    schema = {"type": "object", "required": list(person), "additionalProperties": False,
              "properties": {"nome": {"type": "string"}, "cognome": {"type": "string"}, "eta": {"type": "integer"}, "citta": {"type": "string"}}}
    out.append(_q(1, "Persona da una frase", f"Frase: «Mi chiamo {first} {last}, ho {age} anni e vivo a {city}.»\nEstrai i dati in un oggetto JSON con i campi `nome`, `cognome`, `eta` (numero intero) e `citta`. {_ONLY}",
                  person, schema, wrong=[_dump({**person, "eta": str(age)}), _dump({**person, "extra": 1}), "Ecco il JSON:\n" + _dump(person), _dump({"nome": f"{first} {last}", "eta": age, "citta": city}), "{'nome': 'x'}"], difficulty=1))

    # 2. The lines of an invoice, with their types.
    rng = Rng(seed, "json.002")
    chosen = rng.sample(_ITEMS, 3)
    lines = [{"articolo": name, "quantita": rng.randint(1, 6), "prezzo_unitario": rng.randint(10, 400) + rng.choice([0.0, 0.5, 0.9])} for name in chosen]
    sentence = "; ".join(f"{ln['quantita']} x {ln['articolo']} a {_it(ln['prezzo_unitario'])} € ciascuno" for ln in lines)
    schema = {"type": "array", "minItems": 3, "items": {"type": "object", "required": ["articolo", "quantita", "prezzo_unitario"], "additionalProperties": False,
                                                          "properties": {"articolo": {"type": "string"}, "quantita": {"type": "integer"}, "prezzo_unitario": {"type": "number"}}}}
    out.append(_q(2, "Righe di fattura", f"Dalla fattura: «{sentence}.»\nEstrai le righe in un array JSON di oggetti con `articolo` (stringa), `quantita` (intero) e `prezzo_unitario` (numero, con il punto decimale). {_ONLY}", lines, schema,
                  wrong=[_dump([{**ln, "prezzo_unitario": _it(ln["prezzo_unitario"])} for ln in lines]), _dump(lines[:2]), _dump([{**ln, "quantita": str(ln["quantita"])} for ln in lines]), "```json\n" + _dump(lines) + "\n```\nQueste sono le righe."],
                  difficulty=1))

    # 3. Types: a boolean, an integer, a decimal, a missing value.
    rng = Rng(seed, "json.003")
    orders, discount = rng.randint(3, 40), rng.randint(2, 25) / 2
    flag = rng.choice([True, False])
    record = {"attivo": flag, "ordini": orders, "sconto": discount, "note": None}
    out.append(_q(3, "Tipi giusti", f"Scheda cliente: «attivo: {'sì' if flag else 'no'}; ordini: {orders}; sconto: {_it(discount).replace(',00', '').replace(',50', ',5')}%; note: nessuna».\nConvertila in un oggetto JSON con i campi `attivo` (booleano), `ordini` (intero), "
                  f"`sconto` (numero, senza il simbolo %) e `note` (`null` perché non ci sono). {_ONLY}", record, {"type": "object", "required": list(record), "additionalProperties": False,
                                                                                                                      "properties": {"attivo": {"type": "boolean"}, "ordini": {"type": "integer"}, "sconto": {"type": "number"}, "note": {"type": "null"}}},
                  wrong=[_dump({**record, "attivo": "sì" if flag else "no"}), _dump({**record, "sconto": f"{discount}%"}), _dump({**record, "note": "nessuna"}), _dump({**record, "ordini": float(orders) + 0.5})], difficulty=1))

    # 4. Dates in words, as ISO dates.
    rng = Rng(seed, "json.004")
    dates: list[date] = []
    while len(dates) < 3:
        # Three different dates: with a repeat the same list backwards would be the right answer.
        candidate = date(rng.randint(2025, 2027), rng.randint(1, 12), rng.randint(1, 28))
        if candidate not in dates:
            dates.append(candidate)
    words = ", ".join(f"{d.day} {_MONTHS[d.month - 1]} {d.year}" for d in dates)
    expect = {"date": [d.isoformat() for d in dates]}
    out.append(_q(4, "Date in formato ISO", f"Date: {words}.\nScrivile come stringhe ISO `aaaa-mm-gg` in un oggetto JSON con un solo campo `date` che contiene l'array, nello stesso ordine. {_ONLY}", expect,
                  {"type": "object", "required": ["date"], "additionalProperties": False, "properties": {"date": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}}}},
                  wrong=[_dump({"date": [f"{d.day}/{d.month}/{d.year}" for d in dates]}), _dump({"date": list(reversed(expect["date"]))}), _dump({"date": [d.isoformat() for d in dates][:2]})]))

    # 5. Classification with a closed set of labels.
    messages = [(1, "Non riesco ad accedere al portale, mi dà l'errore 403.", "supporto"), (2, "Vorrei un preventivo per venti licenze.", "commerciale"),
                (3, "La fattura di marzo riporta un importo sbagliato.", "fattura"), (4, "Avete uno stand alla fiera di Verona?", "altro")]
    expect = [{"id": i, "categoria": c} for i, _, c in messages]
    schema = {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "object", "required": ["id", "categoria"], "additionalProperties": False,
                                                                       "properties": {"id": {"type": "integer"}, "categoria": {"enum": ["fattura", "supporto", "commerciale", "altro"]}}}}
    out.append(_q(5, "Classificare messaggi", "Classifica ogni messaggio in una sola categoria tra: fattura, supporto, commerciale, altro.\n" + "\n".join(f"{i}. {t}" for i, t, _ in messages)
                  + f"\nRispondi con un array JSON di oggetti `{{\"id\": numero, \"categoria\": etichetta}}`. {_ONLY}", expect, schema,
                  wrong=[_dump([{**e, "categoria": "assistenza" if e["categoria"] == "supporto" else e["categoria"]} for e in expect]), _dump([{**expect[0], "categoria": "altro"}, *expect[1:]]), _dump(expect[:3])]))

    # 6. A nested structure from a paragraph.
    rng = Rng(seed, "json.006")
    dept, boss = rng.choice(["Vendite", "Logistica", "Assistenza", "Amministrazione"]), f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
    members = []
    names = rng.sample([f"{first} {last}" for first in _FIRST for last in _LAST if f"{first} {last}" != boss], 3)
    for name, role in zip(names, ["agente", "agente senior", "stagista"], strict=True):
        members.append({"nome": name, "ruolo": role})
    expect = {"reparto": dept, "responsabile": boss, "team": members}
    text = f"Il reparto {dept} è guidato da {boss}. Nel team lavorano " + f"{members[0]['nome']} ({members[0]['ruolo']}), {members[1]['nome']} ({members[1]['ruolo']}) e {members[2]['nome']} ({members[2]['ruolo']})."
    out.append(_q(6, "Struttura annidata", f"Testo: «{text}»\nScrivi un oggetto JSON con `reparto`, `responsabile` e `team`: un array di oggetti con `nome` e `ruolo`, nello stesso ordine del testo. {_ONLY}", expect,
                  {"type": "object", "required": ["reparto", "responsabile", "team"], "properties": {"reparto": {"type": "string"}, "responsabile": {"type": "string"}, "team": {"type": "array", "minItems": 3, "items": {"type": "object", "required": ["nome", "ruolo"], "properties": {"nome": {"type": "string"}, "ruolo": {"type": "string"}}}}}},
                  wrong=[_dump({**expect, "team": [m["nome"] for m in members]}), _dump({**expect, "team": members[:2]}), _dump({"reparto": dept, "team": members}), _dump({**expect, "team": [{**members[0], "ruolo": "stagista"}, *members[1:]]})]))

    # 7. Only JSON, nothing else.
    expect = ["gennaio", "giugno"]
    out.append(_q(7, "Solo JSON, niente altro", "Quali sono i mesi dell'anno il cui nome comincia per «g»? Rispondi **solo** con un array JSON di stringhe minuscole, senza spiegazioni e senza altro testo.", expect,
                  {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}, unordered=True,
                  wrong=["I mesi che iniziano per «g» sono: " + _dump(expect), _dump(["gennaio", "giugno", "gio"]), _dump(["gennaio"]), "gennaio, giugno"], difficulty=1))

    # 8. The same, through response_format: the schema is given to the runtime as well.
    product = {"modello": "Zenith 10", "prezzo": 349, "ram_gb": 6, "colori": ["nero", "argento"]}
    product_schema = {"type": "object", "required": list(product), "additionalProperties": False,
                      "properties": {"modello": {"type": "string"}, "prezzo": {"type": "number"}, "ram_gb": {"type": "integer"}, "colori": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}
    out.append(_q(8, "Output strutturato con schema", "«Il tablet Zenith 10 costa 349 euro, ha 6 GB di RAM ed è disponibile in nero e argento.»\nEstrai `modello`, `prezzo` (numero), `ram_gb` (intero) e `colori` (array di stringhe minuscole) in un oggetto JSON.",
                  product, product_schema, unordered=True, response_format={"type": "json_schema", "json_schema": {"name": "product", "strict": True, "schema": product_schema}},
                  wrong=[_dump({**product, "prezzo": "349 euro"}), _dump({**product, "colori": "nero e argento"}), _dump({"modello": "Zenith", "prezzo": 349, "ram_gb": 6, "colori": ["nero", "argento"]})], difficulty=2))

    # 9. Escaping: quotes and a line break.
    quote = 'Disse: "ciao"\nPoi se ne andò.'
    out.append(_q(9, "Virgolette e a capo", "Scrivi un oggetto JSON con un solo campo `citazione` che contiene esattamente questo testo, su due righe, con le virgolette comprese:\nDisse: \"ciao\"\nPoi se ne andò.\n" + _ONLY,
                  {"citazione": quote}, {"type": "object", "required": ["citazione"], "additionalProperties": False, "properties": {"citazione": {"type": "string"}}}, strict=True,
                  wrong=['{"citazione": "Disse: "ciao"\nPoi se ne andò."}', _dump({"citazione": "Disse: ciao Poi se ne andò."}), _dump({"citazione": 'Disse: "ciao" Poi se ne andò.'})], difficulty=3))

    # 10. Numbers in the Italian format, and a missing one.
    rng = Rng(seed, "json.010")
    a, b, c = rng.randint(1000, 9999) + rng.choice([0.5, 0.25, 0.75]), -rng.randint(5, 60), rng.randint(1, 99) / 100
    shown = f"{a:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    expect = [a, b, None, c]
    out.append(_q(10, "Numeri all'italiana", f"Importi scritti all'italiana: «{shown}; {b}; n/d; {_it(c)}».\nConvertili in un array JSON di numeri (con il punto decimale, senza separatori di migliaia); l'importo «n/d» non è disponibile e diventa `null`. {_ONLY}", expect,
                  {"type": "array", "minItems": 4, "maxItems": 4}, wrong=[_dump([shown, b, None, _it(c)]), _dump([a, b, 0, c]), _dump([a * 1000, b, None, c]), _dump([a, b, c])], difficulty=3))

    # 11. A merge with a rule.
    rng = Rng(seed, "json.011")
    cpu, ram_a, ram_b, disk = rng.randint(1, 4), rng.randint(2, 8), rng.randint(9, 32), rng.randint(5, 50) * 10
    first_tag, second_tag = rng.sample(["rosso", "blu", "verde", "giallo", "grigio", "viola"], 2)
    obj_a = {"nome": "Atlas", "tag": ["alfa", first_tag], "limiti": {"cpu": cpu, "ram": ram_a}, "attivo": False}
    obj_b = {"tag": [second_tag], "limiti": {"ram": ram_b, "disco": disk}, "attivo": True}
    merged = _merge(obj_a, obj_b)
    out.append(_q(11, "Unire due oggetti", f"Unisci questi due oggetti JSON, A e B. Gli oggetti si uniscono ricorsivamente; per le chiavi in conflitto vince B; gli array si concatenano (prima gli elementi di A, poi quelli di B).\nA = {json.dumps(obj_a, ensure_ascii=False)}\n"
                  f"B = {json.dumps(obj_b, ensure_ascii=False)}\n{_ONLY}", merged, {"type": "object", "required": ["nome", "tag", "limiti", "attivo"]},
                  wrong=[_dump({**merged, "tag": obj_b["tag"]}), _dump({**merged, "limiti": obj_b["limiti"]}), _dump({**merged, "attivo": False}), _dump({**merged, "tag": list(reversed(merged["tag"]))})], difficulty=3))

    # 12. Filter and sort.
    rng = Rng(seed, "json.012")
    products = [{"nome": name, "prezzo": rng.randint(5, 90) + rng.choice([0.0, 0.5, 0.9])} for name in rng.sample([*_ITEMS, "Cavo HDMI", "Mouse ottico"], 6)]
    limit = sorted(p["prezzo"] for p in products)[3] + 0.05
    expect = [p["nome"] for p in sorted((p for p in products if p["prezzo"] < limit), key=lambda p: p["prezzo"])]
    out.append(_q(12, "Filtrare e ordinare", f"Prodotti: {json.dumps(products, ensure_ascii=False)}\nRestituisci un array JSON con i nomi dei prodotti il cui prezzo è inferiore a {_it(limit)}, ordinati dal più economico al più caro. {_ONLY}", expect,
                  {"type": "array", "items": {"type": "string"}, "minItems": len(expect), "maxItems": len(expect)},
                  wrong=[_dump(list(reversed(expect))), _dump([p["nome"] for p in products]), _dump(expect[:-1]), _dump([p for p in products if p["prezzo"] < limit])], difficulty=3))
    return out
