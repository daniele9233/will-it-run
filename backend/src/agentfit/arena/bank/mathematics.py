"""Mathematics: arithmetic, percentages, equations, probability, counting, modular arithmetic and word problems.

Every question is generated from the seed and solved here, in exact arithmetic where it matters, so the expected number is
never typed by hand and a model cannot have seen the question. The answer is read from the «Risposta:» line.
"""

from __future__ import annotations

import math
from fractions import Fraction

from agentfit.arena.bank.kit import IT_NUM, make, wrong_numbers
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng


def it(value: float, digits: int = 2) -> str:
    """A number as an Italian reader writes it: 1234,5."""
    text = f"{value:.{digits}f}"
    return text.replace(".", ",")


def _number(value: float, tolerance: float | None = None) -> dict:
    spec: dict = {"type": "number", "value": float(value)}
    if tolerance is not None:
        spec["tolerance"] = tolerance
    return spec


def _frac_text(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}" if f.denominator != 1 else str(f.numerator)


def _fraction_slips(correct: Fraction, *pairs: tuple[int, int]) -> list[str]:
    """«Risposta: n/d» lines for the slips that are really different from the right fraction (4/10 is 2/5, so it is no slip)."""
    seen = [correct]
    out: list[str] = []
    for n, d in pairs:
        value = Fraction(n, d)
        if value not in seen:
            seen.append(value)
            out.append(f"Risposta: {_frac_text(value)}")
    step = 1
    while len(out) < 2:
        value = Fraction(correct.numerator + step, correct.denominator)
        step += 1
        if value not in seen:
            seen.append(value)
            out.append(f"Risposta: {_frac_text(value)}")
    return out


