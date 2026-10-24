"""Logic: puzzles generated from the seed and solved by a program, so the expected answer is computed and, for the two kinds
that have several readings (who is a knight, who is taller), the puzzle is kept only if it has exactly one solution.

A model's answer is judged by what it says, not by how it got there; it has to be the one that satisfies every clue.
"""

from __future__ import annotations

import heapq
import itertools
import math

from agentfit.arena.bank.kit import IT_NUM, make, wrong_numbers
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng

_DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_NAMES = ["Anna", "Bruno", "Carla", "Dario", "Elisa", "Fabio", "Giada", "Hugo"]
_FEMALE = {"Anna", "Carla", "Elisa", "Giada"}
_KNIGHT = ["cavaliere", "knight", "sincero", "veritiero"]
_KNAVE = ["furfante", "knave", "bugiardo", "mentitore"]


def _number(value: float) -> dict:
    return {"type": "number", "value": float(value)}


def _agree(name: str, adjective: str) -> str:
    """«alto» or «alta», by the gender of the name."""
    return adjective[:-1] + ("a" if name in _FEMALE else "o")


# -- knights and knaves -----------------------------------------------------------------------------


def _pair(speaker: str, x: str, y: str) -> tuple[str, str]:
    """«Io e B» and «siamo», or «A e B» and «sono»."""
    if speaker in (x, y):
        return f"Io e {y if x == speaker else x}", "siamo"
    return f"{x} e {y}", "sono"


def _statement_text(kind: str, speaker: str, x: str, y: str) -> str:
    if kind in ("knight", "knave"):
        what = "un cavaliere" if kind == "knight" else "un furfante"
        return f"«{x} è {what}»" if x != speaker else f"«Io sono {what}»"
    if kind == "any_knave":
        a, b = ("me" if p == speaker else p for p in (x, y))
        return f"«Almeno uno tra {a} e {b} è un furfante»"
    subject, verb = _pair(speaker, x, y)
    return f"«{subject} {verb} {'dello stesso tipo' if kind == 'same' else 'di tipo diverso'}»"


def _holds(kind: str, x: str, y: str, knight: dict[str, bool]) -> bool:
    if kind == "knight":
        return knight[x]
    if kind == "knave":
        return not knight[x]
    if kind == "any_knave":
        return not knight[x] or not knight[y]
    if kind == "same":
        return knight[x] == knight[y]
    return knight[x] != knight[y]


def knights(seed: int, key: str) -> tuple[str, dict[str, str]]:
    """Three islanders each make a statement. Returns the puzzle text and the only assignment that fits."""
    rng = Rng(seed, key)
    people = ["A", "B", "C"]
    kinds = ["knight", "knave", "any_knave", "same", "diff"]
    for _ in range(2000):
        statements = []
        for speaker in people:
            kind = rng.choice(kinds)
            if kind in ("knight", "knave"):
                x, y = rng.choice(people), ""
            else:
                x, y = rng.sample(people, 2)
            statements.append((speaker, kind, x, y))
        fits = []
        for combo in itertools.product([True, False], repeat=3):
            assignment = dict(zip(people, combo, strict=True))
            if all(_holds(kind, x, y, assignment) == assignment[speaker] for speaker, kind, x, y in statements):
                fits.append(assignment)
        if len(fits) == 1:
            solution = {p: "cavaliere" if fits[0][p] else "furfante" for p in people}
            lines = "\n".join(f"{speaker} dice: {_statement_text(kind, speaker, x, y)}." for speaker, kind, x, y in statements)
            return lines, solution
    raise RuntimeError("no puzzle with a single solution was found")


# -- who is taller ------------------------------------------------------------------------------------


def ordering(seed: int, key: str, count: int) -> tuple[str, list[str], list[str]]:
    """Clues about heights that leave exactly one order. Returns (the clues, the names, tallest to shortest)."""
    rng = Rng(seed, key)
    names = rng.sample(_NAMES, count)
    order = list(names)
    rng.shuffle(order)  # tallest first
    pairs = [(order[i], order[j]) for i in range(count) for j in range(i + 1, count)]
    rng.shuffle(pairs)
    clues: list[tuple[str, str]] = []

    def unique() -> bool:
        fits = 0
        for perm in itertools.permutations(names):
            position = {n: i for i, n in enumerate(perm)}
            if all(position[a] < position[b] for a, b in clues):
                fits += 1
                if fits > 1:
                    return False
        return fits == 1

    for pair in pairs:
        clues.append(pair)
        if unique():
            break
    rng.shuffle(clues)
    lines = []
    for a, b in clues:
        lines.append(f"- {a} è più {_agree(a, 'alto')} di {b}." if rng.random() < 0.5 else f"- {b} è più {_agree(b, 'basso')} di {a}.")
    return "\n".join(lines), names, order


