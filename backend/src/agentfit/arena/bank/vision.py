"""Vision: counting, reading and comparing things in pictures the suite draws itself.

A photograph has no ground truth a program can check, so the pictures are made like the CLEVR ones: shapes, charts, a
receipt, a clock, all drawn from the seed, and every answer is known exactly. A model that cannot see is skipped, not
failed. One question shows a house and asks for the plate of a car that is not there.
"""

from __future__ import annotations

import base64
import math
from typing import Any

from agentfit.arena.bank.kit import IT_LINE, IT_NUM, make, wrong_numbers
from agentfit.arena.model import Image, Question
from agentfit.arena.pngart import Canvas, Color
from agentfit.arena.rng import Rng

RED: Color = (215, 38, 38)
BLUE: Color = (30, 80, 205)
GREEN: Color = (34, 150, 70)
YELLOW: Color = (240, 195, 25)
PURPLE: Color = (135, 55, 175)
INK: Color = (30, 30, 30)
PAPER: Color = (255, 255, 255)
GRID: Color = (222, 222, 222)

COLORS: dict[str, Color] = {"rosso": RED, "blu": BLUE, "verde": GREEN, "giallo": YELLOW, "viola": PURPLE}
# What a person (or a model) may call each colour. «azzurro» is accepted for the blue: it is the one name that is not exact.
NAMES: dict[str, list[str]] = {
    "rosso": ["rosso", "rossa", "red"], "blu": ["blu", "azzurro", "azzurra", "celeste", "blue"], "verde": ["verde", "green"],
    "giallo": ["giallo", "gialla", "yellow"], "viola": ["viola", "porpora", "purple", "violet", "lilla"],
}
_KINDS = ("circle", "square", "triangle")
_NEEDS = ("vision",)


def _image(number: int, canvas: Canvas) -> Image:
    return Image(name=f"vision-{number:03d}.png", data_b64=base64.b64encode(canvas.png()).decode("ascii"), width=canvas.width, height=canvas.height,
                 pixels_sha256=canvas.pixels_sha256())


def _draw(c: Canvas, kind: str, cx: int, cy: int, size: int, color: Color) -> None:
    half = size // 2
    if kind == "circle":
        c.circle(cx, cy, half, color)
    elif kind == "square":
        c.rect(cx - half, cy - half, size, size, color)
    else:
        c.triangle((cx, cy - half), (cx - half, cy + half), (cx + half, cy + half), color)


