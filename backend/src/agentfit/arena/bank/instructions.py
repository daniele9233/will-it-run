"""Following instructions to the letter: a number of words, a letter that must not appear, a title, a list of exactly so many
lines. Every rule is checked by counting, so the verdict says which rule was broken. A reply that keeps four rules out of five
is a partial score, not a pass.
"""

from __future__ import annotations

from agentfit.arena.bank.kit import make
from agentfit.arena.model import Question
from agentfit.arena.rng import Rng


def _q(n: int, title: str, prompt: str, rules: list[dict], reference: str, wrong: list[str], difficulty: int = 2) -> Question:
    return make("instructions", n, title, prompt, {"type": "rules", "rules": rules}, "Rispettare tutte le regole: " + "; ".join(_describe(r) for r in rules), reference, wrong=wrong, max_tokens=500, difficulty=difficulty)


def _describe(rule: dict) -> str:
    kind = rule["kind"]
    if kind in ("words", "sentences", "paragraphs", "lines", "bullets", "chars"):
        wanted = f"esattamente {rule['equals']}" if "equals" in rule else f"da {rule['min']} a {rule['max']}" if "min" in rule and "max" in rule else f"almeno {rule['min']}" if "min" in rule else f"al massimo {rule['max']}"
        return f"{kind} {wanted}"
    if kind in ("includes", "excludes"):
        return f"{'contiene' if kind == 'includes' else 'non contiene'} {', '.join(rule['words'])}"
    if kind in ("starts_with", "ends_with"):
        return f"{'comincia con' if kind == 'starts_with' else 'finisce con'} {rule['text']}"
    if kind == "word_count_of":
        return f"«{rule['word']}» esattamente {rule['equals']} volte"
    if kind == "no_letter":
        return f"mai la lettera {rule['letter']}"
    if kind == "placeholders":
        return f"esattamente {rule['equals']} segnaposto"
    if kind == "language":
        return "in inglese" if rule["lang"] == "en" else "in italiano"
    return {"uppercase": "tutto maiuscolo", "lowercase": "tutto minuscolo", "no_commas": "senza virgole", "title": "con un titolo <<così>>", "regex": "formato richiesto"}.get(kind, kind)


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    # 1. An exact number of words.
    rng = Rng(seed, "instructions.001")
    words, sentence = rng.choice([
        (6, "Il gatto dorme sul divano rosso."), (8, "Domani mattina andremo al mercato a comprare frutta."), (10, "Sul lago il vento porta il profumo dei tigli fioriti."),
    ])
    out.append(_q(1, "Un numero esatto di parole", f"Scrivi una frase di esattamente {words} parole, su un argomento a tua scelta. Rispondi solo con la frase.",
                  [{"kind": "words", "equals": words}, {"kind": "sentences", "equals": 1}], sentence,
                  ["Oggi il sole splende alto nel cielo azzurro sopra la città vecchia.", "Il gatto dorme.", f"Ecco la frase richiesta: {sentence}"], difficulty=1))

    # 2. Capitals, no commas, one sentence.
    rng = Rng(seed, "instructions.002")
    topic, sentence = rng.choice([
        ("il mare", "IL MARE È UNA GRANDE DISTESA DI ACQUA SALATA CHE NON STA MAI FERMA."), ("la montagna", "LA MONTAGNA SI ALZA SOPRA LE NUVOLE E CUSTODISCE UN SILENZIO ANTICO."),
        ("il fiume", "IL FIUME SCENDE DALLE MONTAGNE E PORTA L'ACQUA FINO ALLA PIANURA."),
    ])
    out.append(_q(2, "Maiuscole, senza virgole", f"Descrivi {topic} in una sola frase, scritta tutta in lettere maiuscole e senza nessuna virgola.",
                  [{"kind": "uppercase"}, {"kind": "no_commas"}, {"kind": "sentences", "max": 1}], sentence,
                  [sentence.lower(), sentence.replace(" CHE", ", CHE").replace(" E ", ", E "), sentence + " È BELLISSIMO. LO ADORO."], difficulty=1))

    # 3. A list of an exact length and nothing else.
    rng = Rng(seed, "instructions.003")
    n = rng.choice([3, 4, 5])
    things = ["Costume", "Asciugamano", "Crema solare", "Occhiali da sole", "Cappello"][:n]
    out.append(_q(3, "Un elenco di lunghezza esatta", f"Elenca {n} cose da mettere in valigia per andare al mare. Rispondi solo con un elenco puntato di {n} righe, una cosa per riga, senza introduzione né conclusione.",
                  [{"kind": "bullets", "equals": n}, {"kind": "lines", "equals": n}], "\n".join(f"- {t}" for t in things),
                  ["Ecco cosa portare:\n" + "\n".join(f"- {t}" for t in things), "\n".join(f"- {t}" for t in [*things, "Libro"]), "\n".join(f"- {t}" for t in things[:-1])], difficulty=1))

    # 4. It starts and ends as it was told.
    rng = Rng(seed, "instructions.004")
    start, end = rng.choice([("Gentile cliente,", "Cordiali saluti."), ("Caro Marco,", "A presto."), ("Egregio dottore,", "Distinti saluti.")])
    body = "la Sua richiesta è stata presa in carico e riceverà una risposta entro due giorni lavorativi."
    out.append(_q(4, "Inizio e fine prescritti", f"Scrivi un breve messaggio per rispondere a una richiesta. Deve cominciare esattamente con «{start}» e finire esattamente con «{end}».",
                  [{"kind": "starts_with", "text": start}, {"kind": "ends_with", "text": end}, {"kind": "words", "min": 8}], f"{start} {body} {end}",
                  [f"{start} {body}", f"Buongiorno, {body} {end}", f"Ecco il messaggio:\n{start} {body} {end}"]))

    # 5. A title between angle brackets and a length.
    rng = Rng(seed, "instructions.005")
    theme, text = rng.choice([
        ("il valore del tempo", "<<Il valore del tempo>>\nIl tempo è la risorsa più preziosa che abbiamo, perché non si può comprare né restituire. Chi impara a usarlo con attenzione riesce a dedicarsi alle cose importanti, alle persone care e ai propri sogni, senza rimandare tutto a un domani che non è mai sicuro."),
        ("la città di notte", "<<La città di notte>>\nQuando cala il buio, la città cambia volto: le strade si svuotano, le insegne si accendono e i rumori del giorno lasciano il posto a un silenzio gentile. Camminare sotto i lampioni, senza fretta, è un modo semplice per riscoprire i luoghi che credevamo di conoscere."),
        ("il primo giorno di scuola", "<<Il primo giorno di scuola>>\nIl primo giorno di scuola è fatto di zaini nuovi, quaderni ancora bianchi e un po' di emozione che non si riesce a nascondere. Si incontrano compagni che non si vedevano da mesi, si conoscono nuovi insegnanti e si ha la sensazione che l'anno sia pieno di cose da scoprire."),
    ])
    body_only = text.split("\n", 1)[1]
    out.append(_q(5, "Un titolo tra parentesi angolari", f"Scrivi un breve testo, tra 30 e 60 parole, sul tema «{theme}». Dagli un titolo scritto tra doppie parentesi angolari, come <<titolo>>.",
                  [{"kind": "title"}, {"kind": "words", "min": 30, "max": 60}], text,
                  [body_only, f"Titolo: {theme.capitalize()}\n{body_only}", f"{text}\n{body_only}", f"<<{theme.capitalize()}>>\n{theme.capitalize()} è importante."]))

    # 6. Three sentences, two words in and one word out.
    rng = Rng(seed, "instructions.006")
    city, keep, bad, text, wrong_text = rng.choice([
        ("Firenze", ["Arno", "Rinascimento"], "bella", "Firenze sorge lungo l'Arno, in una valle circondata da colline. Fu il cuore del Rinascimento e custodisce capolavori di Michelangelo e Botticelli. Ogni anno milioni di visitatori percorrono le sue strade.",
         "Firenze è una città bella e ricca di storia. Ha tanti musei e molte chiese. Si visita volentieri."),
        ("Venezia", ["laguna", "canali"], "romantica", "Venezia è costruita su una laguna e non ha strade per le automobili. Al posto delle vie ci sono più di cento canali, percorsi da barche e vaporetti. Il Carnevale richiama ogni anno visitatori da tutto il mondo.",
         "Venezia è una città romantica sull'acqua. Ha tanti ponti e molte calli. Si visita volentieri."),
        ("Roma", ["Colosseo", "antica"], "caotica", "Roma conserva il Colosseo, simbolo della sua storia antica. I resti dei Fori imperiali raccontano il passato dell'impero. Ogni piazza e ogni fontana nascondono una storia da scoprire.",
         "Roma è una città caotica e piena di vita. Ha tanti monumenti e molte piazze. Si visita volentieri."),
    ])
    out.append(_q(6, "Tre frasi, parole date e vietate", f"Descrivi {city} in esattamente tre frasi. Usa le parole «{keep[0]}» e «{keep[1]}» e non usare mai la parola «{bad}».",
                  [{"kind": "sentences", "equals": 3}, {"kind": "includes", "words": keep}, {"kind": "excludes", "words": [bad]}], text,
                  [text + " Non finisce qui.", text.replace(keep[0], "fiume"), wrong_text]))

    # 7. A word that must appear an exact number of times.
    rng = Rng(seed, "instructions.007")
    word, times, sentence, less, more = rng.choice([
        ("casa", 2, "La casa di Marco è più grande della casa di Luigi.", "La casa di Marco è molto grande.", "La casa di Marco, la casa di Luigi e la casa di Anna sono vicine."),
        ("sole", 3, "Il sole sorge presto, il sole scalda la terra e il sole tramonta tardi.", "Il sole sorge presto e tramonta tardi.", "Il sole sorge, il sole scalda, il sole splende e il sole tramonta."),
        ("mare", 2, "Il mare è calmo e il mare è azzurro.", "Il mare è calmo e azzurro.", "Il mare, il mare e ancora il mare."),
    ])
    out.append(_q(7, "Una parola un numero esatto di volte", f"Scrivi una frase in cui la parola «{word}» compaia esattamente {times} volte. Rispondi solo con la frase.",
                  [{"kind": "word_count_of", "word": word, "equals": times}, {"kind": "sentences", "equals": 1}], sentence, [less, more, f"{sentence} {sentence}"]))

    # 8. Placeholders.
    rng = Rng(seed, "instructions.008")
    count = rng.choice([2, 3, 4])
    marks = ["[destinatario]", "[data]", "[nome]", "[città]"][:count]
    sentence = {2: "Gentile [destinatario], con la presente comunico le mie dimissioni, con effetto dal giorno stabilito. Cordiali saluti, [nome].",
                3: "Gentile [destinatario], con la presente comunico le mie dimissioni, con effetto dal [data]. Cordiali saluti, [nome].",
                4: "[città], [data]\nGentile [destinatario], con la presente comunico le mie dimissioni, con effetto immediato. Cordiali saluti, [nome]."}[count]
    out.append(_q(8, "Segnaposto", f"Scrivi il modello di una lettera di dimissioni con esattamente {count} segnaposto tra parentesi quadre, come {marks[0]}. Rispondi solo con la lettera.",
                  [{"kind": "placeholders", "equals": count}], sentence,
                  ["Gentile responsabile, con la presente comunico le mie dimissioni. Cordiali saluti.", sentence + " [firma] [allegato]", sentence.replace("[nome]", "Mario Rossi", 1) if count > 2 else sentence + " [firma]"]))

    # 9. The language of the answer.
    rng = Rng(seed, "instructions.009")
    question, answer, italian = rng.choice([
        ("perché il cielo è blu?", "The sky is blue because air molecules scatter the short blue wavelengths of sunlight much more than the longer red ones.", "Il cielo è blu perché le molecole d'aria diffondono la luce blu del Sole più di quella rossa."),
        ("perché il mare è salato?", "The sea is salty because rivers carry minerals from rocks into it, and the water that evaporates leaves the salt behind.", "Il mare è salato perché i fiumi portano i sali minerali delle rocce e l'acqua che evapora lascia il sale."),
        ("perché esistono le stagioni?", "We have seasons because the Earth's axis is tilted, so each hemisphere receives more direct sunlight at different times of the year.", "Esistono le stagioni perché l'asse della Terra è inclinato rispetto al piano della sua orbita."),
    ])
    out.append(_q(9, "Rispondere in un'altra lingua", f"Rispondi in inglese, in una sola frase: {question}", [{"kind": "language", "lang": "en"}, {"kind": "sentences", "max": 1}], answer,
                  [italian, answer + " This effect was first described in the nineteenth century. It is still studied today.", f"Ecco la risposta in inglese: {italian}"], difficulty=3))

    # 10. A letter that must never appear.
    rng = Rng(seed, "instructions.010")
    letter, sentence = rng.choice([("a", "Io esco con te questo pomeriggio."), ("u", "Io vedo il mare dal balcone.")])
    out.append(_q(10, "Una lettera vietata", f"Scrivi una frase di almeno cinque parole senza usare mai la lettera «{letter}». Rispondi solo con la frase.",
                  [{"kind": "words", "min": 5}, {"kind": "no_letter", "letter": letter}], sentence,
                  ["Il gatto dorme sul divano.", sentence.replace("Io", "Tu" if letter != "u" else "Noi") + " Buonanotte, amici.", "Ciao."], difficulty=3))

    # 11. Several rules at once.
    rng = Rng(seed, "instructions.011")
    greeting, closing = rng.choice([("Buongiorno", "Grazie."), ("Gentili signori", "Cordialmente."), ("Salve", "A presto.")])
    mail = (f"{greeting},\nvi scrivo per confermare l'appuntamento previsto per il [data] presso la nostra sede.\n\n"
            f"Vi chiedo di portare i documenti già concordati, così da velocizzare le pratiche.\n\nResto a disposizione per qualsiasi chiarimento.\n{closing}")
    out.append(_q(11, "Più regole insieme", f"Scrivi una email di esattamente tre paragrafi (separati da una riga vuota): deve cominciare con «{greeting}», finire con «{closing}», contenere un solo segnaposto tra parentesi quadre (come [data]) e non superare le 80 parole.",
                  [{"kind": "paragraphs", "equals": 3}, {"kind": "starts_with", "text": greeting}, {"kind": "ends_with", "text": closing}, {"kind": "placeholders", "equals": 1}, {"kind": "words", "max": 80}], mail,
                  [mail.replace("\n\nVi chiedo", "\nVi chiedo"), mail.rsplit("\n", 1)[0], mail.replace("la nostra sede", "la nostra sede [indirizzo]"), mail + "\n\n" + "Ulteriori dettagli verranno comunicati nei prossimi giorni, appena disponibili. " * 5], difficulty=3))

    # 12. A short text with several limits.
    rng = Rng(seed, "instructions.012")
    topic, tweet = rng.choice([
        ("la colazione", "il profumo del caffè e dei cornetti caldi rende ogni mattina un po' più bella #colazione #buongiorno"),
        ("il lunedì mattina", "lunedì mattina, sveglia presto e caffè doppio: si riparte con un sorriso #lunedì #energia"),
        ("una passeggiata in montagna", "aria fresca, sentiero in salita e un panorama che ripaga ogni fatica #montagna #natura"),
    ])
    out.append(_q(12, "Un tweet con più limiti", f"Scrivi un tweet sul tema «{topic}»: tutto in minuscolo, al massimo 120 caratteri e con esattamente due hashtag.",
                  [{"kind": "lowercase"}, {"kind": "chars", "max": 120}, {"kind": "regex", "pattern": r"#\w+.*#\w+", "must": True}, {"kind": "regex", "pattern": r"#\w+.*#\w+.*#\w+", "must": False}], tweet,
                  [tweet.upper(), tweet + " #extra", tweet.rsplit(" #", 1)[0], tweet + " e poi ancora un lungo giro di parole che non finisce più, perché i tweet devono essere brevi e questo non lo è affatto"], difficulty=3))
    return out
