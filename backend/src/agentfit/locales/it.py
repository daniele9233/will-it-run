"""Italian catalog for text the core produces. Keys are the English source strings.

Placeholders, including their format specs (``{gb:.1f}``), must match the key exactly.
``scripts/list_core_strings.py`` lists keys that have no entry here.
"""

MESSAGES: dict[str, str] = {
    # -- confidence levels and fit verdicts (translated through a variable) ----------
    "HIGH": "ALTA",
    "MEDIUM": "MEDIA",
    "LOW": "BASSA",
    "UNKNOWN": "SCONOSCIUTA",
    "PERFECT": "PERFETTO",
    "EXCELLENT": "ECCELLENTE",
    "GOOD": "BUONO",
    "ACCEPTABLE": "ACCETTABILE",
    "SLOW": "LENTO",
    "MEMORY TIGHT": "MEMORIA AL LIMITE",
    "NOT RECOMMENDED": "SCONSIGLIATO",
    "IMPOSSIBLE": "IMPOSSIBILE",
    # -- team specializations (labels only; translated through a variable) -----------
    "Infrastructure": "Infrastruttura",
    "Content": "Contenuti",
    "Research": "Ricerca",
    "Creative": "Creatività",
    "Automation": "Automazione",
    "Windows Administration": "Amministrazione Windows",
    "Networking": "Reti",
    "Cybersecurity": "Sicurezza informatica",
    "Enterprise Documentation": "Documentazione aziendale",
    "Technical Documentation": "Documentazione tecnica",
    "Creative Writing": "Scrittura creativa",
    "Book Writing": "Scrittura di libri",
    "Editing": "Revisione di testi",
    "Translation": "Traduzione",
    "Scientific Research": "Ricerca scientifica",
    "Biomedical Research": "Ricerca biomedica",
    "Legal Document Analysis": "Analisi di documenti legali",
    "Image": "Immagini",
    "Photography": "Fotografia",
    "Computer Use": "Uso del computer",
    "Browser Automation": "Automazione del browser",
    # -- dashboard --------------------------------------------------------------------
    "Coding": "Programmazione",
    "Reasoning": "Ragionamento",
    "Vision": "Visione",
    "Writing": "Scrittura",
    "Large 70B models": "Modelli grandi da 70B",
    # -- why a model is recommended ---------------------------------------------------
    "coding": "programmazione",
    "reasoning": "ragionamento",
    "writing": "scrittura",
    "but": "ma",
    "Installed file: {file} ({quant}).": "File installato: {file} ({quant}).",
    "Excellent {skill} capability (estimate, {confidence} confidence)":
        "Capacità eccellente in {skill} (stima, affidabilità {confidence})",
    "Strong {skill} capability (estimate, {confidence} confidence)":
        "Capacità solida in {skill} (stima, affidabilità {confidence})",
    "Moderate {skill} capability for this job (estimate)": "Capacità moderata in {skill} per questo compito (stima)",
    "Quality unknown: parameter count not available": "Qualità sconosciuta: numero di parametri non disponibile",
    "Tool use supported": "Supporta l'uso di strumenti",
    "Tool use not confirmed for this model": "Uso di strumenti non confermato per questo modello",
    "Accepts images": "Accetta immagini",
    "Uncensored / abliterated build ({evidence})": "Versione uncensored / abliterated ({evidence})",
    "Limited metadata: {fields} unknown": "Metadati limitati: sconosciuti {fields}",
    "several fields": "diversi campi",
    "{name} has slightly stronger expected quality ({a:.0f} vs {b:.0f})":
        "{name} ha una qualità attesa leggermente superiore ({a:.0f} contro {b:.0f})",
    "requires partial CPU offload": "richiede di spostare parte del modello sulla CPU",
    "expected ~{a:.0f} tok/s versus ~{b:.0f} tok/s": "previsti ~{a:.0f} tok/s contro ~{b:.0f} tok/s",
    "leaves only {gb:.1f} GB of VRAM reserve": "lascia solo {gb:.1f} GB di riserva di VRAM",
    "poor choice for concurrent agents": "scelta poco adatta ad agenti in parallelo",
    "only fits at {a}, a heavier quantization than {b}": "ci sta solo a {a}, una quantizzazione più spinta di {b}",
    "lower overall Will It Run score ({a:.1f} vs {b:.1f})": "punteggio Will It Run complessivo più basso ({a:.1f} contro {b:.1f})",
    # -- search notes -----------------------------------------------------------------
    "Showing fit for a hypothetical {gb:g} GB GPU, not this PC.":
        "Compatibilità calcolata per una GPU ipotetica da {gb:g} GB, non per questo PC.",
    "No uncensored models are cached yet. Connect to the Internet to search Hugging Face.":
        "Nessun modello uncensored è ancora in cache. Collegati a Internet per cercare su Hugging Face.",
    "Hugging Face search is disabled in Settings; showing cached and built-in models only.":
        "La ricerca su Hugging Face è disattivata nelle Impostazioni: sono mostrati solo i modelli in cache e integrati.",
    "Hugging Face is unreachable ({error}); showing cached and built-in models only.":
        "Hugging Face non è raggiungibile ({error}): sono mostrati solo i modelli in cache e integrati.",
    # -- fit engine -------------------------------------------------------------------
    "Measured on this PC.": "Misurato su questo PC.",
    "Measured {tps:.0f} tok/s on this PC.": "Misurati {tps:.0f} tok/s su questo PC.",
    "Estimated ~{tps:.0f} tok/s ({confidence} confidence).": "Stimati ~{tps:.0f} tok/s (affidabilità {confidence}).",
    "Needs {need:.1f} GB but only {vram:.1f} GB VRAM and {ram:.1f} GB RAM are safely usable.":
        "Richiede {need:.1f} GB ma sono utilizzabili in sicurezza solo {vram:.1f} GB di VRAM e {ram:.1f} GB di RAM.",
    "Fits completely on your GPU.": "Entra completamente nella tua GPU.",
    "Too large for one card; split across your GPUs.": "Troppo grande per una sola scheda: diviso tra le tue GPU.",
    "Leaves only {mb:.0f} MB of the recommended VRAM budget.": "Lascia solo {mb:.0f} MB del budget di VRAM consigliato.",
    "Leaves {gb:.1f} GB of VRAM headroom for Windows and other apps.":
        "Lascia {gb:.1f} GB di VRAM liberi per Windows e le altre app.",
    "Little room left for a second model or a larger context.": "Poco spazio per un secondo modello o un contesto più lungo.",
    "Requires partial CPU offload: {share:.0f}% of the weights on the GPU, {ram:.1f} GB in system RAM.":
        "Richiede di usare anche la CPU: {share:.0f}% dei pesi sulla GPU, {ram:.1f} GB nella RAM di sistema.",
    "Leaves almost no VRAM reserve; poor choice for concurrent agents.":
        "Non lascia quasi riserva di VRAM: scelta poco adatta ad agenti in parallelo.",
    "Runs on the CPU only, using {gb:.1f} GB of system RAM.": "Gira solo sulla CPU, usando {gb:.1f} GB di RAM di sistema.",
    "{k}K context fits comfortably.": "Un contesto di {k}K ci sta comodamente.",
    "Context reduced to {n} tokens so the model fits in memory.":
        "Contesto ridotto a {n} token perché il modello entri in memoria.",
    "Will It Run selected {chosen} instead of {alt}: {alt} would leave only {mb:.0f} MB of usable VRAM, while {chosen} "
    "leaves about {gb:.1f} GB and is more stable on this computer.":
        "Will It Run ha scelto {chosen} invece di {alt}: {alt} lascerebbe solo {mb:.0f} MB di VRAM utilizzabile, mentre "
        "{chosen} ne lascia circa {gb:.1f} GB ed è più stabile su questo computer.",
    "Will It Run selected {chosen} instead of {alt}: {alt} needs partial CPU offload and would run at about "
    "{alt_tps:.0f} tok/s instead of {tps:.0f} tok/s.":
        "Will It Run ha scelto {chosen} invece di {alt}: {alt} dovrebbe usare anche la CPU e girerebbe a circa "
        "{alt_tps:.0f} tok/s invece di {tps:.0f} tok/s.",
    "{quant} does not fit this computer ({category}).": "{quant} non è adatto a questo computer ({category}).",
    "Smaller quantizations would also run, but {quant} keeps more of the model's quality while still fitting.":
        "Girerebbero anche quantizzazioni più piccole, ma {quant} conserva più qualità del modello pur entrando in memoria.",
    # -- speed notes ------------------------------------------------------------------
    "CPU-only inference; speed depends heavily on RAM speed and thread count.":
        "Inferenza solo su CPU: la velocità dipende molto dalla velocità della RAM e dal numero di thread.",
    "Mixture-of-experts model: only active experts are read per token.":
        "Modello mixture-of-experts: per ogni token vengono letti solo gli esperti attivi.",
    "Split across GPUs: about 10% slower than a single card.": "Diviso tra più GPU: circa il 10% più lento di una scheda singola.",
    "{share:.0f}% of the weights run from system RAM, which is much slower than VRAM.":
        "Il {share:.0f}% dei pesi gira dalla RAM di sistema, molto più lenta della VRAM.",
    "GPU memory bandwidth unknown; estimate uses a per-backend constant.":
        "Banda di memoria della GPU sconosciuta: la stima usa una costante per backend.",
    # -- resource budget --------------------------------------------------------------
    "System RAM: {total:.1f} GB - {reserve:.1f} GB reserved for Windows and apps = {usable:.1f} GB recommended usable":
        "RAM di sistema: {total:.1f} GB - {reserve:.1f} GB riservati a Windows e alle app = {usable:.1f} GB utilizzabili consigliati",
    "{name}: {total:.1f} GB - {os:.1f} GB Windows/desktop - {safety:.1f} GB safety = {usable:.1f} GB recommended usable":
        "{name}: {total:.1f} GB - {os:.1f} GB Windows/desktop - {safety:.1f} GB di sicurezza = {usable:.1f} GB utilizzabili consigliati",
    "{n} {backend} GPUs: one model can be split across them (llama.cpp layer split) for about {gb:.1f} GB, "
    "not the raw sum. Runtimes without splitting are limited to the largest single GPU.":
        "{n} GPU {backend}: un modello può essere diviso tra loro (layer split di llama.cpp) per circa {gb:.1f} GB, "
        "non la somma completa. I runtime che non dividono i modelli sono limitati alla GPU singola più grande.",
    # -- accuracy ---------------------------------------------------------------------
    "Near lossless": "Quasi senza perdita",
    "Very low loss": "Perdita molto bassa",
    "Low loss": "Perdita bassa",
    "Noticeable loss": "Perdita percepibile",
    "High loss": "Perdita alta",
    "Severe loss": "Perdita grave",
    "Unknown": "Sconosciuta",
    "Original precision": "Precisione originale",
    "Unquantized weights: this is the model exactly as published.":
        "Pesi non quantizzati: è il modello esattamente come pubblicato.",
    "+{delta} perplexity over the original, measured by llama.cpp on {model}.":
        "+{delta} di perplessità rispetto all'originale, misurato da llama.cpp su {model}.",
    "No published figure for {quant}; shown with the one for {nearest} (+{delta}), the closest measured quantization.":
        "Nessun dato pubblicato per {quant}: è mostrato quello di {nearest} (+{delta}), la quantizzazione misurata più vicina.",
    "No published perplexity for {quant}. Shown with {proxy}, the smallest measured quantization of the same bit "
    "width; treat it as a rough guide.":
        "Nessuna perplessità pubblicata per {quant}. È mostrato con {proxy}, la quantizzazione misurata più piccola "
        "con gli stessi bit: prendilo come indicazione di massima.",
    "No published figure for {quant}; placed with other {bits}-bit quantizations.":
        "Nessun dato pubblicato per {quant}: collocato con le altre quantizzazioni a {bits} bit.",
    "This quantization is not recognized, so no accuracy figure is shown.":
        "Questa quantizzazione non è riconosciuta, quindi non viene mostrato alcun dato di accuratezza.",
    "Measured on one 8B model. Larger models usually lose less at the same quantization; smaller ones lose more.":
        "Misurato su un solo modello da 8B. I modelli più grandi di solito perdono meno a parità di quantizzazione; "
        "quelli più piccoli perdono di più.",
    # -- disk safety ------------------------------------------------------------------
    "It needs {required:.1f} GB but only {available:.1f} GB is safely available on {drive} ({free:.1f} GB free "
    "minus a {reserve:.1f} GB reserve).":
        "Servono {required:.1f} GB ma su {drive} sono disponibili in sicurezza solo {available:.1f} GB "
        "({free:.1f} GB liberi meno una riserva di {reserve:.1f} GB).",
    "NOT SAFE: {reason}": "NON SICURO: {reason}",
    "{gb:.0f} GB will remain free on {drive}.": "Su {drive} resteranno liberi {gb:.0f} GB.",
    "SAFE TO INSTALL: {reason}": "SICURO DA INSTALLARE: {reason}",
    "Use a smaller quantization: {option}": "Usa una quantizzazione più piccola: {option}",
    "Choose a smaller model": "Scegli un modello più piccolo",
    "Delete unused models to free space": "Elimina i modelli che non usi per liberare spazio",
    "Save to drive {drive} instead ({gb:.0f} GB safely available)":
        "Salva invece sul disco {drive} ({gb:.0f} GB disponibili in sicurezza)",
    "LM Studio already stores models here and there is room for more.":
        "LM Studio salva già qui i modelli e c'è spazio per altri.",
    "Most free space. LM Studio's current folder is nearly full.":
        "Più spazio libero. La cartella attuale di LM Studio è quasi piena.",
    "Most free space. LM Studio's folder was not found.": "Più spazio libero. La cartella di LM Studio non è stata trovata.",
    # -- downloads --------------------------------------------------------------------
    "Saved to {folder} and linked into LM Studio's folder.": "Salvato in {folder} e collegato nella cartella di LM Studio.",
    "Saved to {folder}. LM Studio already has a file with this name.":
        "Salvato in {folder}. LM Studio ha già un file con questo nome.",
    "Saved to {folder}, but Windows did not allow linking it into LM Studio's folder ({folder_lm}). LM Studio will "
    "not list it until you move it there, or turn on Developer Mode in Windows and download again.":
        "Salvato in {folder}, ma Windows non ha permesso di collegarlo nella cartella di LM Studio ({folder_lm}). "
        "LM Studio non lo elencherà finché non lo sposti lì, oppure attiva la Modalità sviluppatore di Windows e scarica di nuovo.",
    "Choose a full folder path, for example D:\\AI\\Models.": "Scegli il percorso completo di una cartella, ad esempio D:\\AI\\Models.",
    "Drive {drive} does not exist on this computer.": "Il disco {drive} non esiste su questo computer.",
    "{path} is a file, not a folder.": "{path} è un file, non una cartella.",
    "LM Studio's models folder is unknown. Choose a folder for this download.":
        "La cartella dei modelli di LM Studio è sconosciuta. Scegli una cartella per questo download.",
    "Unknown download method: {choice}": "Metodo di download sconosciuto: {choice}",
    "The file size is unknown, so disk safety cannot be verified. Refresh the model's file list first.":
        "La dimensione del file è sconosciuta, quindi non si può verificare la sicurezza del disco. Aggiorna prima l'elenco dei file del modello.",
    "LM Studio's own downloader cannot be paused. Stop it from LM Studio, or use Will It Run's downloader next time.":
        "Il downloader di LM Studio non si può mettere in pausa. Fermalo da LM Studio, oppure la prossima volta usa il downloader di Will It Run.",
    "LM Studio's own downloader cannot be stopped from here. Stop it from LM Studio, or use Will It Run's downloader next time.":
        "Il downloader di LM Studio non si può fermare da qui. Fermalo da LM Studio, oppure la prossima volta usa il downloader di Will It Run.",
    "Stop the download before removing it.": "Ferma il download prima di rimuoverlo.",
    "Incomplete download: got {got} of {total} bytes. Retry to resume from where it stopped.":
        "Download incompleto: ricevuti {got} byte su {total}. Riprova per riprendere da dove si è fermato.",
    "LM Studio did not return a download job (status: {status}).":
        "LM Studio non ha restituito un download in corso (stato: {status}).",
    "LM Studio reported that the download failed.": "LM Studio ha segnalato che il download è fallito.",
    "SHA-256 mismatch: the downloaded file is corrupt and was deleted. Retry the download.":
        "SHA-256 non corrispondente: il file scaricato è corrotto ed è stato eliminato. Riprova il download.",
    "Interrupted when Will It Run closed; resume to continue.": "Interrotto alla chiusura di Will It Run: riprendi per continuare.",
    "Model download complete": "Download del modello completato",
    "The agent has a question": "L'agente ha una domanda",
    "Model download failed": "Download del modello non riuscito",
    "Hugging Face returned HTTP {code} for {file}": "Hugging Face ha risposto HTTP {code} per {file}",
    # -- workflow templates (name and description, translated through a variable) ----
    "Coding Team": "Team di programmazione",
    "Tech lead, planner, human-approved plan, developer, test loop and code review.":
        "Tech lead, pianificatore, piano approvato da te, sviluppatore, ciclo di test e revisione del codice.",
    "DevOps Team": "Team DevOps",
    "Supervisor, architect, human-approved design, engineer, validation loop, documentation and technical review.":
        "Supervisore, architetto, progetto approvato da te, ingegnere, ciclo di validazione, documentazione e revisione tecnica.",
    "Documentation Team": "Team di documentazione",
    "Supervisor, documentation specialist and a technical review loop.":
        "Supervisore, specialista di documentazione e un ciclo di revisione tecnica.",
    "Three.js Team": "Team Three.js",
    "Art direction, scene architecture, three parallel builders, integration, browser test and performance loop.":
        "Direzione artistica, architettura della scena, tre sviluppatori in parallelo, integrazione, test nel browser e ciclo sulle prestazioni.",
    "Writing Team": "Team di scrittura",
    "Editorial direction, author, editor rewrite loop, fact check, proofreading and final review.":
        "Direzione editoriale, autore, ciclo di riscrittura con l'editor, verifica dei fatti, correzione di bozze e revisione finale.",
    "Computer Use Team": "Team per l'uso del computer",
    "Plan, permission check, observe-decide-classify-execute-verify loop with human approval for risky actions.":
        "Piano, controllo dei permessi, ciclo osserva-decidi-classifica-esegui-verifica con approvazione umana per le azioni rischiose.",
    "Adversarial Blackboard": "Lavagna avversariale",
    "A researcher posts claims with quotes; a skeptic who never sees the reasoning tries to break them; code keeps only the claims whose quote is really in the evidence and survived; the answer is written from those alone.":
        "Un ricercatore pubblica affermazioni con citazioni; uno scettico che non vede mai il ragionamento prova a smontarle; il codice tiene solo le affermazioni la cui citazione è davvero nelle prove e che hanno retto; la risposta viene scritta solo da quelle.",
    "Commit DAG": "DAG di commit",
    "Two authors work on the same specification without seeing each other. An independent reviewer approves or rejects each commit; code recomputes the hashes and passes only what was approved unchanged; the merger never sees the rest.":
        "Due autori lavorano sulla stessa specifica senza vedersi. Un revisore indipendente approva o respinge ogni commit; il codice ricalcola gli hash e fa passare solo ciò che è stato approvato senza modifiche; chi unisce il lavoro non vede mai il resto.",
    "Fail-Closed Swarm": "Sciame fail-closed",
    "A gate runs before any model. The router can only name an expert from a fixed list, or refuse. The expert's claims go to a validator and a quote check; if nothing survives another expert is tried, three activations at most, and then the run refuses instead of answering.":
        "Un controllo gira prima di qualsiasi modello. Il router può nominare solo un esperto di una lista fissa, oppure rifiutare. Le affermazioni dell'esperto passano da un validatore e da un controllo delle citazioni; se non resta nulla si prova un altro esperto, tre attivazioni al massimo, poi l'esecuzione rifiuta invece di rispondere.",
    "Deferred-Consensus Delphi": "Delphi a consenso differito",
    "Three panelists vote on the same propositions from the same evidence, blind to each other. Code counts the votes afterwards; what splits the panel gets one more round with anonymous counts; what is still contested is reported as unresolved, and without a quorum the run ends with INSUFFICIENT_CONSENSUS.":
        "Tre esperti votano le stesse proposizioni sulle stesse prove, senza vedersi. Il codice conta i voti dopo; ciò che divide il gruppo ha un secondo giro con conteggi anonimi; ciò che resta conteso viene riportato come irrisolto e, senza quorum, l'esecuzione finisce con INSUFFICIENT_CONSENSUS.",
    # -- deterministic checks (label and description, translated through a variable) -
    "Evidence dossier": "Fascicolo delle prove",
    "Runs before any model. Collects the request and the text files of the documents folder into numbered sources, records the SHA-256 of each, and stops the run when there is too little evidence or the request is too long. Everything an agent later quotes is checked against this text.":
        "Gira prima di qualsiasi modello. Raccoglie la richiesta e i file di testo della cartella dei documenti in fonti numerate, registra lo SHA-256 di ognuna e ferma l'esecuzione quando le prove sono troppo poche o la richiesta è troppo lunga. Tutto ciò che un agente cita dopo viene confrontato con questo testo.",
    "Claim filter": "Filtro delle affermazioni",
    "Keeps a claim only if its quote exists word for word in the evidence and, when an auditor is set, the auditor's verdict says it holds. A claim with no quote, an invented quote or no verdict is discarded. It proves the quote is real, not that the claim reads it correctly: that is the auditor's job.":
        "Tiene un'affermazione solo se la sua citazione esiste parola per parola nelle prove e, quando c'è un revisore, se il suo verdetto dice che regge. Un'affermazione senza citazione, con una citazione inventata o senza verdetto viene scartata. Dimostra che la citazione è vera, non che l'affermazione la legga correttamente: quello è il lavoro del revisore.",
    "Consensus tally": "Conteggio del consenso",
    "Counts the panel's votes on each proposition. A vote backed by a quote found in the evidence weighs 1, a vote without one weighs half, and in a second round a vote against the earlier majority without a quote weighs nothing. A proposition is decided only when one side reaches the threshold and holds a real quote; the rest stay contested. The threshold is a setting, not a calibrated value.":
        "Conta i voti del gruppo su ogni proposizione. Un voto con una citazione trovata nelle prove pesa 1, un voto senza pesa la metà, e al secondo giro un voto contro la maggioranza precedente senza citazione non pesa nulla. Una proposizione è decisa solo quando una parte raggiunge la soglia e ha una citazione vera; le altre restano contese. La soglia è un'impostazione, non un valore calibrato.",
    "Commit check": "Controllo dei commit",
    "Recomputes the SHA-256 of every commit and lets one through only if the reviewer approved it and the hash equals the one recorded when the reviewer read it. A commit that was rejected, not reviewed, or changed after its review is dropped. The merge step is shown only what passes.":
        "Ricalcola lo SHA-256 di ogni commit e lo fa passare solo se il revisore lo ha approvato e l'hash è uguale a quello registrato quando lo ha letto. Un commit respinto, non revisionato o modificato dopo la revisione viene scartato. Al passo di unione viene mostrato solo ciò che passa.",
    "Audit trail": "Tracciato di verifica",
    "The last step. Checks that every item the answer says it relies on was accepted earlier, recomputes every recorded hash, and releases the answer together with the list of steps, what each one read and its SHA-256. It checks the citations the answer declares, not the wording of the answer.":
        "L'ultimo passo. Controlla che ogni elemento su cui la risposta dice di basarsi sia stato accettato prima, ricalcola ogni hash registrato e rilascia la risposta insieme all'elenco dei passi, a ciò che ognuno ha letto e al suo SHA-256. Controlla le citazioni dichiarate dalla risposta, non il testo della risposta.",
    "Preparation": "Preparazione",
    "Fetches the cloning kit at its pinned commit, puts its project in the folder (unless the folder already is one), installs its packages and Playwright's Chromium.":
        "Scarica il kit di clonazione alla versione fissata, mette il suo progetto nella cartella (a meno che lo sia già), installa i pacchetti e il Chromium di Playwright.",
    "Build check": "Controllo della build",
    "Runs the project's check script (lint, typecheck, production build). A failure is sent back, with the log, to the agent that wrote the code.":
        "Esegue lo script di controllo del progetto (lint, typecheck, build di produzione). Un errore torna, con il log, all'agente che ha scritto il codice.",
    "Refusal": "Rifiuto",
    "Ends the run without an answer, with a named verdict. A workflow routes here when no trusted path produced something that could be verified: refusing is the outcome, not a failure to be worked around.":
        "Chiude l'esecuzione senza risposta, con un verdetto esplicito. Un workflow arriva qui quando nessun percorso affidabile ha prodotto qualcosa di verificabile: rifiutare è l'esito, non un errore da aggirare.",
}

# What the Arena's core says (why an answer was judged as it was, its messages and its reports). A key above keeps its translation.
from agentfit.locales.it_arena import MESSAGES as _ARENA  # noqa: E402

for _key, _value in _ARENA.items():
    MESSAGES.setdefault(_key, _value)