# -- the bridge and the torch --------------------------------------------------------------------------


def bridge_time(times: list[int]) -> int:
    """The least time for everyone to cross a bridge two at a time with one torch, each pair going at its slower walker's pace."""
    everyone = frozenset(range(len(times)))
    best: dict[tuple[frozenset[int], bool], int] = {(everyone, True): 0}
    heap: list[tuple[int, int, tuple[int, ...], bool]] = [(0, 0, tuple(sorted(everyone)), True)]
    counter = 1
    while heap:
        cost, _tie, near_list, torch_near = heapq.heappop(heap)
        near = frozenset(near_list)
        if not near:
            return cost
        if best.get((near, torch_near), math.inf) < cost:
            continue
        if torch_near:
            groups = [frozenset(pair) for pair in itertools.combinations(sorted(near), 2)] or [near]
            moves = [(near - group, False, cost + max(times[i] for i in group)) for group in groups]
        else:
            moves = [(near | {person}, True, cost + times[person]) for person in everyone - near]
        for state_near, state_torch, spent in moves:
            if spent < best.get((state_near, state_torch), math.inf):
                best[(state_near, state_torch)] = spent
                heapq.heappush(heap, (spent, counter, tuple(sorted(state_near)), state_torch))
                counter += 1
    raise RuntimeError("nobody crossed")