def build(seed: int) -> list[Question]:
    m = "math"
    out: list[Question] = []

    # 1. Long multiplication and a subtraction.
    rng = Rng(seed, f"{m}.001")
    a, b, c = rng.randint(120, 989), rng.randint(12, 97), rng.randint(100, 999)
    value = a * b - c
    out.append(make(m, 1, "Moltiplicazione e sottrazione", f"Calcola {a} × {b} − {c}.\n{IT_NUM}", _number(value), str(value), f"{a} × {b} = {a * b}, meno {c} fa {value}.\nRisposta: {value}",
                    wrong=wrong_numbers(value, a * b, value + 10, a * b + c, value - 100), max_tokens=500, difficulty=1))

    # 2. A fixed discount first, then VAT: the order matters.
    rng = Rng(seed, f"{m}.002")
    price, discount = rng.randint(12, 48) * 10, rng.randint(2, 6) * 5
    pay = (price - discount) * 1.22
    wrong_order = price * 1.22 - discount
    out.append(make(m, 2, "Sconto fisso e IVA", f"Un articolo costa {price} €. Si applica prima uno sconto fisso di {discount} € e poi l'IVA al 22 %. Quanto paga il cliente, in euro? Arrotonda a due decimali.\n{IT_NUM}",
                    _number(pay, 0.0051), it(pay), f"({price} − {discount}) × 1,22 = {it(pay)}\nRisposta: {it(pay)}",
                    wrong=wrong_numbers(pay, wrong_order, price - discount, price * 1.22, pay + 1, fmt=it, tol=0.006), max_tokens=600, difficulty=2))

    # 3. A linear equation with the unknown on both sides.
    rng = Rng(seed, f"{m}.003")
    x = rng.randint(2, 15) * rng.choice([1, 1, -1])
    p, q = rng.randint(4, 9), rng.randint(1, 3)
    r0 = rng.randint(1, 20)
    s0 = (p - q) * x + r0
    out.append(make(m, 3, "Equazione di primo grado", f"Risolvi l'equazione {p}x + {r0} = {q}x + {s0}. Qual è il valore di x?\n{IT_NUM}", _number(x), str(x), f"Risposta: {x}",
                    wrong=wrong_numbers(x, -x, x + 1, s0 - r0, 0), max_tokens=500, difficulty=2))

    # 4. Two percentage changes in a row.
    rng = Rng(seed, f"{m}.004")
    while True:
        up, down = rng.choice([10, 15, 20, 25, 30, 40, 50]), rng.choice([10, 15, 20, 25, 30, 40, 50])
        change = ((1 + up / 100) * (1 - down / 100) - 1) * 100
        if abs(change) >= 1 and up != down:
            break
    out.append(make(m, 4, "Due variazioni percentuali", f"Un prezzo sale del {up} % e subito dopo scende del {down} %. Di quanto è variato, in percentuale, rispetto al prezzo iniziale? "
                    f"Scrivi un numero (negativo se è sceso), arrotondato a due decimali.\n{IT_NUM}", _number(change, 0.0051), it(change), f"Risposta: {it(change)}",
                    wrong=wrong_numbers(change, up - down, -(up - down), change + 5, abs(change) if change < 0 else -change, fmt=it, tol=0.006), max_tokens=600, difficulty=2))

    # 5. The sum of two fractions, reduced.
    rng = Rng(seed, f"{m}.005")
    while True:
        d1, d2 = rng.randint(3, 12), rng.randint(3, 12)
        n1, n2 = rng.randint(1, d1 - 1), rng.randint(1, d2 - 1)
        total = Fraction(n1, d1) + Fraction(n2, d2)
        if d1 != d2 and total.denominator != 1:
            break
    out.append(make(m, 5, "Somma di frazioni", f"Calcola {n1}/{d1} + {n2}/{d2}. Scrivi il risultato come frazione ridotta ai minimi termini, nel formato «Risposta: n/d».", _number(float(total)),
                    _frac_text(total), f"Risposta: {_frac_text(total)}",
                    wrong=_fraction_slips(total, (n1 + n2, d1 + d2), (n1 + n2, d1 * d2), (n1 * n2, d1 * d2)), max_tokens=600, difficulty=2))

    # 6. Two dice.
    rng = Rng(seed, f"{m}.006")
    target = rng.randint(4, 10)
    ways = sum(1 for i in range(1, 7) for j in range(1, 7) if i + j == target)
    prob = Fraction(ways, 36)
    out.append(make(m, 6, "Probabilità con due dadi", f"Si lanciano due dadi a sei facce non truccati. Qual è la probabilità che la somma dei due numeri sia {target}? "
                    "Scrivi la frazione ridotta ai minimi termini, nel formato «Risposta: n/d».", _number(float(prob)), _frac_text(prob), f"Risposta: {_frac_text(prob)}",
                    wrong=_fraction_slips(prob, (1, target), (ways + 1, 36), (ways, 12), (ways, 6), (1, 6)), max_tokens=700, difficulty=2))

    # 7. Choosing people from a group.
    rng = Rng(seed, f"{m}.007")
    n, k = rng.randint(8, 13), rng.randint(2, 4)
    ways_k = math.comb(n, k)
    out.append(make(m, 7, "Combinazioni", f"In quanti modi si possono scegliere {k} persone da un gruppo di {n}, senza tener conto dell'ordine?\n{IT_NUM}", _number(ways_k), str(ways_k), f"Risposta: {ways_k}",
                    wrong=wrong_numbers(ways_k, math.perm(n, k), n * k, ways_k + n), max_tokens=500, difficulty=2))

    # 8. Compound interest, no rounding on the way.
    rng = Rng(seed, f"{m}.008")
    capital, rate, years = rng.randint(2, 20) * 500, rng.choice([2, 3, 4, 5, 6]), rng.choice([2, 3])
    final = capital * (1 + rate / 100) ** years
    simple = capital * (1 + rate / 100 * years)
    out.append(make(m, 8, "Interesse composto", f"Si investono {capital} € al tasso annuo del {rate} % con interessi composti, senza prelievi. Quanto si ha dopo {years} anni? "
                    f"Arrotonda il risultato finale a due decimali.\n{IT_NUM}", _number(final, 0.0051), it(final), f"Risposta: {it(final)}",
                    wrong=wrong_numbers(final, simple, capital * rate / 100 * years, final + 10, fmt=it, tol=0.006), max_tokens=700, difficulty=2))

    # 9. Ages today from a statement about the future.
    rng = Rng(seed, f"{m}.009")
    while True:
        son, father, t = rng.randint(6, 14), rng.randint(32, 46), rng.randint(4, 10)
        if (father + t) % (son + t) == 0 and (father + t) // (son + t) >= 2 and father - son >= 20:
            break
    k_times = (father + t) // (son + t)
    total_ages = son + father
    out.append(make(m, 9, "Età del figlio", f"Oggi la somma delle età di un padre e di suo figlio è {total_ages} anni. Tra {t} anni il padre avrà esattamente {k_times} volte l'età del figlio. "
                    f"Quanti anni ha oggi il figlio?\n{IT_NUM}", _number(son), str(son), f"Risposta: {son}",
                    wrong=wrong_numbers(son, father, son + t, total_ages - son - 1, total_ages // (k_times + 1)), max_tokens=900, difficulty=3))

    # 10. Greatest common divisor, and the least common multiple.
    rng = Rng(seed, f"{m}.010")
    g, u, w = rng.randint(3, 14), rng.choice([2, 3, 4, 5, 7]), rng.choice([9, 11, 13])
    x1, x2 = g * u, g * w
    want = rng.choice(["mcd", "mcm"])
    answer = math.gcd(x1, x2) if want == "mcd" else math.lcm(x1, x2)
    label = "il massimo comune divisore" if want == "mcd" else "il minimo comune multiplo"
    other = math.lcm(x1, x2) if want == "mcd" else math.gcd(x1, x2)
    out.append(make(m, 10, "MCD o mcm", f"Calcola {label} di {x1} e {x2}.\n{IT_NUM}", _number(answer), str(answer), f"Risposta: {answer}",
                    wrong=wrong_numbers(answer, other, x1 * x2, answer + g), max_tokens=500, difficulty=2))

    # 11. A remainder that nobody computes by hand: modular exponentiation.
    rng = Rng(seed, f"{m}.011")
    base, exp, mod = rng.choice([2, 3, 7, 11]), rng.randint(40, 200), rng.choice([13, 17, 19, 23, 29])
    rest = pow(base, exp, mod)
    out.append(make(m, 11, "Resto di una potenza", f"Qual è il resto della divisione di {base}^{exp} per {mod}?\n{IT_NUM}", _number(rest), str(rest), f"Risposta: {rest}",
                    wrong=wrong_numbers(rest, (rest + 1) % mod, exp % mod, mod - 1, 0), max_tokens=900, difficulty=3))

    # 12. Two trains that meet.
    rng = Rng(seed, f"{m}.012")
    v1, v2 = rng.randint(6, 12) * 10, rng.randint(6, 12) * 10
    minutes = rng.choice([30, 36, 42, 48, 54, 60])
    distance = (v1 + v2) * minutes // 60
    out.append(make(m, 12, "Due treni che si incrociano", f"Due città distano {distance} km. Da ciascuna parte un treno, nello stesso istante, diretto verso l'altra: uno viaggia a {v1} km/h, l'altro a {v2} km/h. "
                    f"Dopo quanti minuti si incrociano?\n{IT_NUM}", _number(minutes), str(minutes), f"Risposta: {minutes}",
                    wrong=wrong_numbers(minutes, round(distance / max(v1, v2) * 60), round(distance / min(v1, v2) * 60), minutes + 6), max_tokens=700, difficulty=2))

    # 13. A weighted average.
    rng = Rng(seed, f"{m}.013")
    marks = [rng.randint(18, 30) for _ in range(3)]
    weights = rng.choice([[2, 3, 5], [1, 2, 3], [3, 3, 4], [1, 1, 2]])
    mean = sum(v * w for v, w in zip(marks, weights, strict=True)) / sum(weights)
    plain = sum(marks) / 3
    desc = ", ".join(f"{v} con peso {w}" for v, w in zip(marks, weights, strict=True))
    out.append(make(m, 13, "Media ponderata", f"Un esame ha tre prove: {desc}. Qual è la media ponderata? Arrotonda a due decimali.\n{IT_NUM}", _number(mean, 0.0051), it(mean), f"Risposta: {it(mean)}",
                    wrong=wrong_numbers(mean, plain, mean + 1, sum(marks), fmt=it, tol=0.006), max_tokens=700, difficulty=2))

    # 14. Two workers, one job.
    rng = Rng(seed, f"{m}.014")
    pa, pb = rng.choice([(6, 12), (10, 15), (12, 24), (20, 30), (4, 12), (15, 10), (30, 20)])
    together = Fraction(pa * pb, pa + pb)
    out.append(make(m, 14, "Lavoro in due", f"Un operaio completa un lavoro in {pa} giorni, un altro lo completa da solo in {pb} giorni. Lavorando insieme, in quanti giorni lo finiscono? "
                    f"Se non è un numero intero scrivi i decimali.\n{IT_NUM}", _number(float(together), 0.0051), it(float(together)), f"Risposta: {it(float(together))}",
                    wrong=wrong_numbers(float(together), (pa + pb) / 2, pa + pb, float(together) + 1, fmt=it, tol=0.006), max_tokens=700, difficulty=2))
    return out
