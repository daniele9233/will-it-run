"""Reading comprehension: answer from a text that is full of numbers and names that look alike, or say the text does not
contain the answer. The passages are written from the seed, so the right answer is never the same twice."""

from __future__ import annotations

from agentfit.arena.bank.kit import IT_LINE, make
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng

_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì"]
_MEN = ["Luca", "Marco", "Giorgio", "Paolo", "Davide", "Andrea", "Stefano", "Matteo", "Franco", "Alberto"]
_WOMEN = ["Marta", "Carla", "Giulia", "Elena", "Sara", "Chiara", "Paola", "Laura", "Anna", "Silvia"]
_CLIENTS = ["Verdi Srl", "Bianchi SpA", "Neri & Figli", "Gallo Costruzioni", "Ferrari Impianti", "Colombo Logistica", "Greco Alimentari", "Ricci Software"]
_ONLY_TEXT = "Rispondi solo in base al testo."
_ABSTAIN_LINE = "Se il testo non contiene l'informazione, dillo chiaramente e non inventare nulla."


def _euro(value: float) -> str:
    whole, cents = divmod(round(value * 100), 100)
    return f"{whole:,}".replace(",", ".") + f",{cents:02d}"


def _alias(answers: list[str], decoys: list[str] | None = None) -> dict:
    return {"type": "alias", "answers": answers, "decoys": decoys or []}


def _number(value: float) -> dict:
    return {"type": "number", "value": value}