def build(seed: int) -> list[Question]:
    g = "logic"
    out: list[Question] = []

    # 1. Four people, a few clues about their height: put them in order.
    text, names, order = ordering(seed, f"{g}.001", 4)
    out.append(make(g, 1, "In ordine di altezza", f"Quattro amici ({', '.join(names)}) sono di altezze diverse. Si sa che:\n{text}\n\nDisponili dal più alto al più basso. "
                    "Scrivi l'ultima riga nel formato «Risposta: nome, nome, nome, nome».", {"type": "puzzle", "kind": "order", "solution": order, "names": names}, " > ".join(order),
                    f"Risposta: {', '.join(order)}", wrong=[f"Risposta: {', '.join(reversed(order))}", f"Risposta: {', '.join(order[1:] + order[:1])}", f"Risposta: {', '.join(order[:2])}"],
                    max_tokens=900, difficulty=1))

    # 2. The next term of a sequence.
    rng = Rng(seed, f"{g}.002")
    kind = rng.choice(["arithmetic", "geometric", "squares", "alternating", "fibonacci"])
    if kind == "arithmetic":
        a0, d = rng.randint(2, 30), rng.randint(3, 13)
        terms = [a0 + d * i for i in range(7)]
    elif kind == "geometric":
        a0, r = rng.randint(1, 5), rng.choice([2, 3])
        terms = [a0 * r**i for i in range(7)]
    elif kind == "squares":
        k = rng.randint(1, 9)
        terms = [i * i + k for i in range(1, 8)]
    elif kind == "alternating":
        a0, p, q = rng.randint(1, 20), rng.randint(2, 9), rng.randint(2, 9)
        terms = [a0]
        for i in range(6):
            terms.append(terms[-1] + (p if i % 2 == 0 else q))
    else:
        x, y = rng.randint(1, 5), rng.randint(2, 7)
        terms = [x, y]
        for _ in range(5):
            terms.append(terms[-1] + terms[-2])
    shown_terms, nxt = terms[:6], terms[6]
    out.append(make(g, 2, "Il termine successivo", f"Qual è il numero che continua la sequenza? {', '.join(map(str, shown_terms))}, …\n{IT_NUM}", _number(nxt), str(nxt), f"Risposta: {nxt}",
                    wrong=wrong_numbers(nxt, nxt + 1, 2 * shown_terms[-1] - shown_terms[-2], shown_terms[-1]), max_tokens=700, difficulty=1))

    # 3. The day of the week, some days from now.
    rng = Rng(seed, f"{g}.003")
    today, n = rng.randint(0, 6), rng.randint(40, 400)
    target = _DAYS[(today + n) % 7]
    out.append(make(g, 3, "Che giorno sarà", f"Oggi è {_DAYS[today]}. Che giorno della settimana sarà tra {n} giorni?\nScrivi l'ultima riga nel formato «Risposta: <giorno>».",
                    {"type": "alias", "answers": [target], "decoys": [d for d in _DAYS if d != target]}, target, f"Risposta: {target}",
                    wrong=[f"Risposta: {_DAYS[(today + n + 1) % 7]}", f"Risposta: {_DAYS[(today + n - 1) % 7]}", f"Risposta: {_DAYS[(today + n + 3) % 7]}"], max_tokens=700, difficulty=2))

    # 4. Knights and knaves.
    lines, solution = knights(seed, f"{g}.004")
    flip = {"cavaliere": "furfante", "furfante": "cavaliere"}
    reference = ", ".join(f"{p}={v}" for p, v in solution.items())
    out.append(make(g, 4, "Cavalieri e furfanti", f"Su un'isola ogni abitante è un cavaliere (dice sempre la verità) oppure un furfante (mente sempre). Tre abitanti, A, B e C, dicono:\n{lines}\n\n"
                    "Chi è cavaliere e chi è furfante? Scrivi l'ultima riga nel formato «Risposta: A=cavaliere, B=furfante, C=…».",
                    {"type": "puzzle", "kind": "mapping", "solution": solution, "values": {"cavaliere": _KNIGHT, "furfante": _KNAVE}}, reference, f"Risposta: {reference}",
                    wrong=["Risposta: " + ", ".join(f"{p}={flip[v]}" for p, v in solution.items()), "Risposta: " + ", ".join(f"{p}={flip[v] if p == 'A' else v}" for p, v in solution.items()),
                           "Risposta: A=cavaliere"], max_tokens=1200, difficulty=3))

    # 5. A syllogism on words that mean nothing: does the conclusion follow?
    rng = Rng(seed, f"{g}.005")
    syllables = ["bo", "ri", "ta", "ku", "mi", "lo", "fe", "sa", "ne", "du", "va", "zo"]
    words: list[str] = []
    while len(words) < 3:
        word = "".join(rng.choice(syllables) for _ in range(rng.choice([2, 3])))
        if word not in words:
            words.append(word)
    s_, m_, p_ = words
    forms = [
        (True, f"Tutti i {m_} sono {p_}.\nTutti i {s_} sono {m_}.", f"Tutti i {s_} sono {p_}."),
        (True, f"Nessun {m_} è {p_}.\nTutti i {s_} sono {m_}.", f"Nessun {s_} è {p_}."),
        (True, f"Tutti i {m_} sono {p_}.\nAlcuni {s_} sono {m_}.", f"Alcuni {s_} sono {p_}."),
        (False, f"Tutti i {p_} sono {m_}.\nTutti i {s_} sono {m_}.", f"Tutti i {s_} sono {p_}."),
        (False, f"Alcuni {m_} sono {p_}.\nAlcuni {s_} sono {m_}.", f"Alcuni {s_} sono {p_}."),
        (False, f"Tutti i {m_} sono {p_}.\nAlcuni {s_} non sono {m_}.", f"Alcuni {s_} non sono {p_}."),
    ]
    valid, premises, conclusion = forms[rng.below(len(forms))]
    options = {"valido": ["valido", "valida", "valid", "corretto", "segue"],
               "non valido": ["non valido", "non valida", "invalido", "invalid", "not valid", "non corretto", "non segue", "errato"]}
    correct = "valido" if valid else "non valido"
    out.append(make(g, 5, "Sillogismo", f"Premesse:\n{premises}\nConclusione: {conclusion}\n\nLa conclusione segue logicamente dalle premesse? (Le parole non hanno significato: conta solo la forma.) "
                    "Scrivi l'ultima riga «Risposta: valido» oppure «Risposta: non valido».", {"type": "choice", "options": options, "correct": correct}, correct, f"Risposta: {correct}",
                    wrong=["Risposta: non valido" if valid else "Risposta: valido", "Risposta: dipende"], max_tokens=900, difficulty=2))

    # 6. Students who play neither sport.
    rng = Rng(seed, f"{g}.006")
    total = rng.randint(26, 40)
    football, basket = rng.randint(10, total - 8), rng.randint(8, total - 10)
    both = rng.randint(2, min(football, basket) - 1)
    while football + basket - both > total - 2:
        both += 1
    neither = total - (football + basket - both)
    out.append(make(g, 6, "Né calcio né basket", f"In una classe di {total} studenti, {football} giocano a calcio, {basket} giocano a basket e {both} giocano a entrambi. Quanti non giocano a nessuno dei due sport?\n{IT_NUM}",
                    _number(neither), str(neither), f"Risposta: {neither}", wrong=wrong_numbers(neither, total - football - basket, total - football - basket + 2 * both, neither + both),
                    max_tokens=700, difficulty=2))

    # 7. A Boolean expression with three values.
    rng = Rng(seed, f"{g}.007")
    pv, qv, rv = rng.random() < 0.5, rng.random() < 0.5, rng.random() < 0.5
    expressions = [
        ("(P e non Q) oppure R", lambda p, q, r: (p and not q) or r), ("non (P oppure Q) e R", lambda p, q, r: (not (p or q)) and r),
        ("(P oppure Q) e non R", lambda p, q, r: (p or q) and not r), ("P e (Q oppure non R)", lambda p, q, r: p and (q or not r)),
        ("non P oppure (Q e R)", lambda p, q, r: (not p) or (q and r)),
    ]
    text_expr, fn = expressions[rng.below(len(expressions))]
    result = fn(pv, qv, rv)
    word = {True: "vero", False: "falso"}
    out.append(make(g, 7, "Espressione booleana", f"P è {word[pv]}, Q è {word[qv]}, R è {word[rv]}. L'espressione «{text_expr}» è vera o falsa?\nScrivi l'ultima riga «Risposta: vero» oppure «Risposta: falso».",
                    {"type": "choice", "options": {"vero": ["vero", "vera", "true"], "falso": ["falso", "falsa", "false"]}, "correct": word[result]}, word[result], f"Risposta: {word[result]}",
                    wrong=[f"Risposta: {word[not result]}", "Risposta: non si può dire"], max_tokens=700, difficulty=2))

    # 8. The torch on the bridge.
    rng = Rng(seed, f"{g}.008")
    times = sorted(rng.sample(range(1, 13), 4))
    best = bridge_time(times)
    out.append(make(g, 8, "Il ponte e la torcia", f"Quattro persone devono attraversare un ponte di notte, con una sola torcia. Il ponte regge al massimo due persone alla volta, che vanno insieme alla velocità della più lenta; "
                    f"la torcia deve essere riportata indietro a mano. Le persone impiegano {times[0]}, {times[1]}, {times[2]} e {times[3]} minuti. Qual è il tempo minimo, in minuti, perché attraversino tutte?\n{IT_NUM}",
                    _number(best), str(best), f"Risposta: {best}", wrong=wrong_numbers(best, sum(times), best + 1, sum(times[1:]) + 2 * times[0], best - 1), max_tokens=1500, difficulty=3))

    # 9. The fewest weighings to find a heavier coin.
    rng = Rng(seed, f"{g}.009")
    coins = rng.choice([9, 12, 20, 27, 28, 50, 81, 82])
    weighings = 0
    while 3**weighings < coins:
        weighings += 1
    out.append(make(g, 9, "Moneta più pesante", f"Tra {coins} monete identiche all'aspetto una sola è più pesante delle altre. Hai una bilancia a due piatti, senza pesi. "
                    f"Qual è il numero minimo di pesate che garantisce di trovarla sempre?\n{IT_NUM}", _number(weighings), str(weighings), f"Risposta: {weighings}",
                    wrong=wrong_numbers(weighings, weighings + 1, weighings - 1, math.ceil(math.log2(coins)), coins), max_tokens=900, difficulty=3))

    # 10. Paths on a grid.
    rng = Rng(seed, f"{g}.010")
    across, up = rng.randint(3, 7), rng.randint(3, 6)
    paths = math.comb(across + up, across)
    out.append(make(g, 10, "Percorsi su una griglia", f"Un pedone parte dall'angolo in basso a sinistra di una griglia e deve raggiungere l'angolo in alto a destra, muovendosi di un passo alla volta "
                    f"solo verso destra o verso l'alto lungo le linee: in tutto {across} passi a destra e {up} in alto. Quanti percorsi diversi esistono?\n{IT_NUM}", _number(paths), str(paths), f"Risposta: {paths}",
                    wrong=wrong_numbers(paths, across * up, across + up, paths + across), max_tokens=900, difficulty=3))

    # 11. Lamps switched by their divisors.
    rng = Rng(seed, f"{g}.011")
    lamps = rng.randint(30, 150)
    lit = math.isqrt(lamps)
    out.append(make(g, 11, "Le lampadine", f"In un corridoio ci sono {lamps} lampadine, tutte spente, numerate da 1 a {lamps}. Passano {lamps} persone: la persona k cambia lo stato (accesa o spenta) di ogni lampadina il cui numero "
                    f"è un multiplo di k. Dopo che sono passate tutte, quante lampadine sono accese?\n{IT_NUM}", _number(lit), str(lit), f"Risposta: {lit}",
                    wrong=wrong_numbers(lit, lamps // 2, lit + 1, lamps), max_tokens=1200, difficulty=3))

    # 12. Who is third?
    text, names, order = ordering(seed, f"{g}.012", 5)
    third = order[2]
    out.append(make(g, 12, "Il terzo in altezza", f"Cinque colleghi ({', '.join(names)}) sono di altezze diverse. Si sa che:\n{text}\n\nChi è il terzo, contando dal più alto?\n"
                    "Scrivi l'ultima riga nel formato «Risposta: <nome>».", {"type": "alias", "answers": [third], "decoys": [n for n in names if n != third]}, third, f"Risposta: {third}",
                    wrong=[f"Risposta: {order[1]}", f"Risposta: {order[3]}", f"Risposta: {order[0]}"], max_tokens=1200, difficulty=3))
    return out
