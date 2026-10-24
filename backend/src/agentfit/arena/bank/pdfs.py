"""PDF analysis: real PDF files (an invoice, a contract, a quarterly report), made from the seed, byte for byte the same every
time. The text the model reads is the text extracted *from the file* by the Arena's own PDF reader, page markers included,
and the question asks for a number, a date or a name that is in it, or for something that is not.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import date, timedelta

from agentfit.arena import pdfgen
from agentfit.arena.bank.kit import IT_LINE, IT_NUM, make, wrong_numbers
from agentfit.arena.model import Attachment, Question
from agentfit.arena.rng import Rng

_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
_ISSUERS = ["Rossi Forniture Srl", "Bianchi Ufficio SpA", "Verdi Informatica Snc", "Gallo Servizi Srl"]
_CLIENTS = ["Studio Colombo", "Ricci & Partners", "Fontana Logistica", "Marini Costruzioni"]
_ITEMS = [("Tastiera meccanica", 25, 60), ("Monitor 27 pollici", 150, 300), ("Webcam HD", 30, 70), ("Cuffie con microfono", 40, 90), ("Hub USB-C", 20, 45), ("Stampante laser", 90, 180)]


def eur(value: float) -> str:
    whole, cents = divmod(round(value * 100), 100)
    return f"{whole:,}".replace(",", ".") + f",{cents:02d}"


def date_names(d: date) -> list[str]:
    month = _MONTHS[d.month - 1]
    return [f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.day}/{d.month}/{d.year}", f"{d.day} {month} {d.year}", f"{d.day:02d} {month} {d.year}", f"{d.year}-{d.month:02d}-{d.day:02d}",
            f"{d.day:02d}-{d.month:02d}-{d.year}", f"{d.day:02d}.{d.month:02d}.{d.year}"]


def _attach(name: str, pages: list[list[str]], title: str) -> tuple[Attachment, str]:
    data = pdfgen.build(pages, title=title)
    if pdfgen.extract_pages(data) != pages:
        raise RuntimeError(f"{name} does not read back as it was written")
    text = "\n\n".join(f"--- Pagina {i} ---\n" + "\n".join(lines) for i, lines in enumerate(pdfgen.extract_pages(data), 1))
    attachment = Attachment(name=name, mime="application/pdf", data_b64=base64.b64encode(data).decode("ascii"), sha256=hashlib.sha256(data).hexdigest(), pages=len(pages))
    return attachment, text


def _prompt(name: str, pages: int, text: str, ask: str, line: str) -> str:
    return (f"Ecco il testo estratto dal file PDF «{name}» ({pages} {'pagina' if pages == 1 else 'pagine'}).\n\n{text}\n\nRispondi solo in base al documento. {ask}\n{line}")


def _invoice(seed: int) -> tuple[Attachment, str, dict]:
    rng = Rng(seed, "pdf.invoice")
    number = f"FT-2026-{rng.randint(100, 999)}"
    issued = date(2026, rng.randint(1, 10), rng.randint(2, 27))
    days = rng.choice([30, 60])
    due = issued + timedelta(days=days)
    issuer, client = rng.choice(_ISSUERS), rng.choice(_CLIENTS)
    items = rng.sample(_ITEMS, 4)
    lines = [(name, rng.randint(1, 6), rng.randint(lo // 5, hi // 5) * 5) for name, lo, hi in items]
    imp = sum(q * p for _, q, p in lines)
    vat = round(imp * 0.22, 2)
    total = round(imp + vat, 2)
    rows = [["Articolo", "Qta", "Prezzo", "Totale"]] + [[name, str(q), eur(p), eur(q * p)] for name, q, p in lines]
    page1 = [f"FATTURA N. {number}", f"Data di emissione: {issued.day:02d}/{issued.month:02d}/{issued.year}", f"Scadenza di pagamento: {due.day:02d}/{due.month:02d}/{due.year}", "",
             f"Emittente: {issuer}", f"Cliente: {client}", "", *pdfgen.table_lines(rows), "", f"Imponibile: {eur(imp)} EUR", f"IVA 22%: {eur(vat)} EUR", f"Totale da pagare: {eur(total)} EUR"]
    page2 = ["COORDINATE E CONDIZIONI", "", "IBAN: IT00X0000000000000000000000", "Causale: pagamento fattura " + number, "", "Il pagamento va effettuato entro la data di scadenza.",
             "In caso di ritardo si applicano interessi di mora dell'8% annuo.", "Per contestazioni scrivere entro 15 giorni dal ricevimento."]
    attachment, text = _attach("fattura.pdf", [page1, page2], f"Fattura {number}")
    return attachment, text, {"total": total, "imp": imp, "vat": vat, "due": due, "qty": sum(q for _, q, _ in lines), "lines": len(lines)}


def _contract(seed: int) -> tuple[Attachment, str, dict]:
    rng = Rng(seed, "pdf.contract")
    a, b = rng.sample(["Alfa Servizi Srl", "Beta Logistica SpA", "Gamma Software Srl", "Delta Energia SpA"], 2)
    years, monthly = rng.choice([2, 3, 4, 5]), rng.randint(10, 80) * 100
    notice, penalty = rng.choice([30, 60, 90]), rng.choice([10, 15, 20])
    court = rng.choice(["Milano", "Torino", "Bologna", "Napoli", "Verona"])
    page1 = ["CONTRATTO DI FORNITURA DI SERVIZI", "", f"tra {a} (il Fornitore) e {b} (il Cliente).", "", "Art. 1 - Oggetto", "Il Fornitore presta al Cliente i servizi descritti nell'allegato tecnico.", "",
             "Art. 2 - Durata", f"Il contratto dura {years} anni dalla data di firma e non si rinnova tacitamente.", "", "Art. 3 - Corrispettivo", f"Il Cliente paga un corrispettivo mensile di {eur(monthly)} EUR, entro il giorno 10 di ogni mese."]
    page2 = ["Art. 4 - Recesso", f"Ciascuna parte può recedere con un preavviso scritto di {notice} giorni.", "", "Art. 5 - Penale", f"In caso di recesso senza il preavviso previsto si applica una penale pari al {penalty}% del corrispettivo annuo.", "",
             "Art. 6 - Foro competente", f"Per ogni controversia è competente in via esclusiva il Foro di {court}.", "", "Letto, approvato e sottoscritto.", "Il Fornitore ______________   Il Cliente ______________"]
    attachment, text = _attach("contratto.pdf", [page1, page2], "Contratto di fornitura")
    return attachment, text, {"notice": notice, "penalty": penalty, "years": years, "monthly": monthly, "court": court}


def _report(seed: int) -> tuple[Attachment, str, dict]:
    rng = Rng(seed, "pdf.report")
    regions = ["Nord", "Centro", "Sud", "Isole"]
    while True:
        values = {r: [rng.randint(10, 90) * 10 for _ in range(4)] for r in regions}
        fourth = sorted((v[3] for v in values.values()), reverse=True)
        if fourth[0] > fourth[1]:
            break  # one area is clearly first in the last quarter
    rows = [["Area", "T1", "T2", "T3", "T4"]] + [[r, *map(str, values[r])] for r in regions]
    year_total = sum(sum(v) for v in values.values())
    page1 = ["REPORT TRIMESTRALE DEI RICAVI 2026", "", "Ricavi per area, in migliaia di euro.", "", *pdfgen.table_lines(rows), "", "Le cifre sono al netto dell'IVA.",
             "Il commento della direzione è nella pagina seguente."]
    page2 = ["COMMENTO DELLA DIREZIONE", "", "Il quarto trimestre conferma la tendenza dell'anno.", "Le aree con i risultati migliori restano oggetto di investimento.", f"Ricavi complessivi dell'anno: {year_total} migliaia di euro."]
    attachment, text = _attach("report-ricavi.pdf", [page1, page2], "Report ricavi")
    best_q4 = max(regions, key=lambda r: values[r][3])
    ties = sum(1 for r in regions if values[r][3] == values[best_q4][3])
    return attachment, text, {"values": values, "regions": regions, "best_q4": best_q4, "unique_best": ties == 1}


def _ask(n: int, title: str, name_pages: tuple[str, int], text: str, attachment: Attachment, ask: str, line: str, check: dict, expected: str, reference: str, wrong: list[str], difficulty: int = 2) -> Question:
    prompt = _prompt(name_pages[0], name_pages[1], text, ask, line)
    return make("pdf", n, title, prompt, check, expected, reference, wrong=wrong, attachments=(attachment,), max_tokens=500, difficulty=difficulty)


def build(seed: int) -> list[Question]:
    out: list[Question] = []
    inv, inv_text, i = _invoice(seed)
    con, con_text, c = _contract(seed)
    rep, rep_text, r = _report(seed)

    out.append(_ask(1, "Totale della fattura", ("fattura.pdf", 2), inv_text, inv, "Qual è il totale da pagare della fattura, in euro?", IT_NUM, {"type": "number", "value": i["total"]}, eur(i["total"]),
                    f"Risposta: {eur(i['total'])}", wrong_numbers(i["total"], i["imp"], i["vat"], i["total"] + 100, fmt=eur), difficulty=1))
    out.append(_ask(2, "Preavviso di recesso", ("contratto.pdf", 2), con_text, con, "Con quanti giorni di preavviso si può recedere dal contratto?", IT_NUM, {"type": "number", "value": c["notice"]}, str(c["notice"]),
                    f"Risposta: {c['notice']}", wrong_numbers(c["notice"], c["years"], c["penalty"], c["notice"] + 30, 15), difficulty=1))
    due_names = date_names(i["due"])
    out.append(_ask(3, "Scadenza della fattura", ("fattura.pdf", 2), inv_text, inv, "Entro quale data va pagata la fattura?", "Rispondi con una sola riga nel formato «Risposta: gg/mm/aaaa».",
                    {"type": "alias", "answers": due_names, "decoys": []}, due_names[0], f"Risposta: {due_names[0]}",
                    [f"Risposta: {date_names(i['due'] + timedelta(days=1))[0]}", f"Risposta: {date_names(i['due'] - timedelta(days=30))[0]}", f"Risposta: {date_names(i['due'] + timedelta(days=30))[0]}"]))
    q3_region, q3_col = r["regions"][1], 2
    val = r["values"][q3_region][q3_col]
    out.append(_ask(4, "Ricavi di un'area", ("report-ricavi.pdf", 2), rep_text, rep, f"Quanti ricavi, in migliaia di euro, ha fatto l'area {q3_region} nel terzo trimestre (T3)?", IT_NUM, {"type": "number", "value": val}, str(val),
                    f"Risposta: {val}", wrong_numbers(val, r["values"][q3_region][1], r["values"][q3_region][3], r["values"][r["regions"][0]][q3_col], val + 10)))
    best = r["best_q4"]
    out.append(_ask(5, "L'area migliore del T4", ("report-ricavi.pdf", 2), rep_text, rep, "Quale area ha avuto i ricavi più alti nel quarto trimestre (T4)?", IT_LINE,
                    {"type": "alias", "answers": [best], "decoys": [x for x in r["regions"] if x != best]}, best, f"Risposta: {best}", [f"Risposta: {x}" for x in r["regions"] if x != best][:3]))
    out.append(_ask(6, "Penale per recesso anticipato", ("contratto.pdf", 2), con_text, con, "Quale percentuale del corrispettivo annuo è la penale per il recesso senza preavviso?", IT_NUM, {"type": "number", "value": c["penalty"]}, str(c["penalty"]),
                    f"Risposta: {c['penalty']}", wrong_numbers(c["penalty"], c["notice"], c["penalty"] + 5, c["years"], 8), difficulty=2))
    out.append(_ask(7, "Quantità totale degli articoli", ("fattura.pdf", 2), inv_text, inv, "Quante unità in tutto (sommando le quantità di tutti gli articoli) sono fatturate?", IT_NUM, {"type": "number", "value": i["qty"]}, str(i["qty"]),
                    f"Risposta: {i['qty']}", wrong_numbers(i["qty"], i["lines"], i["qty"] + 1, i["qty"] - 1), difficulty=2))
    out.append(_ask(8, "Caparra non prevista", ("contratto.pdf", 2), con_text, con, "Qual è l'importo della caparra confirmatoria che il Cliente deve versare? Se il documento non lo dice, dillo chiaramente e non inventare nulla.", "",
                    {"type": "abstain", "mode": "must_abstain", "forbid": [r"(?i)caparra[^.\n]{0,60}\d", r"(?i)\d[\d.,]*\s*(?:€|euro|eur)[^.\n]{0,40}caparra"]}, "Il contratto non prevede nessuna caparra.",
                    "Il contratto non prevede alcuna caparra confirmatoria: non c'è nessuna clausola che ne indichi l'importo.",
                    [f"La caparra confirmatoria è di {eur(c['monthly'])} euro.", f"Non lo so con precisione, ma la caparra dovrebbe essere di {c['monthly'] * 2} euro.", "Il Cliente versa una caparra pari al 10% del corrispettivo annuo."], difficulty=3))
    return out
