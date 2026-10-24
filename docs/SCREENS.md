# Screenshots

Immagini di ogni schermata dell'app, a 1600×1000 (la finestra è 1440×900, quindi il layout è
quello reale o più largo). I file **senza suffisso** sono in **italiano**, quelli con `-en` in
**inglese**. I dati sono quelli veri di questa macchina (RTX 4080 SUPER 16 GB, i7-14700K,
32 GB DDR5, 12 modelli in LM Studio): non ci sono numeri inventati.

## Avvio

| File | Cosa mostra |
|---|---|
| `00-wizard-scanning.png` | Primo avvio, scansione in corso: gli 8 passi con lo stato, la colonna dei risultati ancora a scheletro. |
| `00-wizard.png` | Primo avvio, scansione finita: PC riconosciuto, VRAM e RAM utilizzabili consigliate, LM Studio trovato con 12 modelli, pulsante Continua. |

## Le schermate

| Schermata | Italiano | Inglese | Cosa mostra |
|---|---|---|---|
| Home | `01-home.png` | `01-home-en.png` | Avviso "Nuovo all'AI locale?", mappa della memoria grafica con il modello consigliato, memoria di sistema, dischi, grafico «cosa gira su questo PC», prontezza AI per tipo di lavoro, modelli installati. |
| Hardware | `02-hardware.png` | `02-hardware-en.png` | Scheda completa: sistema, CPU, RAM, GPU con affidabilità, dischi, riserva di sicurezza. |
| Esplora modelli | `03-models.png` | `03-models-en.png` | Ricerca, filtri per uso (Consigliati, Programmazione, …), filtro di allineamento, elenco con velocità stimata ed esito di compatibilità; il badge "Installato" segna i file già presenti. |
| Team AI | `04-teams.png` | `04-teams-en.png` | Il modulo di pianificazione: specializzazioni per gruppo, specializzazione libera, quanti modelli, modalità (Automatico / Guidato / Esperto), allineamento, nome del team. |
| Workflow — elenco | `05-workflows.png` | `05-workflows-en.png` | I workflow dell'utente e i modelli di verifica/team che Will It Run fornisce, con numero di agenti e controlli nel codice. |
| Workflow — editor | `05b-workflow-editor.png` | `05b-workflow-editor-en.png` | Il grafo di un workflow vero (Richiesta → Supervisor → Documentation Specialist → Technical Reviewer → Risultato), con i modelli assegnati a destra e il piano di caricamento. |
| Workflow — dettagli | `05c-workflow-editor1…3.png` | `05c-workflow-editor*−en.png` | Lo stesso editor scorso in basso: contesto e memoria, passi, esecuzione. |
| Benchmark | `06-benchmarks.png` | `06-benchmarks-en.png` | Scelta del modello e del contesto, più la cronologia con i numeri **misurati** (113 tok/s, primo token 53 ms, 4,8 GB di VRAM, caricamento 2,8 s, punteggio 8,3). |
| Arena — Classifica | `14-arena-classifica.png` | `14-arena-classifica-en.png` | Il podio dei primi tre con l'anello del punteggio, il radar per area, la classifica con l'intervallo di confidenza, quante domande sono state valutate, tok/s e *dove sbaglia* (risposta sbagliata, formato, inventata, rifiutata, interrotta), poi le barre per area e la tabella per categoria. I numeri sono quelli di una prova dal vivo di 29 domande con `gemma-4-12b-qat` e `spark-x2.5-4b` su questo PC (RTX 4080 SUPER): niente è inventato. |
| Arena — Domande | `14b-arena-domande.png` | `14b-arena-domande-en.png` | La matrice modelli × domande, un gruppo per categoria, i filtri (solo errori, non valutate, dove si dividono) e a destra il dettaglio della domanda scelta: testo, immagine, atteso, cosa ha risposto ogni modello e il motivo del verdetto. |
| Arena — Confronto | `14c-arena-confronto.png` | `14c-arena-confronto-en.png` | Due modelli A contro B: anelli, barre area per area, radar sovrapposto, le domande su cui si separano e la velocità contro l'affidabilità. |
| Arena — Nuova sfida | `14d-arena-nuova.png` | `14d-arena-nuova-en.png` | Chi partecipa (i modelli in LM Studio, e quelli cloud se c'è la chiave), quanto a lungo (rapida 48, standard 115, completa 192 domande), le categorie, le impostazioni registrate nel rapporto e gli strumenti trovati su questo PC che controllano le risposte. |
| Arena — In corso | `14e-arena-in-corso.png` | (solo italiano) | Una corsia per modello con un quadrato per domanda, la domanda posta e la risposta mentre il modello la scrive, il registro dei caricamenti e Ferma. |
| Download | `07-downloads.png` | `07-downloads-en.png` | Dove vengono salvati i modelli (`D:\Models`), i 12 modelli scaricati con dimensione, velocità ed esito, e i download in corso. |
| Sessioni — elenco | `08-sessions.png` | `08-sessions-en.png` | L'elenco delle sessioni per progetto con lo stato, accanto al dettaglio della sessione aperta. |
| Sessioni — dettaglio | `08b-session-detail.png` | `08b-session-detail-en.png` | Una sessione reale: caricamento del modello con tempi e VRAM, il passo del Supervisor completato (token, tok/s, hash), il cambio di modello, il passo fallito con i due tentativi e l'errore, più il pannello Team/Modelli/Strumenti/Risorse/Artefatti a destra. |
| Sessioni — attività | `08c-session-activity1…3.png` | `08c-session-activity*−en.png` | La stessa sessione scorsa: attività dei passi, hash degli output, dettagli. |
| Sessioni — nuova | `08d-session-new.png` | `08d-session-new-en.png` | Il modulo per avviare una sessione: workflow, progetto, compito. |
| Log | `09-logs.png` | `09-logs-en.png` | Il registro di cosa ha fatto Will It Run, con l'avviso che password, token e chiavi vengono oscurati. |
| Guida | `10-guide.png` | `10-guide-en.png` | La guida in parole semplici: come funziona, cosa significano i numeri, le domande frequenti. |
| Impostazioni | `11-settings.png` | `11-settings-en.png` | Scheda Generale: lingua, dimensione dell'interfaccia, modalità di esperienza, tema (Scuro / Chiaro / Sistema), colore d'accento, notifiche, nuova scansione di benvenuto. |

## Stati trasversali

| File | Cosa mostra |
|---|---|
| `12-command-palette.png`, `12-command-palette-en.png` | Il command palette (`Ctrl+K`) aperto sopra la schermata: comandi, scorciatoie, ricerca. |
| `13-home-light.png` | La Home nel **tema chiaro**, per confronto con la versione scura. |

## Bozze di design

Render delle bozze in `design/`, non dell'app:

| File | Cosa mostra |
|---|---|
| `design-1-grafite.png` | Direzione 1 · Grafite: è quella poi applicata nell'app. |
| `design-2-telemetria.png` | Direzione 2 · Telemetria: indicatori circolari, ambra, scala dell'obiettivo. |
| `design-3-carta.png` | Direzione 3 · Carta: chiara, tipografica, da scheda tecnica. |
| `design-4-morbido.png` | Direzione 4 · Morbido: riquadri arrotondati, indaco, corallo e menta. |
| `design-5-terminale.png` | Direzione 5 · Terminale: monospazio, verde fosforo, menu numerato. |
| `design-interface-options-page.png` | La pagina delle 5 direzioni intera, con il selettore in alto e la didascalia in basso. |
| `design-logo-options.png` | Le 4 idee di logo, ognuna come icona grande, icona piccola e accanto al nome, su fondo scuro e chiaro. |

## Come sono state prodotte

1. Il core vero in sviluppo: `backend\.venv\Scripts\willitrun.exe serve --port 8765`, con
   `AGENTFIT_HOME` puntato a una cartella di lavoro (niente dati dell'utente toccati).
2. L'interfaccia in browser: `cd desktop; npm run dev` su `http://127.0.0.1:1420`
   (vedi [DEVELOPMENT.md](DEVELOPMENT.md)).
3. Cattura con Chromium headless a 1600×1000, `prefers-reduced-motion`, tema scuro, una
   schermata per volta; per le schermate lunghe è stata scorsa l'area di scorrimento in modo
   che le righe pigre esistano davvero.
4. Le bozze in `design/` sono state renderizzate a 1360×820, la loro misura naturale.

Per rifarle basta ripetere i tre passi: nessun dato è finto, quindi rifacendole su un'altra
macchina cambiano i numeri ma non il layout.
