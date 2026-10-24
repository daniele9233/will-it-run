"""Enterprise documents: a purchase order, minutes, an SLA summary, a post-mortem, a quote, a runbook, a checklist, a credit
note, a status report and a change request, each written from data in the prompt.

The data are generated from the seed. A document is checked piece by piece: the sections it must have and their order, the
facts it must carry, the table and the totals it must compute, the numbers it must not invent and the placeholders it must not leave.
"""

from __future__ import annotations

from agentfit.arena.bank.kit import make
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng

_RULE = "Usa solo i dati forniti: non inventare numeri, nomi o date e non lasciare segnaposto. Scrivi in italiano, in Markdown."
_SUPPLIERS = ["Rossi Forniture Srl", "Bianchi Ufficio SpA", "Verdi Informatica Snc", "Neri Arredi Srl", "Gallo Servizi Srl"]
_CITIES = [("via Roma", "Milano"), ("corso Italia", "Torino"), ("via Garibaldi", "Bologna"), ("viale dei Mille", "Firenze"), ("via Appia", "Roma")]
_PEOPLE = ["Marta Ferri", "Luca Bernardi", "Giulia Conti", "Paolo Serra", "Elena Vitale", "Davide Longo", "Chiara Monti", "Stefano Greco"]
_ITEMS = [("Tastiera meccanica", 25, 60), ("Monitor 27 pollici", 150, 300), ("Webcam HD", 30, 70), ("Cuffie con microfono", 40, 90), ("Hub USB-C", 20, 45),
          ("Sedia ergonomica", 120, 250), ("Scrivania regolabile", 250, 450), ("Lampada da tavolo", 20, 40), ("Stampante laser", 90, 180)]


def eur(value: float) -> str:
    whole, cents = divmod(round(value * 100), 100)
    return f"{whole:,}".replace(",", ".") + f",{cents:02d}"


def table(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows])


def _doc(n: int, title: str, prompt: str, spec: dict, reference: str, wrong: list[str], difficulty: int = 2) -> Question:
    check = {"type": "document", **spec}
    return make("documents", n, title, f"{prompt}\n\n{_RULE}", check, "sezioni, fatti, tabelle e totali come da richiesta, nessun numero inventato", reference, wrong=wrong, max_tokens=1600, difficulty=difficulty)


