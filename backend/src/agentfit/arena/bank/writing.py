"""Writing: open texts that no program can judge. They are asked, kept, and shown side by side for people to read.

Nothing here is ever scored: the verdict is «not evaluated» (never «wrong»), the category has no score and no place on the
radar, and the reference is an example, not a gold standard. They are in the suite because the answers of two models to the
same request are the best way to see how they differ.
"""

from __future__ import annotations

from agentfit.arena.bank.kit import make
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng

_NOTE = "Non valutato: confronta le risposte a mano"


def _q(n: int, title: str, prompt: str, reference: str) -> Question:
    return make("writing", n, title, prompt, {"type": "manual"}, _NOTE, reference, max_tokens=700, difficulty=2)


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    rng = Rng(seed, "writing.001")
    cause, fix = rng.choice([
        ("un ritardo nella consegna dell'ordine", "la spedizione partirà domani con corriere espresso, senza costi aggiuntivi"),
        ("un errore nell'importo della fattura", "abbiamo emesso una nota di credito e vi invieremo la fattura corretta entro due giorni"),
        ("lo spostamento dell'appuntamento deciso all'ultimo momento", "vi proponiamo tre nuove date e siamo disponibili a trovarne un'altra"),
    ])
    out.append(_q(1, "Una email di scuse", f"Scrivi una email di scuse a un cliente per {cause}. Massimo 120 parole, tono professionale e cordiale.",
                  f"Gentile cliente,\nci scusiamo sinceramente per {cause}: capiamo il disagio che ha causato. Per rimediare, {fix}. Restiamo a disposizione per qualsiasi chiarimento.\nCordiali saluti"))

    rng = Rng(seed, "writing.002")
    topic, text = rng.choice([
        ("l'inflazione", "L'inflazione è quando le cose che compriamo costano un po' di più ogni anno: con la stessa paghetta oggi si compra meno gelato di ieri."),
        ("la fotosintesi", "Le piante sono come cuochi che usano la luce del sole: prendono acqua e aria e le trasformano nel cibo che serve a crescere, e ci regalano l'ossigeno."),
        ("internet", "Internet è una enorme rete che collega i computer di tutto il mondo, come tante strade che permettono a un messaggio di arrivare dall'altra parte in un attimo."),
    ])
    out.append(_q(2, "Spiegare a un bambino", f"Spiega a un bambino di otto anni che cos'è {topic}, in al massimo cinque righe.", text))

    passage = ("La biblioteca comunale resterà chiusa per lavori dal 3 al 21 marzo. In questo periodo il prestito dei libri sarà sospeso, ma sarà possibile restituire i volumi nel punto di raccolta davanti all'ingresso. "
               "Le sale studio saranno trasferite temporaneamente nella scuola media di via Verdi, aperta dal lunedì al venerdì dalle 15 alle 19. La riapertura è prevista per lunedì 24 marzo.")
    out.append(_q(3, "Riassumere un avviso", f"Riassumi in due frasi questo avviso:\n\n«{passage}»",
                  "La biblioteca resta chiusa dal 3 al 21 marzo: i libri si possono restituire davanti all'ingresso e le sale studio si spostano alla scuola media di via Verdi. Riapre lunedì 24 marzo."))

    rng = Rng(seed, "writing.004")
    theme, poem = rng.choice([
        ("l'autunno", "Foglie sul sentiero\nil vento le porta via\ncade la prima pioggia"),
        ("il mare", "Onde sulla riva\nil sale resta sul vetro\nsilenzio di sera"),
        ("una città di notte", "Luci sull'asfalto\npassi che nessuno ascolta\nsi accende una finestra"),
    ])
    out.append(_q(4, "Un haiku", f"Scrivi un haiku (tre versi) su {theme}.", poem))
    return out
