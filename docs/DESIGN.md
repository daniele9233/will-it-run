# Design: file e dove vivono

Mappa dei file che determinano l'aspetto, il linguaggio visivo e i testi dell'interfaccia di
**Will It Run**, con accanto le immagini delle schermate renderizzate
(`docs/screenshots/`, indice in [SCREENS.md](SCREENS.md)).

Il design vive in due posti: le **bozze** in `design/` (le direzioni grafiche da cui è nata
l'interfaccia, in HTML autonomo) e il **design system applicato** in `desktop/src/` (token,
primitive, schermate). `PRODUCT.md` è il documento che lega i due: dice a cosa serve il
prodotto, per chi è e con quale linguaggio va disegnato.

## 1. Documento di riferimento

| File | Cosa contiene |
|---|---|
| [`PRODUCT.md`](../PRODUCT.md) | Contesto di prodotto per il design: cos'è, chi lo usa, 5 principi, linguaggio visivo (colore, tipografia, motion), dove stanno token e primitive. È la fonte da leggere prima di toccare l'interfaccia. |

## 2. Bozze di design (`design/`)

| File | Cosa contiene |
|---|---|
| [`design/interface-options.html`](../design/interface-options.html) | Le **5 direzioni grafiche** a confronto, ognuna disegnata a 1360×820 con i dati reali del PC: 1 Grafite, 2 Telemetria, 3 Carta, 4 Morbido, 5 Terminale. Si sfoglia con i tasti `1`–`5`. La direzione 1 (Grafite) è quella poi applicata nell'app. |
| [`design/arena-options.html`](../design/arena-options.html) | Le **3 grafiche proposte per l'Arena**, con dati di esempio: Podio (primi tre, radar e classifica), Matrice (modelli × domande con il dettaglio a fianco), Duello (due modelli A contro B), ognuna nei tre stati (nuova sfida, in corso, risultati), più il piano dei test. Sono diventate le tre schede della pagina: Classifica, Domande, Confronto. |
| [`design/logo-options.html`](../design/logo-options.html) | Le **4 idee di logo**, ognuna mostrata come icona grande, icona da barra delle applicazioni e accanto al nome, su fondo scuro e chiaro. La n. 1 è quella in uso. |

Render delle stesse bozze, per guardarle senza aprire il browser:
`docs/screenshots/design-1-grafite.png` … `design-5-terminale.png`,
`design-interface-options-page.png` (con il selettore e la didascalie delle direzioni),
`design-logo-options.png`.

## 3. Design system applicato

### Token e stili

| File | Cosa contiene |
|---|---|
| [`desktop/src/styles.css`](../desktop/src/styles.css) | **Token del design system** (OKLCH): tema dark e light, superfici (`--bg`, `--panel`, `--surface`, `--raised`), linee, inchiostro (`--ink`, `--ink-2`, `--ink-3`), accento configurabile, colori semantici (`--ok`, `--warn`, `--limited`, `--bad`, `--off`), ombra, scala tipografica fissa, raggi, easing, scala z-index; keyframe del motion (rise-in, fade-in, skeleton) e il tema React Flow. |
| [`desktop/index.html`](../desktop/index.html) | Guscio HTML: `data-theme="dark"`, `color-scheme`, titolo della finestra. |
| [`desktop/src-tauri/tauri.conf.json`](../desktop/src-tauri/tauri.conf.json) | Finestra dell'app (1440×900, minimo 1120×700, tema Dark, sfondo `#0a0a0a`), nome prodotto, icona e metadati dell'installer. |

### I grafici dell'Arena

| File | Cosa contiene |
|---|---|
| [`desktop/src/components/arena/charts.tsx`](../desktop/src/components/arena/charts.tsx) | I grafici, disegnati a mano in SVG sui token dell'app: anello del punteggio, barra con intervallo di confidenza, radar, barre per area, barra degli errori, farfalla del confronto, velocità contro affidabilità, il segno di una risposta (`StatusMark`). |
| `--s1` … `--s8` in [`styles.css`](../desktop/src/styles.css) | Un colore per modello, in ordine fisso: un modello ha lo stesso colore in ogni grafico e in ogni scheda. Sono gli otto passi della palette di riferimento, validati sulle superfici dell'app (scuro: tutti i controlli; chiaro: tre passi sotto 3:1, per cui ogni grafico nomina anche ciò che disegna con legenda, etichette e tabella). Il colore non è mai l'unico segnale: ogni risposta ha anche il suo segno (✓ ½ ✗ ? – !). |
| [`desktop/src/lib/arena.ts`](../desktop/src/lib/arena.ts) | Le forme dei dati, i nomi, e l'aritmetica dei grafici (confronto, filtri, raggruppamenti) come funzioni semplici, provate in `arena.test.ts`. |

### Primitive e struttura

| File | Cosa contiene |
|---|---|
| [`desktop/src/components/ui.tsx`](../desktop/src/components/ui.tsx) | Le primitive: `Button`, `IconButton`, `Badge`, `FitBadge`, `StatusDot`, `ConfidenceTag`, `AlignmentTag`, `Meter`, `Stars`, `PageHeader`, `Section`, `Panel`, `KeyValue`, `Collapsible`, `Field`, `TextInput`, `Select`, `TextArea`, `Toggle`, `Segmented`, `Chip`, `Tabs`, `EmptyState`, `SkeletonRows`, `Note`, `Kbd`, `WhyList`, `ProgressBar`, `cx`, `toneVar`. |
| [`desktop/src/components/Shell.tsx`](../desktop/src/components/Shell.tsx) | Struttura della finestra: barra laterale con i gruppi di navigazione, il marchio (`Mark`), la barra di stato (LM Studio, VRAM, RAM), il **command palette** (`Ctrl+K`) e i toast. |
| [`desktop/src/components/Guide.tsx`](../desktop/src/components/Guide.tsx) | Componenti dei contenuti guidati (passi, spiegazioni) usati dentro la Guida e i pannelli "Perché?". |