def build(seed: int) -> list[Question]:
    r = "reading"
    out: list[Question] = []

    # 1. Which day is the meeting with the client? Four events on four different days.
    rng = Rng(seed, f"{r}.001")
    days = rng.sample(_DAYS, 4)
    client = rng.choice(_CLIENTS)
    hours = [f"{rng.randint(9, 17)}:{rng.choice(['00', '30'])}" for _ in range(4)]
    events = ["riunione di reparto", f"incontro con il cliente {client}", "revisione del budget", "formazione sulla sicurezza"]
    schedule = " ".join(f"{d.capitalize()} alle {h}: {e}." for d, h, e in zip(days, hours, events, strict=True))
    day = days[1]
    out.append(make(r, 1, "Giorno dell'incontro con il cliente", f"Calendario della settimana. {schedule}\n\n{_ONLY_TEXT} In quale giorno è fissato l'incontro con il cliente {client}?\n{IT_LINE}",
                    _alias([day], [d for d in days if d != day]), day, f"Risposta: {day}", wrong=[f"Risposta: {days[0]}", f"Risposta: {days[2]}", f"Risposta: {day} e {days[3]}"], max_tokens=400, difficulty=1))

    # 2. Two invoices, two amounts, two due dates: which amount?
    rng = Rng(seed, f"{r}.002")
    n1, n2 = rng.randint(100, 480), rng.randint(500, 990)
    a1, a2 = rng.randint(800, 999) + rng.choice([0, 0.5, 0.25, 0.75]), rng.randint(1000, 4800) + rng.choice([0, 0.5, 0.25, 0.75])
    d1, d2 = rng.randint(1, 28), rng.randint(1, 28)
    text = (f"Fattura n. {n1} del {d1} marzo: importo € {_euro(a1)}, da pagare entro 30 giorni. "
            f"Fattura n. {n2} del {d2} aprile: importo € {_euro(a2)}, da pagare entro 60 giorni.")
    out.append(make(r, 2, "Importo di una fattura", f"{text}\n\n{_ONLY_TEXT} Qual è l'importo della fattura n. {n2}, in euro?\n{IT_LINE}", _number(a2), _euro(a2),
                    f"Risposta: {_euro(a2)}", wrong=[f"Risposta: {_euro(a1)}", f"Risposta: {n2}", f"Risposta: {_euro(a1 + a2)}"], max_tokens=400, difficulty=1))

    # 3. The text does not give the office's phone number.
    rng = Rng(seed, f"{r}.003")
    who, place = rng.choice(_WOMEN), rng.choice(["Torino", "Bologna", "Verona", "Padova", "Bari", "Genova"])
    opens = rng.randint(7, 9)
    out.append(make(r, 3, "Telefono non nel testo", f"L'ufficio di {place} è aperto dal lunedì al venerdì, dalle {opens}:30 alle 17:00. Per ogni pratica va contattata {who}, responsabile dell'accoglienza, "
                    f"che risponde via e-mail alle richieste entro due giorni lavorativi.\n\n{_ONLY_TEXT} {_ABSTAIN_LINE}\nQual è il numero di telefono dell'ufficio?",
                    {"type": "abstain", "mode": "must_abstain", "forbid": [r"\+?\d[\d\s./-]{6,}\d"]}, "Il testo non riporta nessun numero di telefono.",
                    "Il testo non riporta il numero di telefono dell'ufficio: indica solo gli orari e un contatto via e-mail.",
                    wrong=["Il numero di telefono dell'ufficio è 011 555 0142.", "Non lo so con certezza, ma credo sia 02 5550 1234.", "Chiama lo 06 4455 6677 durante l'orario di apertura."],
                    max_tokens=400, difficulty=2))

    # 4. A return policy with an exception: the sale window.
    rng = Rng(seed, f"{r}.004")
    normal, sale = rng.choice([30, 45, 60]), rng.choice([7, 10, 14])
    out.append(make(r, 4, "Reso per i prodotti in saldo", f"Condizioni di reso. Un prodotto può essere restituito entro {normal} giorni dalla consegna, con la confezione integra. "
                    f"Per i prodotti acquistati in saldo il termine è ridotto a {sale} giorni. I prodotti personalizzati non possono essere restituiti.\n\n{_ONLY_TEXT} "
                    f"Entro quanti giorni si può restituire un prodotto acquistato in saldo?\n{IT_LINE}", _number(sale), str(sale), f"Risposta: {sale}",
                    wrong=[f"Risposta: {normal}", f"Risposta: {normal + sale}", "Risposta: 0"], max_tokens=400, difficulty=2))

    # 5. A recipe for four, scaled.
    rng = Rng(seed, f"{r}.005")
    flour, people = rng.choice([200, 240, 320, 400]), rng.choice([6, 10, 12])
    eggs, milk = rng.choice([2, 3, 4]), rng.choice([250, 300, 500])
    result = flour * people // 4
    out.append(make(r, 5, "Ricetta per più persone", f"Ricetta base per 4 persone: {flour} g di farina, {eggs} uova, {milk} ml di latte, un pizzico di sale.\n\n"
                    f"Quanti grammi di farina servono per {people} persone?\n{IT_LINE}", _number(result), str(result), f"Risposta: {result}",
                    wrong=[f"Risposta: {flour}", f"Risposta: {flour + people}", f"Risposta: {eggs * people}"], max_tokens=500, difficulty=2))

    # 6. Ages that depend on each other.
    rng = Rng(seed, f"{r}.006")
    a, b = rng.choice(_WOMEN), rng.choice(_MEN)
    c = rng.choice([m for m in _MEN if m != b])
    base, plus, minus = rng.randint(24, 38), rng.randint(3, 9), rng.randint(2, 7)
    age_c = base + plus - minus
    out.append(make(r, 6, "Età a catena", f"{b} ha {base} anni. {a} ha {plus} anni più di {b}. {c} è più giovane di {a} di {minus} anni.\n\n"
                    f"Quanti anni ha {c}?\n{IT_LINE}", _number(age_c), str(age_c), f"Risposta: {age_c}",
                    wrong=[f"Risposta: {base + plus}", f"Risposta: {base - minus}", f"Risposta: {age_c + minus}"], max_tokens=500, difficulty=2))

    # 7. A company's timeline.
    rng = Rng(seed, f"{r}.007")
    founded = rng.randint(1975, 1990)
    ipo = founded + rng.randint(8, 14)
    bought = ipo + rng.randint(3, 7)
    ceo = bought + rng.randint(2, 6)
    firm = rng.choice(["Alfa Motori", "Beta Tessile", "Gamma Energia", "Delta Farmaci"])
    out.append(make(r, 7, "Anno dell'acquisizione", f"La storia di {firm}. Nel {founded} l'azienda viene fondata a Brescia. Nel {ipo} si quota in borsa. "
                    f"Nel {bought} acquisisce un concorrente tedesco. Nel {ceo} un nuovo amministratore delegato succede al fondatore.\n\n"
                    f"{_ONLY_TEXT} In che anno {firm} ha acquisito il concorrente tedesco?\n{IT_LINE}", _number(bought), str(bought), f"Risposta: {bought}",
                    wrong=[f"Risposta: {ipo}", f"Risposta: {ceo}", f"Risposta: {founded}"], max_tokens=400, difficulty=2))

    # 8. A table of models: the RAM of one of them.
    rng = Rng(seed, f"{r}.008")
    rams = rng.sample([4, 8, 12, 16, 32], 3)
    rams.sort()
    disks = rng.sample([64, 128, 256, 512, 1024], 3)
    disks.sort()
    prices = sorted(rng.sample(range(199, 999, 10), 3))
    rows = "\n".join(f"- {m}: {ram} GB di RAM, {disk} GB di archiviazione, {price} €" for m, ram, disk, price in zip(["Lite", "Standard", "Pro"], rams, disks, prices, strict=True))
    out.append(make(r, 8, "RAM del modello Pro", f"Listino dei portatili:\n{rows}\n\n{_ONLY_TEXT} Quanta memoria RAM, in GB, ha il modello Pro?\n{IT_LINE}", _number(rams[2]), str(rams[2]),
                    f"Risposta: {rams[2]}", wrong=[f"Risposta: {disks[2]}", f"Risposta: {rams[1]}", f"Risposta: {prices[2]}"], max_tokens=400, difficulty=1))

    # 9. The same table, asked about a model that is not in it.
    out.append(make(r, 9, "Un modello che non c'è", f"Listino dei portatili:\n{rows}\n\n{_ONLY_TEXT} {_ABSTAIN_LINE}\nQuanta memoria RAM, in GB, ha il modello Max?",
                    {"type": "abstain", "mode": "must_abstain", "forbid": [r"(?i)\bmax\b[^.\n]{0,40}\b\d+\s*GB", r"(?i)\b(?:ha|dispone di|con|offre)\s+\d+\s*GB[^.\n]{0,30}\bmax\b"]},
                    "Il modello Max non è nel listino: la risposta è che il testo non lo dice.", "Il modello Max non compare nel listino: il testo riporta solo Lite, Standard e Pro, quindi non posso dirlo.",
                    wrong=["Il modello Max ha 64 GB di RAM.", f"Non lo so con precisione, ma il Max ha {rams[2] * 2} GB di RAM.", "Il Max dispone di 24 GB di RAM."], max_tokens=400, difficulty=2))

    # 10. An email thread that changes its mind twice.
    rng = Rng(seed, f"{r}.010")
    first, second, third, fourth = rng.sample(_MEN + _WOMEN, 4)
    out.append(make(r, 10, "Chi spedisce il report", f"Scambio di e-mail.\nLunedì, {first}: «Il report trimestrale lo prepara {second}.»\nMartedì, {second}: «Sono in ferie: lo passo a {third}.»\n"
                    f"Mercoledì, {third}: «Va bene, lo preparo io, ma lo spedirà {fourth} al cliente.»\n\n{_ONLY_TEXT} Chi spedirà il report al cliente?\n{IT_LINE}",
                    _alias([fourth], [first, second, third]), fourth, f"Risposta: {fourth}", wrong=[f"Risposta: {second}", f"Risposta: {third}", f"Risposta: {first}"], max_tokens=400, difficulty=2))
    return out
