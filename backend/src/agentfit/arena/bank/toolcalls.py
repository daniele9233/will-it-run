"""Tool calling: the right function, the right arguments, and rightly none at all.

The model is offered a few tools and a request. What is checked is the call it makes (never run: there is nothing behind
the tools), or the lack of one: a question that needs no tool, a request no tool can serve, a detail that is missing and
must be asked for, a tool result that has to be read before answering.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from agentfit.arena.bank.kit import IT_LINE, calls, make, tool
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng

_SYSTEM = "Sei un assistente personale. Hai a disposizione degli strumenti: usali quando servono."
_CITIES = ["Milano", "Torino", "Bologna", "Firenze", "Genova", "Napoli", "Palermo", "Verona", "Padova", "Bari"]
_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]

WEATHER = tool("get_weather", "Get the current weather of a city.", {
    "city": {"type": "string", "description": "Name of the city"},
    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "description": "Unit of the temperature"}}, ["city"])
CONVERT = tool("convert_currency", "Convert an amount of money from one currency to another.", {
    "amount": {"type": "number", "description": "The amount to convert"},
    "from_currency": {"type": "string", "description": "ISO code of the starting currency, for example EUR"},
    "to_currency": {"type": "string", "description": "ISO code of the target currency, for example USD"}}, ["amount", "from_currency", "to_currency"])
TIMER = tool("set_timer", "Start a countdown timer.", {
    "minutes": {"type": "integer", "description": "Length of the timer in minutes"}, "label": {"type": "string", "description": "What the timer is for"}}, ["minutes"])
EMAIL = tool("send_email", "Send an email.", {
    "to": {"type": "string", "description": "Email address of the recipient"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["to", "subject", "body"])
EVENT = tool("create_event", "Add an event to the calendar.", {
    "title": {"type": "string"}, "date": {"type": "string", "description": "Date as YYYY-MM-DD"},
    "time": {"type": "string", "description": "Start time as HH:MM, 24 hours"}, "duration_minutes": {"type": "integer"}}, ["title", "date", "time"])
SEARCH = tool("search_products", "Search the shop catalogue.", {
    "category": {"type": "string", "description": "Product category"}, "max_price": {"type": "number", "description": "Highest price in euro"},
    "in_stock": {"type": "boolean", "description": "Only products that are in stock"}}, ["category"])
ORDER = tool("create_order", "Create an order.", {
    "items": {"type": "array", "description": "The products to order", "items": {"type": "object", "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}}, "required": ["sku", "qty"]}},
    "express": {"type": "boolean", "description": "Express shipping"}}, ["items"])
READ = tool("read_file", "Read a text file.", {"path": {"type": "string"}}, ["path"])
WRITE = tool("write_file", "Write a text file, replacing it.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])
DELETE = tool("delete_file", "Delete a file.", {"path": {"type": "string"}}, ["path"])
LISTING = tool("list_directory", "List the files in a directory.", {"path": {"type": "string"}}, ["path"])
FIND = tool("search_files", "Search for text inside the files of a directory.", {"pattern": {"type": "string"}, "directory": {"type": "string"}}, ["pattern", "directory"])
MOVE = tool("move_file", "Move or rename a file.", {"source": {"type": "string"}, "destination": {"type": "string"}}, ["source", "destination"])

TOOLBOX = (WEATHER, CONVERT, TIMER, EMAIL)
FILES = (READ, WRITE, DELETE, LISTING, FIND, MOVE)


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args}


def _q(n: int, title: str, prompt: str, tools: tuple[dict[str, Any], ...], check: dict[str, Any], expected: str, reference: str, wrong: list[str], *, system: str = _SYSTEM,
       difficulty: int = 2, **fields: Any) -> Question:
    return make("tools", n, title, prompt, check, expected, reference, wrong=wrong, system=system, tools=tools, max_tokens=500, difficulty=difficulty, **fields)


def _shown(name: str, **args: Any) -> str:
    return f"{name}({', '.join(f'{k}={json.dumps(v, ensure_ascii=False)}' for k, v in args.items())})"


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    # 1. One tool, one argument.
    rng = Rng(seed, "tools.001")
    city, other = rng.sample(_CITIES, 2)
    out.append(_q(1, "Una chiamata semplice", f"Che tempo fa a {city} in questo momento?", TOOLBOX, {"type": "toolcall", "calls": [_call("get_weather", {"city": {"eq": city}})]},
                  _shown("get_weather", city=city), calls(("get_weather", {"city": city})),
                  [calls(("get_weather", {"city": other})), f"A {city} adesso ci sono 22 gradi e c'è il sole.", calls(("convert_currency", {"amount": 1, "from_currency": "EUR", "to_currency": "USD"})),
                   calls(("get_weather", {}))], difficulty=1))

    # 2. The right tool among four, with numbers and codes.
    rng = Rng(seed, "tools.002")
    amount = rng.randint(5, 90) * 10
    word, code = rng.choice([("dollari", "USD"), ("sterline", "GBP"), ("franchi svizzeri", "CHF"), ("yen", "JPY")])
    out.append(_q(2, "Scegliere lo strumento giusto", f"Quanti {word} sono {amount} euro?", TOOLBOX,
                  {"type": "toolcall", "calls": [_call("convert_currency", {"amount": {"eq": amount}, "from_currency": {"in": ["EUR"]}, "to_currency": {"in": [code]}})]},
                  _shown("convert_currency", amount=amount, from_currency="EUR", to_currency=code), calls(("convert_currency", {"amount": amount, "from_currency": "EUR", "to_currency": code})),
                  [calls(("convert_currency", {"amount": amount, "from_currency": code, "to_currency": "EUR"})), calls(("convert_currency", {"amount": str(amount * 2), "from_currency": "EUR", "to_currency": code})),
                   calls(("get_weather", {"city": "Roma"})), calls(("convert_currency", {"amount": amount, "from_currency": "EUR"}))], difficulty=1))

    # 3. An argument of the right type.
    rng = Rng(seed, "tools.003")
    minutes = rng.choice([7, 12, 18, 25, 40, 55])
    out.append(_q(3, "Un timer", f"Metti un timer di {minutes} minuti per la pasta.", TOOLBOX, {"type": "toolcall", "calls": [_call("set_timer", {"minutes": {"type": "integer", "eq": minutes}})]},
                  _shown("set_timer", minutes=minutes), calls(("set_timer", {"minutes": minutes, "label": "pasta"})),
                  [calls(("set_timer", {"minutes": str(minutes)})), calls(("set_timer", {"minutes": minutes * 60})), calls(("set_timer", {"label": "pasta"})), f"Va bene, ti avviso tra {minutes} minuti!"], difficulty=1))

    # 4. A question that needs no tool.
    rng = Rng(seed, "tools.004")
    facts = [("della Spagna", "Madrid"), ("del Portogallo", "Lisbona"), ("dell'Austria", "Vienna"), ("della Grecia", "Atene"), ("della Polonia", "Varsavia"), ("della Norvegia", "Oslo")]
    of, capital = rng.choice(facts)
    decoys = [c for _, c in facts if c != capital]
    out.append(_q(4, "Nessuno strumento serve", f"Qual è la capitale {of}?\n{IT_LINE}", TOOLBOX,
                  {"type": "toolcall", "mode": "no_call", "answer": {"type": "alias", "answers": [capital], "decoys": decoys}}, f"{capital}, senza chiamare nessuno strumento", f"Risposta: {capital}",
                  [calls(("get_weather", {"city": capital})), f"Risposta: {decoys[0]}", calls(("convert_currency", {"amount": 1, "from_currency": "EUR", "to_currency": "USD"}), text=f"Risposta: {capital}")], difficulty=1))

    # 5. A calendar event, every argument exact.
    rng = Rng(seed, "tools.005")
    title = rng.choice(["Revisione budget", "Colloquio con Rossi", "Dentista", "Riunione di team", "Call con il fornitore"])
    day = date(2027, rng.randint(1, 12), rng.randint(1, 28))
    at = rng.choice(["09:00", "09:30", "10:15", "14:30", "15:45", "17:00"])
    length = rng.choice([30, 45, 60, 90])
    iso = day.isoformat()
    out.append(_q(5, "Un evento in calendario", f"Fissa in calendario «{title}» il {day.day} {_MONTHS[day.month - 1]} {day.year} alle {at} per {length} minuti.", (EVENT, *TOOLBOX),
                  {"type": "toolcall", "calls": [_call("create_event", {"title": {"eq": title}, "date": {"eq": iso, "exact": True}, "time": {"eq": at, "exact": True}, "duration_minutes": {"type": "integer", "eq": length}})]},
                  _shown("create_event", title=title, date=iso, time=at, duration_minutes=length), calls(("create_event", {"title": title, "date": iso, "time": at, "duration_minutes": length})),
                  [calls(("create_event", {"title": title, "date": f"{day.day:02d}/{day.month:02d}/{day.year}", "time": at, "duration_minutes": length})),
                   calls(("create_event", {"title": title, "date": iso, "time": at.replace(":", "."), "duration_minutes": length})),
                   calls(("create_event", {"title": title, "date": (day + timedelta(days=1)).isoformat(), "time": at, "duration_minutes": length + 15})),
                   calls(("create_event", {"title": "Evento", "date": iso, "time": at}))]))

    # 6. Two independent calls at once.
    rng = Rng(seed, "tools.006")
    first, second, third = rng.sample(_CITIES, 3)
    out.append(_q(6, "Due chiamate insieme", f"Dimmi il meteo di {first} e di {second}.", TOOLBOX,
                  {"type": "toolcall", "calls": [_call("get_weather", {"city": {"eq": first}}), _call("get_weather", {"city": {"eq": second}})]},
                  f"{_shown('get_weather', city=first)}; {_shown('get_weather', city=second)}", calls(("get_weather", {"city": first}), ("get_weather", {"city": second})),
                  [calls(("get_weather", {"city": first})), calls(("get_weather", {"city": first}), ("get_weather", {"city": first})), calls(("get_weather", {"city": first}), ("get_weather", {"city": third})),
                   f"A {first} piove e a {second} c'è il sole."]))

    # 7. A detail it cannot know: it has to ask.
    out.append(_q(7, "Chiedere il destinatario", "Scrivi una mail al mio capo per avvisarlo che domani arriverò con mezz'ora di ritardo.", TOOLBOX, {"type": "toolcall", "mode": "ask", "missing": "recipient"},
                  "Chiedere l'indirizzo email del destinatario, senza inventarlo", "A quale indirizzo email devo inviarla? Non ho quello del tuo capo.",
                  [calls(("send_email", {"to": "capo@azienda.it", "subject": "Ritardo", "body": "Domani arriverò con mezz'ora di ritardo."})),
                   calls(("send_email", {"to": "boss@example.com", "subject": "Ritardo domani", "body": "Buongiorno, domani arriverò con mezz'ora di ritardo."})), "Ho inviato la mail al tuo capo."]))

    # 8. A request that no tool can serve.
    rng = Rng(seed, "tools.008")
    place = rng.choice(["Da Gino", "La Pergola", "Osteria del Borgo", "Trattoria Bianchi"])
    people = rng.randint(2, 6)
    hour = rng.randint(19, 21)
    refuses = (r"(?i)\b(?:non (?:posso|riesco|sono in grado|dispongo|ho (?:a disposizione )?(?:uno|alcuno|nessuno|gli) strument|ho la possibilit|è possibile|e possibile)|purtroppo non|mi dispiace|"
               r"cannot|can't|can not|unable|i don't have|i do not have)")
    claims = (r"(?i)(?:ho prenotato|prenotazione (?:effettuata|confermata|completata)|tavolo (?:prenotato|riservato)|è stato prenotato|e stato prenotato|è tutto fatto|"
              r"booked|reservation (?:is )?confirmed)")
    out.append(_q(8, "Uno strumento che non c'è", f"Prenota un tavolo per {people} persone al ristorante «{place}» stasera alle {hour}.", (WEATHER, CONVERT),
                  {"type": "toolcall", "mode": "no_call", "answer": {"type": "rules", "rules": [{"kind": "regex", "pattern": refuses, "must": True}, {"kind": "regex", "pattern": claims, "must": False}]}},
                  "Dire che non può prenotare, senza chiamare gli strumenti che ha e senza fingere di aver prenotato",
                  "Mi dispiace, non posso prenotare un tavolo: con gli strumenti che ho posso solo controllare il meteo e convertire valute.",
                  [calls(("get_weather", {"city": "Milano"})), f"Ho prenotato un tavolo per {people} persone da {place} per le {hour}.", f"Prenotazione confermata! Vi aspettano da {place} alle {hour}.",
                   calls(("convert_currency", {"amount": people * 10, "from_currency": "EUR", "to_currency": "USD"}), text="Prenoto subito.")], difficulty=3))

    # 9. A tool result to read before answering.
    rng = Rng(seed, "tools.009")
    city = rng.choice(_CITIES)
    temp, wind = rng.randint(4, 33), rng.randint(5, 45)
    condition, stem = rng.choice([("pioggia leggera", r"piogg|piov|rain"), ("sereno", r"seren|\bsole\b|soleggiat|limpid|clear|sunny"), ("nuvoloso", r"nuvol|coperto|cloud"), ("temporale", r"tempora|storm")])
    reading = json.dumps({"city": city, "temp_c": temp, "condition": condition, "wind_kmh": wind}, ensure_ascii=False)
    messages = (
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Che tempo fa a {city}?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": json.dumps({"city": city})}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": reading},
    )
    out.append(_q(9, "Leggere il risultato di uno strumento", f"Che tempo fa a {city}?", TOOLBOX,
                  {"type": "toolcall", "mode": "no_call", "answer": {"type": "rules", "rules": [{"kind": "includes", "words": [str(temp)]}, {"kind": "regex", "pattern": "(?i)" + stem, "must": True}]}},
                  f"Rispondere con i dati ricevuti: {temp} gradi, {condition}", f"A {city} ci sono {temp} gradi, {condition}, con vento a {wind} km/h.",
                  [calls(("get_weather", {"city": city})), "Non ho informazioni sul meteo.", f"A {city} ci sono {temp + 7} gradi e il tempo è molto diverso.", f"A {city} il tempo è {condition} e fa freddissimo, circa {temp - 15} gradi."],
                  messages=messages, difficulty=2))

    # 10. A search with a text, a number and a flag.
    rng = Rng(seed, "tools.010")
    category = rng.choice(["cuffie", "tastiere", "monitor", "webcam"])
    limit = rng.choice([30, 45, 50, 75, 120, 150])
    out.append(_q(10, "Una ricerca con tre filtri", f"Cerca nella categoria «{category}» i prodotti che costano al massimo {limit} euro e che sono disponibili in magazzino.", (SEARCH, *TOOLBOX),
                  {"type": "toolcall", "calls": [_call("search_products", {"category": {"eq": category}, "max_price": {"eq": limit}, "in_stock": {"eq": True}})]},
                  _shown("search_products", category=category, max_price=limit, in_stock=True), calls(("search_products", {"category": category, "max_price": limit, "in_stock": True})),
                  [calls(("search_products", {"category": category, "max_price": str(limit), "in_stock": True})), calls(("search_products", {"category": category, "max_price": limit, "in_stock": "sì"})),
                   calls(("search_products", {"category": category})), calls(("search_products", {"category": "audio", "max_price": limit, "in_stock": True}))]))

    # 11. The right one among six file tools.
    rng = Rng(seed, "tools.011")
    folder = rng.choice(["/var/log/nginx", "/etc/ssh", "/home/deploy/releases", "/srv/app/uploads"])
    out.append(_q(11, "Uno strumento tra sei", f"Mostrami quali file ci sono nella cartella {folder}.", FILES,
                  {"type": "toolcall", "calls": [_call("list_directory", {"path": {"eq": folder, "exact": True}})]}, _shown("list_directory", path=folder), calls(("list_directory", {"path": folder})),
                  [calls(("read_file", {"path": folder})), calls(("list_directory", {"path": folder + "/"})), calls(("search_files", {"pattern": "*", "directory": folder})), calls(("delete_file", {"path": folder}))],
                  system="Sei un assistente che lavora su un server Linux. Hai a disposizione degli strumenti per i file."))

    # 12. Only the first step: it has to read before it can answer.
    rng = Rng(seed, "tools.012")
    path = rng.choice(["config/app.yaml", "deploy/settings.toml", "conf/workers.ini", "etc/service.json"])
    out.append(_q(12, "Prima si legge il file", f"Apri il file {path} e dimmi quanti worker sono configurati.", FILES, {"type": "toolcall", "calls": [_call("read_file", {"path": {"eq": path, "exact": True}})]},
                  _shown("read_file", path=path), calls(("read_file", {"path": path})),
                  [f"Nel file {path} sono configurati 4 worker.", calls(("list_directory", {"path": path.split('/')[0]})), calls(("read_file", {"path": path}), ("write_file", {"path": path, "content": "workers: 4"})),
                   calls(("read_file", {"path": path.split('/')[-1]}))], system="Sei un assistente che lavora su un server Linux. Hai a disposizione degli strumenti per i file.", difficulty=3))

    # 13. An array of objects and a flag.
    rng = Rng(seed, "tools.013")
    sku_a, sku_b = rng.sample(["A-100", "B-250", "C-310", "D-420", "E-515"], 2)
    qty_a, qty_b = rng.sample(list(range(1, 10)), 2)
    items = [{"sku": sku_a, "qty": qty_a}, {"sku": sku_b, "qty": qty_b}]
    out.append(_q(13, "Un ordine con più righe", f"Ordina {qty_a} pezzi dello SKU {sku_a} e {qty_b} dello SKU {sku_b}, con spedizione express.", (ORDER, *TOOLBOX),
                  {"type": "toolcall", "calls": [_call("create_order", {"items": {"eq": items}, "express": {"eq": True}})]}, _shown("create_order", items=items, express=True),
                  calls(("create_order", {"items": items, "express": True})),
                  [calls(("create_order", {"items": [{**i, "qty": str(i["qty"])} for i in items], "express": True})), calls(("create_order", {"items": items, "express": False})),
                   calls(("create_order", {"items": [{"sku": sku_a, "qty": qty_b}, {"sku": sku_b, "qty": qty_a}], "express": True})), calls(("create_order", {"items": [items[0]], "express": True}))], difficulty=3))

    # 14. «Dopodomani»: the date is worked out from the one in the system prompt.
    rng = Rng(seed, "tools.014")
    today = date(2027, rng.randint(1, 11), rng.randint(1, 28))
    words, offset = rng.choice([("dopodomani", 2), ("tra tre giorni", 3), ("tra una settimana", 7)])
    due = (today + timedelta(days=offset)).isoformat()
    system = f"Sei un assistente personale. Oggi è {_DAYS[today.weekday()]} {today.day} {_MONTHS[today.month - 1]} {today.year}. Hai a disposizione degli strumenti: usali quando servono."
    out.append(_q(14, "Una data relativa", f"Fissa «Dentista» {words} alle 9.", (EVENT, *TOOLBOX),
                  {"type": "toolcall", "calls": [_call("create_event", {"title": {"eq": "Dentista"}, "date": {"eq": due, "exact": True}, "time": {"eq": "09:00", "exact": True}})]},
                  _shown("create_event", title="Dentista", date=due, time="09:00"), calls(("create_event", {"title": "Dentista", "date": due, "time": "09:00"})),
                  [calls(("create_event", {"title": "Dentista", "date": (today + timedelta(days=offset + 1)).isoformat(), "time": "09:00"})), calls(("create_event", {"title": "Dentista", "date": today.isoformat(), "time": "09:00"})),
                   calls(("create_event", {"title": "Dentista", "date": due, "time": "9"})), calls(("create_event", {"title": "Dentista", "date": f"{words}", "time": "09:00"}))], system=system, difficulty=3))
    return out