### Schermate (una per file)

| File | Schermata | Immagine |
|---|---|---|
| [`desktop/src/App.tsx`](../desktop/src/App.tsx) | Avvio, scelta della schermata, primo avvio (wizard) | `00-wizard.png`, `00-wizard-scanning.png` |
| [`desktop/src/pages/Wizard.tsx`](../desktop/src/pages/Wizard.tsx) | Scansione del primo avvio | `00-wizard.png` |
| [`desktop/src/pages/Home.tsx`](../desktop/src/pages/Home.tsx) | Home | `01-home.png` |
| [`desktop/src/pages/Hardware.tsx`](../desktop/src/pages/Hardware.tsx) | Hardware | `02-hardware.png` |
| [`desktop/src/pages/Models.tsx`](../desktop/src/pages/Models.tsx) | Esplora modelli (elenco + scheda modello) | `03-models.png` |
| [`desktop/src/pages/Teams.tsx`](../desktop/src/pages/Teams.tsx) | Team AI | `04-teams.png` |
| [`desktop/src/pages/Workflows.tsx`](../desktop/src/pages/Workflows.tsx) | Workflow (elenco, editor, YAML) | `05-workflows.png`, `05b-workflow-editor.png`, `05c-workflow-editor*.png` |
| [`desktop/src/pages/Benchmarks.tsx`](../desktop/src/pages/Benchmarks.tsx) | Benchmark | `06-benchmarks.png` |
| [`desktop/src/pages/Arena.tsx`](../desktop/src/pages/Arena.tsx) | Arena: la scelta della sfida, il menu dei rapporti, lo stato della sfida | `14-arena-*.png` |
| [`desktop/src/pages/arena/`](../desktop/src/pages/arena/) | Le parti dell'Arena: `NewRun` (nuova sfida), `LiveRun` (corsie dal vivo), `Results` (intestazione e schede), `Podium` (Classifica), `Matrix` e `QuestionPanel` (Domande), `Duel` (Confronto) | `14-arena-*.png` |
| [`desktop/src/pages/Downloads.tsx`](../desktop/src/pages/Downloads.tsx) | Download e spazio su disco | `07-downloads.png` |
| [`desktop/src/pages/Sessions.tsx`](../desktop/src/pages/Sessions.tsx) | Sessioni, dettaglio di una sessione, nuova sessione | `08-sessions.png`, `08b-session-detail.png`, `08c-session-activity*.png`, `08d-session-new.png` |
| [`desktop/src/pages/Logs.tsx`](../desktop/src/pages/Logs.tsx) | Log | `09-logs.png` |
| [`desktop/src/pages/Guide.tsx`](../desktop/src/pages/Guide.tsx) | Guida | `10-guide.png` |
| [`desktop/src/pages/Settings.tsx`](../desktop/src/pages/Settings.tsx) | Impostazioni (7 schede) | `11-settings.png` |

### Testi e lingua

| File | Cosa contiene |
|---|---|
| [`desktop/src/locales/it.txt`](../desktop/src/locales/it.txt) | Catalogo italiano, leggibile e modificabile a mano: ogni riga è `testo inglese ||| traduzione`. È il file da cui si parte per cambiare una parola nell'interfaccia. |
| [`desktop/src/locales/it.ts`](../desktop/src/locales/it.ts) | Lo stesso catalogo generato per il codice (`scripts/build_locale.py`): non si modifica a mano. |
| [`desktop/src/lib/i18n.ts`](../desktop/src/lib/i18n.ts) | Come funziona la lingua: l'inglese è la chiave, `t()` traduce, la lingua è fissata a ogni caricamento della pagina. |
| [`scripts/build_locale.py`](../scripts/build_locale.py) | Rigenera `it.ts` da `it.txt`. |

### Marchio e icone

| File | Cosa contiene |
|---|---|
| [`desktop/app-icon.svg`](../desktop/app-icon.svg) | Logo sorgente (il chip con il tasto play). Da qui si rigenerano le icone. |
| [`desktop/src-tauri/icons/`](../desktop/src-tauri/icons/) | Icone generate (`32x32.png`, `128x128.png`, `128x128@2x.png`, `icon.ico`, `icon.png`, `StoreLogo.png`, le varianti `Square*Logo.png` per lo Store e le cartelle `ios/`, `android/`). |
| `Mark` in [`Shell.tsx`](../desktop/src/components/Shell.tsx) | Lo stesso marchio ridisegnato in SVG dentro l'app (barra laterale, wizard). |

## 4. Documentazione che riguarda l'interfaccia

| File | Cosa contiene |
|---|---|
| [`docs/DEVELOPMENT.md`](DEVELOPMENT.md) | Dove sta cosa nel progetto, come si lancia l'interfaccia in browser contro il core, sezione **Design system** e convenzioni (marchio, nomi che non sono cambiati). |
| [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) | Come è diviso il prodotto (core Python, guscio Tauri, UI React) e dove passa il confine. |
| [`docs/LOCALIZATION.md`](LOCALIZATION.md) | Regole di traduzione e come si aggiunge una stringa. |
| [`README.md`](../README.md) | Presentazione del prodotto e immagini d'insieme. |