def _spot(rng: Rng, cell: int, col: int, row: int, size: int) -> tuple[int, int]:
    """The centre of a shape inside its cell of the grid, a little off the middle but never over the next cell."""
    slack = max(0, (cell - size) // 2 - 3)
    return col * cell + cell // 2 + rng.randint(-slack, slack), row * cell + cell // 2 + rng.randint(-slack, slack)


def _centered(c: Canvas, y: int, value: str, color: Color, scale: int, x: int | None = None) -> None:
    c.text((c.width - c.text_width(value, scale)) // 2 if x is None else x, y, value, color, scale)


def _alias(answers: list[str], decoys: list[str] | None = None, exact: bool = False) -> dict[str, Any]:
    return {"type": "alias", "answers": answers, "decoys": decoys or [], "exact": exact}


def _choice(correct: str, names: list[str]) -> dict[str, Any]:
    return {"type": "choice", "options": {n: NAMES[n] for n in names}, "correct": correct}


def _q(n: int, title: str, prompt: str, check: dict[str, Any], expected: str, reference: str, canvas: Canvas, *, wrong: list[str], difficulty: int = 2,
       max_tokens: int = 600) -> Question:
    return make("vision", n, title, prompt, check, expected, reference, wrong=wrong, images=(_image(n, canvas),), needs=_NEEDS, difficulty=difficulty, max_tokens=max_tokens)


# -- 1. count red circles among other shapes -----------------------------------------------------------------


def _count_red_circles(seed: int) -> Question:
    rng = Rng(seed, "vision.001")
    target = rng.randint(2, 5)
    red_squares = rng.randint(2, 3)
    plan: list[tuple[str, Color]] = ([("circle", RED)] * target + [("circle", BLUE)] * rng.randint(2, 3) + [("square", RED)] * red_squares
                                     + [("triangle", GREEN)] * rng.randint(1, 2) + [("circle", YELLOW)])
    rng.shuffle(plan)
    cells = rng.sample([(col, row) for col in range(6) for row in range(4)], len(plan))
    canvas = Canvas(480, 320)
    circles = sum(1 for kind, _ in plan if kind == "circle")
    for (kind, color), (col, row) in zip(plan, cells, strict=True):
        size = rng.randint(40, 50)
        cx, cy = _spot(rng, 80, col, row, size)
        _draw(canvas, kind, cx, cy, size, color)
    return _q(1, "Contare i cerchi rossi", f"Nell'immagine ci sono figure geometriche di vari colori.\nQuanti cerchi rossi ci sono?\n{IT_NUM}",
              {"type": "number", "value": target, "integer": True}, str(target), f"Risposta: {target}", canvas,
              wrong=wrong_numbers(target, target + 1, target - 1 if target > 1 else target + 2, target + red_squares, circles), difficulty=1)


# -- 2. read a code ------------------------------------------------------------------------------------------

# No letter that looks like a digit and no digit that looks like a letter: O/0, I/1, S/5, B/8, Z/2.
_CODE_CHARS = "ACDEFGHJKLMNPRTUVWXY234679"


def _read_code(seed: int) -> Question:
    rng = Rng(seed, "vision.002")
    code = "".join(rng.sample(list(_CODE_CHARS), 5))
    canvas = Canvas(440, 170, (250, 247, 235))
    canvas.frame(6, 6, 428, 158, (90, 90, 90), 3)
    canvas.text(24, 20, "CODICE", (120, 120, 120), 3)
    _centered(canvas, 74, code, INK, 8)
    swapped = code[1] + code[0] + code[2:]
    return _q(2, "Leggere un codice", f"Nell'immagine c'è un codice scritto in stampatello.\nQual è il codice?\n{IT_LINE}", _alias([code, " ".join(code)], exact=True), code, f"Risposta: {code}", canvas,
              wrong=[f"Risposta: {swapped}", f"Risposta: {code[::-1]}", f"Risposta: {code[:4]}", f"Risposta: {code[:2]}{_CODE_CHARS[(_CODE_CHARS.index(code[2]) + 1) % len(_CODE_CHARS)]}{code[3:]}"],
              difficulty=2, max_tokens=500)


# -- 3. the colour of the largest shape ------------------------------------------------------------------------


def _largest_color(seed: int) -> Question:
    rng = Rng(seed, "vision.003")
    names = list(COLORS)
    rng.shuffle(names)
    # The largest is a circle or a square: by area it is then far bigger than a triangle that looks as tall.
    sizes = [90] + [rng.randint(30, 50) for _ in range(4)]
    kinds = [rng.choice(["circle", "square"])] + [rng.choice(_KINDS) for _ in range(4)]
    cells = rng.sample([(col, row) for col in range(5) for row in range(3)], 5)
    canvas = Canvas(480, 288)
    for name, size, kind, (col, row) in zip(names, sizes, kinds, cells, strict=True):
        cx, cy = _spot(rng, 96, col, row, size)
        _draw(canvas, kind, cx, cy, size, COLORS[name])
    right = names[0]
    others = names[1:]
    return _q(3, "Colore della figura più grande", f"Di che colore è la figura più grande dell'immagine?\n{IT_LINE}", _choice(right, list(COLORS)), right, f"Risposta: {right}", canvas,
              wrong=[f"Risposta: {others[0]}", f"Risposta: {others[1]}", "Risposta: non lo so"], difficulty=1, max_tokens=500)


# -- bar charts --------------------------------------------------------------------------------------------------


def _bars(c: Canvas, values: list[int], *, width: int, gap: int, base: int, labels: list[str] | None = None, show_values: bool = False, scale_y: int = 1) -> None:
    total = len(values) * width + (len(values) - 1) * gap
    left = (c.width - total) // 2
    for line in range(base - 50 * scale_y, 20, -50 * scale_y):
        c.rect(left - 14, line, total + 28, 1, GRID)
    for i, value in enumerate(values):
        x = left + i * (width + gap)
        height = value * scale_y
        c.rect(x, base - height, width, height, BLUE)
        if show_values:
            text = str(value)
            c.text(x + (width - c.text_width(text, 3)) // 2, base - height - 28, text, INK, 3)
        if labels:
            c.text(x + (width - c.text_width(labels[i], 2)) // 2, base + 12, labels[i], INK, 2)
    c.rect(left - 14, base, total + 28, 2, INK)


def _highest_bar(seed: int) -> Question:
    rng = Rng(seed, "vision.004")
    labels = rng.sample(["NORD", "SUD", "EST", "OVEST", "CENTRO"], 4)
    values = rng.sample(list(range(50, 201, 15)), 4)
    canvas = Canvas(480, 300)
    _bars(canvas, values, width=70, gap=36, base=262, labels=labels)
    top = labels[values.index(max(values))]
    others = [label for label in labels if label != top]
    return _q(4, "La barra più alta", f"Il grafico a barre mostra le vendite di quattro zone.\nQuale zona ha la barra più alta?\n{IT_LINE}", _alias([top], others), top.title(), f"Risposta: {top.title()}", canvas,
              wrong=[f"Risposta: {others[0].title()}", f"Risposta: {others[1].title()}", f"Risposta: {top.title()} e {others[2].title()}"], difficulty=2, max_tokens=500)


# -- 5. the sum of a receipt ---------------------------------------------------------------------------------------

_GOODS = ["PANE", "LATTE", "PASTA", "UOVA", "MELE", "OLIO", "CAFFE", "BISCOTTI", "FORMAGGIO", "RISO", "TONNO", "ACQUA"]


def _price(cents: int) -> str:
    return f"{cents // 100},{cents % 100:02d}"


def _receipt(seed: int) -> Question:
    rng = Rng(seed, "vision.005")
    names = rng.sample(_GOODS, 4)
    prices = [rng.randint(90, 2400) for _ in names]
    total = sum(prices)
    canvas = Canvas(440, 64 + 4 * 38 + 34, (252, 252, 248))
    canvas.frame(4, 4, 432, canvas.height - 8, (170, 170, 170), 2)
    _centered(canvas, 18, "SCONTRINO", INK, 4)
    for i, (name, cents) in enumerate(zip(names, prices, strict=True)):
        y = 74 + i * 38
        canvas.text(26, y, name, INK, 3)
        canvas.text(414 - canvas.text_width(_price(cents), 3), y, _price(cents), INK, 3)
    value = total / 100
    wrong_values = [value + 1, value - 1, (total - prices[0]) / 100, value + 10]
    return _q(5, "Somma di uno scontrino", f"L'immagine è uno scontrino a cui hanno tolto il totale.\nQuanto fa la somma di tutti i prezzi?\n{IT_NUM}",
              {"type": "number", "value": value, "tolerance": 0.005}, f"{value:.2f}", f"Risposta: {_price(total)}", canvas,
              wrong=wrong_numbers(value, *wrong_values, fmt=lambda v: f"{v:.2f}".replace(".", ",")), difficulty=2, max_tokens=900)


# -- 6. a car that is not in the picture --------------------------------------------------------------------------


def _house_without_car(seed: int) -> Question:
    rng = Rng(seed, "vision.006")
    canvas = Canvas(480, 300, (214, 232, 248))
    canvas.rect(0, 230, 480, 70, (122, 178, 98))
    shift = rng.randint(-30, 30)
    wall = rng.choice([(236, 200, 150), (225, 225, 225), (240, 180, 160)])
    x = 150 + shift
    canvas.rect(x, 130, 170, 100, wall)
    canvas.triangle((x - 14, 130), (x + 184, 130), (x + 85, 66), (150, 70, 50))
    canvas.rect(x + 68, 170, 34, 60, (110, 70, 40))
    canvas.rect(x + 18, 150, 30, 30, (150, 200, 235))
    canvas.rect(x + 122, 150, 30, 30, (150, 200, 235))
    sun_x = rng.choice([48, 420])
    canvas.circle(sun_x, 56, 28, YELLOW)
    tree_x = 70 if sun_x > 100 else 430
    canvas.rect(tree_x - 7, 170, 14, 62, (110, 75, 40))
    canvas.circle(tree_x, 150, 38, (50, 140, 60))
    plate = r"\b[A-Z]{2}\s?-?\d{3}\s?-?[A-Z]{2}\b"
    return _q(6, "La targa di un'auto che non c'è", "Qual è il numero di targa dell'auto parcheggiata davanti alla casa nell'immagine?",
              {"type": "abstain", "mode": "must_abstain", "forbid": [plate]}, "L'immagine non contiene nessuna auto: non si può leggere una targa.",
              "Nell'immagine non c'è nessuna auto, quindi non posso leggere una targa: vedo solo una casa, un albero e un sole.", canvas,
              wrong=["La targa dell'auto è AB 123 CD.", "Risposta: FG456HI", "Non sono sicuro, ma credo che la targa sia XY 987 ZW.", "L'auto ha la targa MN 246 PQ e sta davanti alla porta."],
              difficulty=2, max_tokens=500)


# -- 7. the colour of the shape that is highest up ------------------------------------------------------------------


def _topmost_color(seed: int) -> Question:
    rng = Rng(seed, "vision.007")
    names = rng.sample(list(COLORS), 4)
    rows = [0, 1, 2, 3]
    rng.shuffle(rows)
    canvas = Canvas(480, 320)
    for name, row in zip(names, rows, strict=True):
        size = rng.randint(40, 50)
        cx, cy = _spot(rng, 80, rng.randint(0, 5), row, size)
        _draw(canvas, rng.choice(_KINDS), cx, cy, size, COLORS[name])
    # Four shapes in four different rows, one cell tall each: the one in the first row is the highest, by a whole row.
    right = names[rows.index(0)]
    others = [n for n in names if n != right]
    return _q(7, "La figura più in alto", f"Di che colore è la figura che si trova più in alto nell'immagine?\n{IT_LINE}", _choice(right, list(COLORS)), right, f"Risposta: {right}", canvas,
              wrong=[f"Risposta: {others[0]}", f"Risposta: {others[1]}"], difficulty=2, max_tokens=500)


# -- 8. count the shapes with three sides -----------------------------------------------------------------------------


def _count_triangles(seed: int) -> Question:
    rng = Rng(seed, "vision.008")
    triangles = rng.randint(2, 5)
    squares = rng.randint(2, 4)
    circles = rng.randint(2, 4)
    plan = ["triangle"] * triangles + ["square"] * squares + ["circle"] * circles
    rng.shuffle(plan)
    cells = rng.sample([(col, row) for col in range(6) for row in range(4)], len(plan))
    canvas = Canvas(480, 320)
    for kind, (col, row) in zip(plan, cells, strict=True):
        size = rng.randint(40, 50)
        cx, cy = _spot(rng, 80, col, row, size)
        _draw(canvas, kind, cx, cy, size, COLORS[rng.choice(list(COLORS))])
    return _q(8, "Contare le figure con tre lati", f"Quante figure dell'immagine hanno esattamente tre lati?\n{IT_NUM}", {"type": "number", "value": triangles, "integer": True}, str(triangles),
              f"Risposta: {triangles}", canvas, wrong=wrong_numbers(triangles, triangles + 1, triangles - 1 if triangles > 1 else triangles + 2, len(plan), squares), difficulty=2)


# -- 9. the sum of the values written above the bars ---------------------------------------------------------------------


def _sum_of_bars(seed: int) -> Question:
    rng = Rng(seed, "vision.009")
    values = rng.sample(list(range(12, 97)), 5)
    canvas = Canvas(480, 300)
    _bars(canvas, values, width=56, gap=28, base=266, show_values=True, scale_y=2)
    total = sum(values)
    return _q(9, "Somma dei valori di un grafico", f"Sopra ogni barra del grafico c'è scritto il suo valore.\nQuanto fa la somma dei valori di tutte le barre?\n{IT_NUM}",
              {"type": "number", "value": total, "integer": True}, str(total), f"Risposta: {total}", canvas, wrong=wrong_numbers(total, total + 10, total - 10, total - values[0], max(values)), difficulty=3, max_tokens=900)


# -- 10. read a clock ---------------------------------------------------------------------------------------------------------


def _hand(c: Canvas, cx: int, cy: int, degrees: float, length: int, thickness: int) -> None:
    angle = math.radians(degrees)
    c.line(cx, cy, round(cx + length * math.sin(angle)), round(cy - length * math.cos(angle)), INK, thickness)


def _clock(seed: int) -> Question:
    rng = Rng(seed, "vision.010")
    while True:
        hour, minute = rng.randint(1, 12), rng.choice([0, 15, 30, 45])
        hour_angle, minute_angle = (hour % 12) * 30 + minute * 0.5, minute * 6
        apart = abs(hour_angle - minute_angle) % 360
        # Hands on top of each other cannot be told apart: draw only times where they are at least 40 degrees apart.
        if min(apart, 360 - apart) >= 40:
            break
    cx = cy = 150
    canvas = Canvas(300, 300)
    canvas.circle(cx, cy, 138, INK)
    canvas.circle(cx, cy, 132, PAPER)
    for i in range(12):
        angle = math.radians(i * 30)
        canvas.line(round(cx + 114 * math.sin(angle)), round(cy - 114 * math.cos(angle)), round(cx + 126 * math.sin(angle)), round(cy - 126 * math.cos(angle)), INK, 3)
    # The hands stop short of the numbers, so that a hand never covers the figure it points at.
    for text, (dx, dy) in {"12": (0, -100), "3": (100, 0), "6": (0, 100), "9": (-100, 0)}.items():
        canvas.text(cx + dx - canvas.text_width(text, 3) // 2, cy + dy - 10, text, INK, 3)
    _hand(canvas, cx, cy, hour_angle, 58, 8)
    _hand(canvas, cx, cy, minute_angle, 84, 4)
    canvas.circle(cx, cy, 7, INK)
    right = f"{hour}:{minute:02d}"
    forms = [right, f"{hour:02d}:{minute:02d}", f"{hour % 12 + 12}:{minute:02d}"] + ([f"00:{minute:02d}", f"0:{minute:02d}"] if hour == 12 else [])
    swapped = f"{(minute // 5) or 12}:{round(hour_angle / 6) % 60:02d}"
    shifted = f"{hour}:{(minute + 30) % 60:02d}"
    next_hour = f"{hour % 12 + 1}:{minute:02d}"
    decoys = [d for d in (swapped, shifted, next_hour) if tuple(d.replace(":", " ").split()) != (str(hour), f"{minute:02d}")]
    return _q(10, "Leggere un orologio", f"Che ora segna l'orologio? Scrivi l'ora nel formato ore:minuti, per esempio 9:05.\n{IT_LINE}", _alias(forms, decoys), right, f"Risposta: {right}", canvas,
              wrong=[f"Risposta: {swapped}", f"Risposta: {shifted}", f"Risposta: {next_hour}"], difficulty=3, max_tokens=700)


def build(seed: int) -> list[Question]:
    """The first six are the quick and standard packs: counting, reading, comparing, the receipt and the car that is not there."""
    return [
        _count_red_circles(seed), _read_code(seed),
        _largest_color(seed), _highest_bar(seed), _receipt(seed), _house_without_car(seed),
        _topmost_color(seed), _count_triangles(seed), _sum_of_bars(seed), _clock(seed),
    ]
