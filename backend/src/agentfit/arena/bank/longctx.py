"""Long context: a fact buried in a long text. The text is generated from the seed, so it was never seen by any model, and it
holds no digit apart from the facts that are asked for.

Two needles in the packs of the standard size, four in the complete one. The longest text is about 14 000 characters, a few
thousand tokens: what is measured is whether a model finds a sentence in the middle of a text, not how big a window it has.
"""

from __future__ import annotations

from agentfit.arena.bank.kit import IT_LINE, IT_NUM, make, wrong_numbers
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng

_NOUNS = [("il mercato", "m"), ("il porto", "m"), ("il parco", "m"), ("il ponte", "m"), ("il museo", "m"), ("il teatro", "m"), ("il castello", "m"),
          ("la piazza", "f"), ("la biblioteca", "f"), ("la stazione", "f"), ("la chiesa", "f"), ("la fontana", "f"), ("la torre", "f"), ("la scuola", "f")]
_ADJ = {"m": ["antico", "affollato", "silenzioso", "luminoso", "tranquillo", "famoso", "piccolo", "moderno"],
        "f": ["antica", "affollata", "silenziosa", "luminosa", "tranquilla", "famosa", "piccola", "moderna"]}
_WHEN = ["la mattina presto", "nei giorni di festa", "durante l'estate", "dopo il tramonto", "in inverno", "nel fine settimana", "all'ora di pranzo"]
_TOWNS = ["Aldora", "Brenzio", "Calvena", "Dormia", "Elvezzo", "Fornaro", "Gavira", "Montesella"]


def _sentence(rng: Rng) -> str:
    noun, gender = rng.choice(_NOUNS)
    adjective, town, when = rng.choice(_ADJ[gender]), rng.choice(_TOWNS), rng.choice(_WHEN)
    kind = rng.below(4)
    if kind == 0:
        return f"{noun.capitalize()} di {town} è {adjective} {when}."
    if kind == 1:
        return f"Chi visita {town} {when} trova che {noun} sia {adjective}."
    if kind == 2:
        return f"Secondo i residenti, {noun} è il luogo più {rng.choice(_ADJ['m'])} di {town}."
    return f"Ogni anno {when}, {noun} di {town} accoglie visitatori da molte regioni."


def _haystack(rng: Rng, chars: int, needles: list[tuple[float, str]]) -> str:
    """Paragraphs of filler until ``chars``, with each needle put into a paragraph at its depth (0 = start, 1 = end)."""
    paragraphs: list[list[str]] = []
    total = 0
    while total < chars:
        sentences = [_sentence(rng) for _ in range(rng.randint(5, 8))]
        paragraphs.append(sentences)
        total += sum(len(s) + 1 for s in sentences) + 1
    for depth, needle in needles:
        index = min(len(paragraphs) - 1, int(depth * len(paragraphs)))
        paragraphs[index].insert(rng.randint(1, len(paragraphs[index]) - 1), needle)
    return "\n\n".join(" ".join(p) for p in paragraphs)


def _q(n: int, title: str, prompt: str, check: dict, expected: str, reference: str, wrong: list[str], difficulty: int) -> Question:
    return make("longctx", n, title, prompt, check, expected, reference, wrong=wrong, max_tokens=500, difficulty=difficulty, tags=("long",))


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    # 1. A number in the middle of a medium text.
    rng = Rng(seed, "longctx.001")
    code = rng.randint(1000, 9999)
    text = _haystack(rng, 6000, [(0.5, f"Il codice dell'armadietto della palestra è {code}.")])
    out.append(_q(1, "Il codice nel mezzo", f"Leggi il testo seguente e rispondi alla domanda finale.\n\n{text}\n\nDomanda: qual è il codice dell'armadietto della palestra?\n{IT_NUM}",
                  {"type": "number", "value": code, "integer": True}, str(code), f"Risposta: {code}", wrong_numbers(code, code + 1, code - 10, int(str(code)[::-1]) if int(str(code)[::-1]) != code else code + 100), 2))

    # 2. A name in a long text.
    rng = Rng(seed, "longctx.002")
    owner = rng.choice(["Marta", "Giorgio", "Ilaria", "Tommaso", "Beatrice", "Ettore"])
    cat = rng.sample(["Briciola", "Pistacchio", "Nuvola", "Ginger", "Birba", "Macchia", "Zafferano"], 3)  # the first is the right one
    text = _haystack(rng, 14000, [(0.3, f"Il gatto di {owner} si chiama {cat[0]}.")])
    out.append(_q(2, "Il gatto in un testo lungo", f"Leggi il testo seguente e rispondi alla domanda finale.\n\n{text}\n\nDomanda: come si chiama il gatto di {owner}?\n{IT_LINE}",
                  {"type": "alias", "answers": [cat[0]], "decoys": cat[1:]}, cat[0], f"Risposta: {cat[0]}", [f"Risposta: {cat[1]}", f"Risposta: {cat[2]}", "Risposta: non è scritto nel testo"], 2))

    # 3. Two facts, far apart, to put together.
    rng = Rng(seed, "longctx.003")
    price, people = rng.randint(6, 19), rng.randint(7, 23)
    text = _haystack(rng, 10000, [(0.2, f"Un biglietto per il museo costa {price} euro."), (0.75, f"Alla gita di classe partecipano {people} persone.")])
    total = price * people
    out.append(_q(3, "Due fatti lontani da mettere insieme", f"Leggi il testo seguente e rispondi alla domanda finale.\n\n{text}\n\n"
                  f"Domanda: quanto si spende in totale per i biglietti del museo di tutte le persone che partecipano alla gita di classe?\n{IT_NUM}", {"type": "number", "value": total, "integer": True}, str(total),
                  f"Risposta: {total}", wrong_numbers(total, price + people, price * (people + 1), total + price), 3))

    # 4. One among five of the same kind.
    rng = Rng(seed, "longctx.004")
    colours = rng.sample(["rosso", "verde", "blu", "giallo", "grigio", "arancione"], 5)
    codes = rng.sample(list(range(1000, 9999)), 5)
    needles = [(0.1 + 0.18 * i, f"L'armadietto {c} ha il codice {k}.") for i, (c, k) in enumerate(zip(colours, codes, strict=True))]
    wanted = rng.below(5)
    text = _haystack(rng, 14000, needles)
    out.append(_q(4, "Uno tra cinque simili", f"Leggi il testo seguente e rispondi alla domanda finale.\n\n{text}\n\nDomanda: qual è il codice dell'armadietto {colours[wanted]}?\n{IT_NUM}",
                  {"type": "number", "value": codes[wanted], "integer": True}, str(codes[wanted]), f"Risposta: {codes[wanted]}",
                  wrong_numbers(codes[wanted], *[codes[i] for i in range(5) if i != wanted][:3]), 3))
    return out
