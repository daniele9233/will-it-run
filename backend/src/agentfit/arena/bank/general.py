"""General knowledge: facts with one right answer, in Italian and in English.

Every fact here is settled: no «largest», «first» or «best» that historians argue about. The decoys are the answers a
model that half remembers would give, so naming one next to the right answer is not an answer.
"""

from __future__ import annotations

from agentfit.arena.bank.kit import EN_LINE, IT_LINE, make
from agentfit.arena.model import Question


def _alias(answers: list[str], decoys: list[str] | None = None) -> dict:
    return {"type": "alias", "answers": answers, "decoys": decoys or []}


def _number(value: float) -> dict:
    return {"type": "number", "value": value}


def build(seed: int) -> list[Question]:
    g = "general"
    return [
        make(g, 1, "Capitale dell'Australia", f"Qual è la capitale dell'Australia?\n{IT_LINE}", _alias(["Canberra"], ["Sydney", "Melbourne"]), "Canberra",
             "Risposta: Canberra", wrong=["Risposta: Sydney", "Risposta: Melbourne", "Risposta: Canberra, anzi no, Sydney", "Risposta: non lo so"], max_tokens=400, difficulty=1),
        make(g, 2, "Autore de La coscienza di Zeno", f"Chi ha scritto il romanzo «La coscienza di Zeno»?\n{IT_LINE}",
             _alias(["Italo Svevo", "Svevo", "Ettore Schmitz"], ["Pirandello", "Verga", "Moravia", "Calvino", "Manzoni", "Svevo e Pirandello"]), "Italo Svevo",
             "Risposta: Italo Svevo", wrong=["Risposta: Luigi Pirandello", "Risposta: Italo Calvino"], max_tokens=400, difficulty=2),
        make(g, 3, "Caduta del Muro di Berlino", f"In che anno è caduto il Muro di Berlino?\n{IT_LINE}", _number(1989), "1989", "Risposta: 1989",
             wrong=["Risposta: 1991", "Risposta: 1961", "Risposta: 1987"], max_tokens=400, difficulty=1),
        make(g, 4, "Simbolo del tungsteno", f"Qual è il simbolo chimico del tungsteno?\n{IT_LINE}", _alias(["W"], ["Tu", "Tn", "Tg", "Ti"]), "W",
             "Risposta: W", wrong=["Risposta: Tu", "Risposta: T", "Risposta: Ti"], max_tokens=400, difficulty=2),
        make(g, 5, "Il pianeta più grande", f"Qual è il pianeta più grande del sistema solare?\n{IT_LINE}",
             _alias(["Giove", "Jupiter"], ["Saturno", "Nettuno", "Urano", "Terra", "Marte"]), "Giove", "Risposta: Giove",
             wrong=["Risposta: Saturno", "Risposta: Giove o Saturno"], max_tokens=400, difficulty=1),
        make(g, 6, "Ossa dell'adulto", f"Quante ossa ha il corpo di un adulto?\n{IT_LINE}", _number(206), "206", "Risposta: 206",
             wrong=["Risposta: 300", "Risposta: 208", "Risposta: 212"], max_tokens=400, difficulty=2),
        make(g, 7, "Painter of Guernica", f"Who painted «Guernica» in 1937?\n{EN_LINE}",
             _alias(["Pablo Picasso", "Picasso"], ["Dalí", "Miró", "Goya", "Velázquez", "Matisse"]), "Pablo Picasso", "Answer: Pablo Picasso",
             wrong=["Answer: Salvador Dalí", "Answer: Joan Miró"], lang="en", max_tokens=400, difficulty=1),
        make(g, 8, "Capital of Canada", f"What is the capital of Canada?\n{EN_LINE}", _alias(["Ottawa"], ["Toronto", "Montreal", "Vancouver", "Quebec City"]), "Ottawa",
             "Answer: Ottawa", wrong=["Answer: Toronto", "Answer: Ottawa or Toronto"], lang="en", max_tokens=400, difficulty=1),
        make(g, 9, "Ebollizione in Fahrenheit", f"A quale temperatura bolle l'acqua al livello del mare, espressa in gradi Fahrenheit?\n{IT_LINE}", _number(212), "212",
             "Risposta: 212", wrong=["Risposta: 100", "Risposta: 180", "Risposta: 373"], max_tokens=400, difficulty=2),
        make(g, 10, "Relatività generale", f"Chi ha formulato la teoria della relatività generale?\n{IT_LINE}",
             _alias(["Albert Einstein", "Einstein"], ["Newton", "Bohr", "Planck", "Hawking", "Galileo", "Maxwell"]), "Albert Einstein", "Risposta: Albert Einstein",
             wrong=["Risposta: Isaac Newton", "Risposta: Niels Bohr"], max_tokens=400, difficulty=1),
        make(g, 11, "Il fiume più lungo d'Italia", f"Qual è il fiume più lungo d'Italia?\n{IT_LINE}",
             _alias(["Po", "il Po"], ["Tevere", "Adige", "Arno", "Piave", "Ticino", "Tanaro"]), "Po", "Risposta: il Po",
             wrong=["Risposta: il Tevere", "Risposta: l'Adige"], max_tokens=400, difficulty=2),
        make(g, 12, "Imperatore all'eruzione del 79", f"Quale imperatore romano regnava quando il Vesuvio distrusse Pompei, nel 79 d.C.?\n{IT_LINE}",
             _alias(["Tito", "Titus"], ["Nerone", "Augusto", "Traiano", "Adriano", "Domiziano", "Claudio", "Caligola", "Marco Aurelio"]), "Tito", "Risposta: Tito",
             wrong=["Risposta: Nerone", "Risposta: Traiano"], max_tokens=400, difficulty=3),
        make(g, 13, "Il quarto Mondiale dell'Italia", f"In che anno l'Italia ha vinto il suo quarto campionato mondiale di calcio?\n{IT_LINE}", _number(2006), "2006",
             "Risposta: 2006", wrong=["Risposta: 1982", "Risposta: 2010", "Risposta: 1994"], max_tokens=400, difficulty=2),
        make(g, 14, "Autore di Se questo è un uomo", f"Chi ha scritto «Se questo è un uomo»?\n{IT_LINE}",
             _alias(["Primo Levi"], ["Carlo Levi", "Calvino", "Pavese", "Ginzburg", "Montale", "Quasimodo", "Bassani", "Fenoglio"]), "Primo Levi", "Risposta: Primo Levi",
             wrong=["Risposta: Carlo Levi", "Risposta: Cesare Pavese"], max_tokens=400, difficulty=2),
    ]