def _lines(rng: Rng, count: int) -> list[tuple[str, int, int]]:
    items = rng.sample(_ITEMS, count)
    return [(name, rng.randint(1, 6), rng.randint(lo // 5, hi // 5) * 5) for name, lo, hi in items]


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    # 1. Purchase order.
    rng = Rng(seed, "documents.001")
    po, supplier = f"ORD-2026-{rng.randint(1000, 9999)}", rng.choice(_SUPPLIERS)
    street, city = rng.choice(_CITIES)
    civic = rng.randint(2, 98)
    lines = _lines(rng, 3)
    terms = rng.choice([30, 60])
    subtotal = sum(q * p for _, q, p in lines)
    vat = round(subtotal * 0.22, 2)
    total = round(subtotal + vat, 2)
    rows = [[name, str(q), eur(p), eur(q * p)] for name, q, p in lines]
    data = "\n".join(f"- {name}: quantità {q}, prezzo unitario {eur(p)} €" for name, q, p in lines)
    reference = (f"# Ordine d'acquisto {po}\n\n## Fornitore\n{supplier}, {street} {civic}, {city}\n\n## Articoli\n{table(['Articolo', 'Quantità', 'Prezzo unitario', 'Totale'], rows)}\n\n"
                 f"## Riepilogo\nImponibile: {eur(subtotal)} €\nIVA 22%: {eur(vat)} €\nTotale ordine: {eur(total)} €\n\n## Condizioni\nPagamento a {terms} giorni dalla data della fattura.\n")
    spec = {"sections": ["Fornitore", "Articoli", "Riepilogo", "Condizioni"], "facts": [supplier, po], "no_placeholders": True,
            "tables": [{"columns": ["Articolo", "Quantità", "Prezzo unitario", "Totale"], "min_rows": 3, "column_values": {"Totale": [q * p for _, q, p in lines]}}],
            "totals": [{"label": "Imponibile", "value": subtotal}, {"label": "IVA", "value": vat}, {"label": "Totale ordine", "value": total}],
            "numbers_allowed": [2026, int(po[-4:]), civic, 22, terms, subtotal, vat, total, *[v for _, q, p in lines for v in (q, p, q * p)]]}
    out.append(_doc(1, "Ordine d'acquisto", f"Redigi l'ordine d'acquisto numero {po} al fornitore {supplier}, {street} {civic}, {city}.\nArticoli:\n{data}\nL'IVA è il 22 %. Pagamento a {terms} giorni dalla data della fattura.\n\n"
                    "Il documento ha queste sezioni, in quest'ordine: «Fornitore»; «Articoli» (una tabella con le colonne Articolo, Quantità, Prezzo unitario, Totale); «Riepilogo» (imponibile, IVA e totale ordine); «Condizioni».",
                    spec, reference, [reference.replace("## Condizioni\n", "").replace(f"Pagamento a {terms} giorni dalla data della fattura.\n", ""), reference.replace(f"Totale ordine: {eur(total)} €", f"Totale ordine: {eur(subtotal)} €"),
                                      reference + "\nSpedizione: 15,00 €, consegna in 48 ore.\n", reference.replace(supplier + ",", "[inserire fornitore],")], difficulty=1))

    # 2. Minutes.
    rng = Rng(seed, "documents.002")
    day, month = rng.randint(2, 27), rng.randint(1, 12)
    when = f"{day:02d}/{month:02d}/2026"
    people = rng.sample(_PEOPLE, 4)
    budget = rng.randint(4, 20) * 1000
    chair = people[0]
    decisions = [f"Approvato il budget di {eur(budget)} € per il progetto Atlas.", "Rinviata a settembre la migrazione del server di posta.", f"Affidato a {people[1]} il coordinamento del collaudo."]
    due = [f"{rng.randint(2, 27):02d}/{rng.randint(1, 12):02d}/2026" for _ in range(3)]
    actions = [("Preparare il piano di collaudo", people[1], due[0]), ("Inviare il budget all'amministrazione", people[2], due[1]), ("Aggiornare il calendario della migrazione", people[3], due[2])]
    reference = (f"# Verbale della riunione del {when}\n\nPresiede {chair}.\n\n## Partecipanti\n" + "\n".join(f"- {p}" for p in people) + "\n\n## Decisioni\n" + "\n".join(f"{i}. {d}" for i, d in enumerate(decisions, 1))
                 + f"\n\n## Azioni\n{table(['Azione', 'Responsabile', 'Scadenza'], [list(a) for a in actions])}\n")
    spec = {"sections": ["Partecipanti", "Decisioni", "Azioni"], "facts": [*people, when, "Atlas", *due], "no_placeholders": True,
            "tables": [{"columns": ["Azione", "Responsabile", "Scadenza"], "min_rows": 3}], "numbers_allowed": [2026, budget, day, month, 1, 2, 3, *[int(x) for d in due for x in d.split("/")]], "ignore_up_to": 31}
    out.append(_doc(2, "Verbale di riunione", f"Redigi il verbale della riunione del {when}, presieduta da {chair}. Partecipanti: {', '.join(people)}.\nDecisioni prese:\n" + "\n".join(f"- {d}" for d in decisions)
                    + "\nAzioni assegnate:\n" + "\n".join(f"- {a}: {r}, entro il {d}" for a, r, d in actions)
                    + "\n\nSezioni, in quest'ordine: «Partecipanti» (elenco puntato); «Decisioni» (elenco numerato); «Azioni» (tabella con le colonne Azione, Responsabile, Scadenza).", spec, reference,
                    [reference.replace("## Decisioni", "## Note"), reference.replace(people[2], "Mario Rossi"), reference.replace(due[0], "[data da definire]"), reference + "\nProssima riunione: 19/11/2026 alle 16:45.\n"], difficulty=1))

    # 3. SLA summary.
    rng = Rng(seed, "documents.003")
    target = rng.choice([99.5, 99.9, 99.95])
    measured = round(target + rng.choice([0.02, 0.03, 0.04, 0.05]), 2)
    gap = round(measured - target, 2)
    p1, p2, p3 = rng.choice([(15, 60, 480), (30, 120, 480), (10, 45, 240)])
    month_name = rng.choice(["marzo", "aprile", "maggio", "ottobre"])
    reference = (f"# Riepilogo SLA di {month_name} 2026\n\n## Obiettivi\n{table(['Metrica', 'Obiettivo'], [['Disponibilità mensile', f'{eur(target)} %'], ['Tempo di risposta P1', f'{p1} minuti'], ['Tempo di risposta P2', f'{p2} minuti'], ['Tempo di risposta P3', f'{p3} minuti']])}\n\n"
                 f"## Misure del mese\nDisponibilità misurata: {eur(measured)} %.\nScarto rispetto all'obiettivo: {eur(gap)} punti percentuali.\n\n## Esito\nL'obiettivo di disponibilità è stato rispettato.\n")
    spec = {"sections": ["Obiettivi", "Misure del mese", "Esito"], "facts": ["Disponibilità", "P1", "P2", "P3"], "no_placeholders": True,
            "tables": [{"columns": ["Metrica", "Obiettivo"], "min_rows": 4}], "totals": [{"label": "Scarto", "value": gap, "tolerance": 0.001}],
            "numbers_allowed": [2026, target, measured, gap, p1, p2, p3, 1, 2, 3], "ignore_up_to": 0}
    out.append(_doc(3, "Riepilogo SLA", f"Redigi il riepilogo SLA di {month_name} 2026. Obiettivi: disponibilità mensile {eur(target)} %; tempo di risposta P1 {p1} minuti, P2 {p2} minuti, P3 {p3} minuti. "
                    f"Disponibilità misurata nel mese: {eur(measured)} %. Calcola lo scarto, in punti percentuali, rispetto all'obiettivo di disponibilità e dì se l'obiettivo è stato rispettato.\n\n"
                    "Sezioni, in quest'ordine: «Obiettivi» (tabella con le colonne Metrica e Obiettivo, una riga per ogni obiettivo); «Misure del mese» (disponibilità misurata e scarto); «Esito».", spec, reference,
                    [reference.replace(f"Scarto rispetto all'obiettivo: {eur(gap)}", f"Scarto rispetto all'obiettivo: {eur(gap + 0.1)}"), reference.replace("## Esito", "## Conclusione"), reference.replace(f"{p2} minuti", f"{p2 + 15} minuti")], difficulty=2))

    # 4. Post-mortem.
    rng = Rng(seed, "documents.004")
    start_h, start_m = rng.randint(8, 16), rng.choice([0, 5, 10, 15, 20, 30, 40, 45])
    minutes = rng.choice([45, 60, 75, 90, 105, 120])
    end_total = start_h * 60 + start_m + minutes
    t0, t1, t2 = f"{start_h:02d}:{start_m:02d}", f"{(start_h * 60 + start_m + 10) // 60:02d}:{(start_h * 60 + start_m + 10) % 60:02d}", f"{end_total // 60:02d}:{end_total % 60:02d}"
    cause = rng.choice(["un certificato scaduto sul bilanciatore", "una migrazione del database senza indice", "un deploy con una variabile d'ambiente mancante"])
    reference = (f"# Post-mortem dell'incidente\n\n## Sintesi\nIl servizio è stato indisponibile per {minutes} minuti a causa di {cause}.\n\n## Cronologia\n"
                 f"{table(['Ora', 'Evento'], [[t0, 'Inizio del disservizio'], [t1, 'Allarme ricevuto dal team'], [t2, 'Servizio ripristinato']])}\n\n## Causa radice\nLa causa è {cause}.\n\n"
                 f"## Azioni correttive\n1. Aggiungere un controllo automatico che prevenga il problema.\n2. Rivedere la procedura di rilascio.\n\nDurata del disservizio: {minutes} minuti.\n")
    spec = {"sections": ["Sintesi", "Cronologia", "Causa radice", "Azioni correttive"], "facts": [t0, t1, t2, cause], "no_placeholders": True,
            "tables": [{"columns": ["Ora", "Evento"], "min_rows": 3}], "totals": [{"label": "Durata", "value": minutes}],
            "numbers_allowed": [minutes, int(t0[:2]), int(t0[3:]), int(t1[:2]), int(t1[3:]), int(t2[:2]), int(t2[3:]), 1, 2], "ignore_up_to": 0}
    out.append(_doc(4, "Post-mortem", f"Redigi il post-mortem di un incidente. Cronologia: alle {t0} inizia il disservizio; alle {t1} il team riceve l'allarme; alle {t2} il servizio è ripristinato. "
                    f"Causa: {cause}. Calcola la durata del disservizio in minuti, dall'inizio al ripristino.\n\nSezioni, in quest'ordine: «Sintesi»; «Cronologia» (tabella con le colonne Ora ed Evento); «Causa radice»; «Azioni correttive» (elenco numerato di due azioni sensate); "
                    "in fondo una riga «Durata del disservizio: N minuti».", spec, reference,
                    [reference.replace(f"Durata del disservizio: {minutes} minuti.", f"Durata del disservizio: {minutes + 15} minuti."), reference.replace("## Causa radice", "## Perché"), reference.replace(t1, "09:99")], difficulty=3))

    # 5. Quote with a discount.
    rng = Rng(seed, "documents.005")
    lines = _lines(rng, 3)
    pct = rng.choice([5, 10, 15])
    subtotal = sum(q * p for _, q, p in lines)
    discount = round(subtotal * pct / 100, 2)
    taxable = round(subtotal - discount, 2)
    vat = round(taxable * 0.22, 2)
    total = round(taxable + vat, 2)
    client = rng.choice(["Studio Colombo", "Ricci & Partners", "Fontana Logistica", "Marini Costruzioni"])
    rows = [[name, str(q), eur(p), eur(q * p)] for name, q, p in lines]
    reference = (f"# Preventivo per {client}\n\n## Voci\n{table(['Voce', 'Quantità', 'Prezzo unitario', 'Totale'], rows)}\n\n## Calcolo\nSubtotale: {eur(subtotal)} €\nSconto {pct}%: {eur(discount)} €\n"
                 f"Imponibile: {eur(taxable)} €\nIVA 22%: {eur(vat)} €\nTotale preventivo: {eur(total)} €\n\n## Validità\nIl preventivo è valido per 30 giorni.\n")
    spec = {"sections": ["Voci", "Calcolo", "Validità"], "facts": [client], "no_placeholders": True, "tables": [{"columns": ["Voce", "Quantità", "Prezzo unitario", "Totale"], "min_rows": 3, "column_values": {"Totale": [q * p for _, q, p in lines]}}],
            "totals": [{"label": "Subtotale", "value": subtotal}, {"label": "Sconto", "value": discount}, {"label": "Imponibile", "value": taxable}, {"label": "IVA", "value": vat}, {"label": "Totale preventivo", "value": total}],
            "numbers_allowed": [pct, 22, 30, subtotal, discount, taxable, vat, total, *[v for _, q, p in lines for v in (q, p, q * p)]]}
    out.append(_doc(5, "Preventivo con sconto", f"Redigi un preventivo per {client}. Voci:\n" + "\n".join(f"- {name}: quantità {q}, prezzo unitario {eur(p)} €" for name, q, p in lines)
                    + f"\nSi applica uno sconto del {pct} % sul subtotale, poi l'IVA al 22 % sull'imponibile scontato. Il preventivo è valido 30 giorni.\n\nSezioni, in quest'ordine: «Voci» (tabella con le colonne Voce, Quantità, Prezzo unitario, Totale); "
                    "«Calcolo» (subtotale, sconto, imponibile, IVA, totale preventivo); «Validità».", spec, reference,
                    [reference.replace(f"Totale preventivo: {eur(total)} €", f"Totale preventivo: {eur(round(subtotal * 1.22, 2))} €"), reference.replace(f"Imponibile: {eur(taxable)} €", f"Imponibile: {eur(subtotal)} €"), reference.replace("## Validità", "## Note")], difficulty=3))

    # 6. Runbook.
    rng = Rng(seed, "documents.006")
    service, version = rng.choice(["app-orders", "app-billing", "app-reports"]), f"2.{rng.randint(1, 9)}.{rng.randint(0, 9)}"
    port = rng.choice([8080, 8443, 9000])
    commands = [f"systemctl stop {service}", f"cp -r /opt/{service} /opt/{service}.bak", f"tar -xzf {service}-{version}.tar.gz -C /opt/{service}", f"systemctl start {service}", f"curl -fsS http://localhost:{port}/health"]
    reference = (f"# Procedura di aggiornamento di {service}\n\n## Scopo\nAggiornare {service} alla versione {version}.\n\n## Prerequisiti\n- Accesso come amministratore al server.\n- Il pacchetto {service}-{version}.tar.gz già copiato sul server.\n\n"
                 "## Procedura\n" + "\n".join(f"{i}. `{c}`" for i, c in enumerate(commands[:4], 1)) + f"\n\n## Verifica\n1. `{commands[4]}`\n\n## Rollback\n1. `systemctl stop {service}`\n2. Ripristinare la copia di sicurezza in /opt/{service}.bak.\n3. `systemctl start {service}`\n")
    spec = {"sections": ["Scopo", "Prerequisiti", "Procedura", "Verifica", "Rollback"], "facts": [service, version, *commands], "no_placeholders": True,
            "numbers_allowed": [2, int(version.split(".")[1]), int(version.split(".")[2]), port, 1, 2, 3, 4, 5], "ignore_up_to": 0}
    out.append(_doc(6, "Runbook di aggiornamento", f"Redigi un runbook per aggiornare il servizio {service} alla versione {version}. Comandi da usare, nell'ordine: " + "; ".join(f"`{c}`" for c in commands[:4])
                    + f". Per verificare il servizio: `{commands[4]}`. Il rollback ferma il servizio, ripristina la copia in /opt/{service}.bak e lo riavvia.\n\nSezioni, in quest'ordine: «Scopo»; «Prerequisiti» (elenco puntato); «Procedura» (elenco numerato con i comandi); «Verifica»; «Rollback».",
                    spec, reference, [reference.replace("## Rollback", "## Annullamento"), reference.replace(commands[2], "tar -xf release.tar -C /opt"), reference.replace(f"`{commands[3]}`", "`[riavviare il servizio]`")], difficulty=2))

    # 7. Onboarding checklist.
    rng = Rng(seed, "documents.007")
    newcomer, buddy = rng.sample(_PEOPLE, 2)
    first = rng.sample(["Consegna del portatile", "Creazione dell'account aziendale", "Presentazione al team", "Corso sulla sicurezza informatica", "Accesso al repository del codice", "Primo incontro con il responsabile"], 6)
    reference = (f"# Checklist di onboarding per {newcomer}\n\nTutor: {buddy}.\n\n## Prima del primo giorno\n- [ ] {first[0]}\n- [ ] {first[1]}\n\n## Primo giorno\n- [ ] {first[2]}\n- [ ] {first[3]}\n\n## Prima settimana\n- [ ] {first[4]}\n- [ ] {first[5]}\n")
    spec = {"sections": ["Prima del primo giorno", "Primo giorno", "Prima settimana"], "facts": [newcomer, buddy, *first], "no_placeholders": True, "numbers_allowed": []}
    out.append(_doc(7, "Checklist di onboarding", f"Redigi la checklist di onboarding per {newcomer}, con tutor {buddy}. Attività: prima del primo giorno: {first[0]}; {first[1]}. Il primo giorno: {first[2]}; {first[3]}. Nella prima settimana: {first[4]}; {first[5]}.\n\n"
                    "Sezioni, in quest'ordine: «Prima del primo giorno»; «Primo giorno»; «Prima settimana». Ogni attività è una voce di elenco con casella da spuntare (`- [ ]`).", spec, reference,
                    [reference.replace(f"- [ ] {first[3]}\n", ""), reference.replace("## Primo giorno", "## Giorno 1"), reference.replace(buddy, "[nome del tutor]")], difficulty=1))

    # 8. Credit note.
    rng = Rng(seed, "documents.008")
    inv = f"FT-2026-{rng.randint(100, 999)}"
    lines = _lines(rng, 2)
    imp = sum(q * p for _, q, p in lines)
    vat = round(imp * 0.22, 2)
    total = round(imp + vat, 2)
    client = rng.choice(["Studio Colombo", "Ricci & Partners", "Fontana Logistica"])
    rows = [[name, str(q), eur(p), eur(q * p)] for name, q, p in lines]
    reference = (f"# Nota di credito relativa alla fattura {inv}\n\n## Cliente\n{client}\n\n## Righe stornate\n{table(['Articolo', 'Quantità', 'Prezzo unitario', 'Totale'], rows)}\n\n"
                 f"## Importi\nImponibile da stornare: {eur(imp)} €\nIVA 22%: {eur(vat)} €\nTotale nota di credito: {eur(total)} €\n\n## Motivo\nResa della merce.\n")
    spec = {"sections": ["Cliente", "Righe stornate", "Importi", "Motivo"], "facts": [inv, client], "no_placeholders": True,
            "tables": [{"columns": ["Articolo", "Quantità", "Prezzo unitario", "Totale"], "min_rows": 2, "column_values": {"Totale": [q * p for _, q, p in lines]}}],
            "totals": [{"label": "Imponibile da stornare", "value": imp}, {"label": "IVA", "value": vat}, {"label": "Totale nota di credito", "value": total}],
            "numbers_allowed": [2026, int(inv[-3:]), 22, imp, vat, total, *[v for _, q, p in lines for v in (q, p, q * p)]]}
    out.append(_doc(8, "Nota di credito", f"Redigi la nota di credito per il cliente {client} relativa alla fattura {inv}, per la resa di questi articoli:\n" + "\n".join(f"- {name}: quantità {q}, prezzo unitario {eur(p)} €" for name, q, p in lines)
                    + "\nL'IVA è il 22 %. Il motivo è la resa della merce.\n\nSezioni, in quest'ordine: «Cliente»; «Righe stornate» (tabella con le colonne Articolo, Quantità, Prezzo unitario, Totale); «Importi» (imponibile da stornare, IVA, totale nota di credito); «Motivo».",
                    spec, reference, [reference.replace(f"Totale nota di credito: {eur(total)} €", f"Totale nota di credito: {eur(imp)} €"), reference.replace(inv, "FT-2026-000"), reference.replace("## Motivo\nResa della merce.\n", "")], difficulty=2))

    # 9. Status report.
    rng = Rng(seed, "documents.009")
    projects = []
    for name in ["Atlas", "Borea", "Cometa"]:
        budget = rng.randint(4, 12) * 10_000
        spent = rng.randint(2, int(budget / 10_000) * 10 - 1) * 1000
        progress = rng.choice([20, 35, 50, 65, 80, 95])
        projects.append((name, progress, budget, spent, round(spent / budget * 100, 1)))
    statuses = ["Verde" if p[4] <= p[1] + 5 else "Rosso" for p in projects]
    rows = [[p[0], f"{p[1]} %", eur(p[3]), f"{eur(p[4])[:-1] if eur(p[4]).endswith('0') else eur(p[4])} %", s] for p, s in zip(projects, statuses, strict=True)]
    reference = (f"# Stato dei progetti\n\n## Sintesi\nTre progetti in corso: {statuses.count('Verde')} in verde e {statuses.count('Rosso')} in rosso.\n\n## Stato dei progetti\n"
                 f"{table(['Progetto', 'Avanzamento', 'Speso', 'Budget speso', 'Stato'], rows)}\n\n## Rischi\nI progetti in rosso hanno speso più budget di quanto sia avanzato il lavoro.\n")
    spec = {"sections": ["Sintesi", "Stato dei progetti", "Rischi"], "facts": [p[0] for p in projects], "no_placeholders": True,
            "tables": [{"columns": ["Progetto", "Avanzamento", "Speso", "Budget speso", "Stato"], "min_rows": 3, "column_values": {"Budget speso": [p[4] for p in projects], "Speso": [p[3] for p in projects]}}],
            "numbers_allowed": [3, statuses.count("Verde"), statuses.count("Rosso"), *[v for p in projects for v in (p[1], p[2], p[3], p[4])]]}
    out.append(_doc(9, "Rapporto di stato dei progetti", "Redigi il rapporto di stato di tre progetti. Dati (avanzamento del lavoro, budget totale, importo speso):\n"
                    + "\n".join(f"- {p[0]}: avanzamento {p[1]} %, budget {eur(p[2])} €, speso {eur(p[3])} €" for p in projects)
                    + "\nCalcola per ciascun progetto la percentuale di budget speso (con un decimale). Lo stato è «Verde» se la percentuale di budget speso supera l'avanzamento di al massimo 5 punti, altrimenti «Rosso».\n\n"
                    "Sezioni, in quest'ordine: «Sintesi»; «Stato dei progetti» (tabella con le colonne Progetto, Avanzamento, Speso, Budget speso, Stato); «Rischi».", spec, reference,
                    [reference.replace(rows[0][3], "99,9 %"), reference.replace("## Rischi", "## Note"), reference.replace(projects[1][0], "Zeta")], difficulty=3))

    # 10. Change request.
    rng = Rng(seed, "documents.010")
    rfc = f"RFC-{rng.randint(100, 999)}"
    requester, approver1, approver2 = rng.sample(_PEOPLE, 3)
    d, m = rng.randint(2, 27), rng.randint(1, 12)
    window = f"{d:02d}/{m:02d}/2026"
    risk = rng.choice(["basso", "medio", "alto"])
    change = rng.choice(["l'aggiornamento del firewall perimetrale", "la sostituzione dello storage del database", "il passaggio del DNS al nuovo provider"])
    reference = (f"# Richiesta di modifica {rfc}\n\nRichiedente: {requester}. Finestra di intervento: {window}. Rischio: {risk}.\n\n## Descrizione\nSi richiede {change}.\n\n## Impatto\nÈ previsto un fermo breve dei servizi interessati durante la finestra.\n\n"
                 f"## Piano di rollback\nSe il controllo finale fallisce, si ripristina la configurazione precedente.\n\n## Approvazioni\n- {approver1}\n- {approver2}\n")
    spec = {"sections": ["Descrizione", "Impatto", "Piano di rollback", "Approvazioni"], "facts": [rfc, requester, window, risk, approver1, approver2], "no_placeholders": True,
            "numbers_allowed": [2026, int(rfc[4:]), d, m], "ignore_up_to": 0}
    out.append(_doc(10, "Richiesta di modifica", f"Redigi la richiesta di modifica {rfc}. Richiedente: {requester}. Oggetto: {change}. Finestra di intervento: {window}. Livello di rischio: {risk}. Approvatori: {approver1} e {approver2}.\n\n"
                    "Sezioni, in quest'ordine: «Descrizione»; «Impatto»; «Piano di rollback»; «Approvazioni» (elenco puntato). Indica in cima richiedente, finestra di intervento e rischio.", spec, reference,
                    [reference.replace("## Piano di rollback", "## Ripristino"), reference.replace(window, "[data]"), reference.replace(f"Rischio: {risk}", "Rischio: critico, 99 clienti coinvolti")], difficulty=2))
    return out
