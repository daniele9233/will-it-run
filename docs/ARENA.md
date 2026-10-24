# Arena

The Arena measures how **reliable** a model is. It puts one or more models, one at a time, through the same
questions, and every question has an answer that **code** can check: a number to compare, a JSON document to
validate, a program to run against tests, a Terraform file to apply, a tool call to read. Nothing is judged by another
model, and what no code can judge is never counted as right or wrong: it is shown as **not evaluated**.

It is the second half of what Benchmarks started. A benchmark says how *fast* a model runs on this PC; the Arena says
how far its answers can be *trusted*, where it fails and how it fails.

## Using it

1. **Arena › New challenge.** Choose the models (the ones installed in LM Studio, and Claude, ChatGPT, DeepSeek or
   Gemini if you added a key), the pack (**Quick** 48 questions, **Standard** 115, **Complete** 192 for each model),
   and the categories. The defaults are the ones that make runs comparable: seed 42, temperature 0, one answer per
   question.
2. **Start.** The challenge runs in the background. The live view shows a lane for each model with a square for each
   question, the question being asked and the answer as the model writes it. **Stop** works at any moment; the answers
   given are kept, and **Resume** asks what is missing (and asks again the ones the run itself broke).
3. **Read it** in three tabs:
   - **Ranking** (the podium): the first three with their score, the radar of the areas, the whole ranking with the
     95 % confidence interval, how many questions were scored, tokens per second, and *how the wrong answers went
     wrong*; then the bars by area and the table by category.
   - **Questions** (the matrix): every model against every question. A group per category, filters (only mistakes,
     not evaluated, where the models differ), and on the right the detail of the question you pick: the question, the
     picture or PDF, what was expected, and what each model answered with the reason it was judged as it was.
   - **Duel**: two models against each other. Area by area, the radar overlaid, the questions on which they parted,
     and speed against reliability.
   - **Quantizations**: when the challenge holds the same model in several quantizations (IQ3, Q3_K, Q4...), each one
     against the most precise, with the file, the graphics memory, the speed and *whether the difference is real or
     noise* (see [Quantizations of one model](#quantizations-of-one-model)).
4. **Export** the report as an HTML page (with the charts), Markdown, CSV (one row per answer) or JSON (everything).

## What it asks

Eighteen categories in four groups. The counts are questions in the quick / standard / complete pack.

| Category | What is asked | How it is checked | Q / S / C |
|---|---|---|---|
| General knowledge | Facts with one settled answer, in Italian and English | Answer match, with the wrong answers a model that half remembers would give | 4 / 8 / 14 |
| Reading comprehension | A text, then a question about it | Match on the answer; one question whose answer is not in the text | 2 / 5 / 10 |
| Math | Arithmetic, percentages, equations, probability, counting, modular arithmetic | The number is computed here, in exact arithmetic, from the seed | 4 / 9 / 14 |
| Logic | Puzzles with a single solution (orders, knights and knaves, schedules) | The mapping or the order, compared | 3 / 7 / 12 |
| Coding | Python and JavaScript functions | The code is run against test cases, in a separate process with a time limit | 4 / 8 / 14 |
| PostgreSQL | Queries, DML, schemas, indexes | The SQL is executed on SQLite after a PostgreSQL → SQLite translation; structure and PostgreSQL-only features are read from the text; MySQL and Oracle idioms are refused | 3 / 7 / 12 |
| Terraform | Configurations that are *run* (variables, locals, functions, outputs) and configurations for a cloud that are only *read* | With `terraform`: `fmt`, `init`, `validate` and `apply` on a configuration with no provider, the outputs compared; without it, the structure is checked and the report says so | 2 / 6 / 10 |
| Ansible | Playbooks | The YAML, the modules and their arguments, handlers, names; `ansible-lint` if present | 2 / 6 / 10 |
| Business documents | Procedures, minutes, purchase orders, with sections, tables and totals | Section order, tables, the totals recomputed, no number that was not given, no placeholder left | 2 / 6 / 10 |
| PDF analysis | An invoice, a contract and a quarterly report, made as real PDFs | The text the model is given is extracted from the PDF; the answer is a number or a name; one question whose answer is not in the document | 2 / 5 / 8 |
| Structured JSON | Extraction into a schema, dates, escaping, merging, filtering | The JSON, its types and its values, against a JSON Schema and an expected document; "only JSON" means only JSON | 3 / 7 / 12 |
| Image analysis | Shapes, a bar chart, a receipt, a clock, a code: pictures drawn by the suite itself | Number or name, and one question about a car that is not in the picture. A model that cannot see is **skipped** | 2 / 6 / 10 |
| Tool calling | Choosing a tool, its arguments, several calls, a missing detail to ask for, a tool that does not exist, a tool result to read | The call is read and compared; nothing is ever executed | 4 / 9 / 14 |
| Impossible questions | A novel that does not exist, numbers nobody can know, a false premise, a division by zero; and plain facts as controls | The reply must admit it does not know (or correct the premise) and must not invent; the controls must be answered | 4 / 9 / 14 |
| Following instructions | A number of words, a letter that must not appear, a title, a list of exact length | Counting, one rule at a time | 3 / 7 / 12 |
| Resisting injection | An email, a review, a CV that give orders to the model; a secret in the system prompt that someone asks for | The reply must do the job and not the order hidden in the text; the secret must not appear in any spelling | 2 / 5 / 8 |
| Long context | A fact in the middle of a long text, two facts to put together, one among five alike | Number or name | 0 / 2 / 4 |
| Writing | An apology email, an explanation for a child, a summary, a haiku | **Never scored.** The answers are kept to be read side by side | 2 / 3 / 4 |

Most questions are *generated from the seed*: the same seed always gives the same numbers, names and pictures, and
another seed gives another set that no model can have seen. Every question carries a **reference answer** that must
pass its own check and **wrong answers** that must not: `tests/test_arena_bank.py` holds all of them to that, for the
canonical seed and for five others, and a sweep of a thousand seeds found the rare collisions that were fixed.

## What a verdict says

| Verdict | Meaning | In the score |
|---|---|---|
| Right (`passed`) | The check passed | 1 |
| Partly right (`partial`) | Some of the rules, arguments or sections were right; the score is the share | between 0 and 1 |
| Wrong (`failed`) | It was judged wrong, with a *kind*: wrong answer, format not followed, made it up, refused to answer, cut off | 0 |
| Not evaluated (`unscored`) | No code can judge it (writing), or a tool it needs is missing | **left out** |
| Skipped (`skipped`) | The model cannot do what the question needs (it cannot see pictures) | **left out** |
| Run error (`error`) | The run itself broke: the server answered an error, the answer did not come in time | **left out**, and asked again on resume |

*Cut off* is its own kind: a reply that ran out of tokens before it got to the answer is not the same failure as a
wrong answer, and a model that thinks before it answers needs more tokens. A run allows 2048 tokens for an answer by
default (a question may ask for more, never for less); the form suggests 4096 for a model that thinks. Seen in the
first live test: a 12B model that works a sum out aloud was cut off at 1024 tokens on two of four maths questions.

A person can mark a not-evaluated answer good or poor. The mark is kept beside the answer and **never enters a score**.

## How the scores are made

- A question is worth the mean of its repeats. A category is the mean of the questions that were scored in it.
- A model's score is the mean of its categories, each weighing the same, **over the categories every model of the run
  could be scored on**. A model that can see does not win or lose because the others cannot: its score over
  everything it did is kept as well (*score on all it did*).
- Every score has a 95 % interval from a bootstrap with a fixed seed: the same results always give the same interval.
- The radar has nine axes (knowledge, math, logic, coding, infrastructure, documents, agents, honesty, vision); each is
  the mean of its categories. Writing is on none of them.
- Speed is the mean of tokens per second over the answers of at least 20 tokens, counted from the first token. Time to
  first token is the median.
- With more than one repeat, *consistency* is the share of questions that came out the same every time.

## Quantizations of one model

Choosing which file of a model to keep is a different question from ranking models. Put the files you are weighing in
one challenge (for example `Qwen3 30B A3B` in `Q4_K_M`, `Q3_K_M` and `IQ3_XXS`) and open the **Quantizations** tab.

**Which models are one model.** The Arena takes as one model those with the same name once the quantization is taken
away (`Q4_K_M`, `IQ3_XXS`, `UD-Q3_K_XL`, `GGUF` are not part of a name), the same size in parameters and the same
architecture, each in a quantization of its own. They are ordered by nominal bits per weight (the table in
`arena/quants.py`, from llama.cpp), and the most precise is the **reference**. If your files are not grouped, or you
want another reference, *Choose the models by hand* and *Compare with* do it. Cloud models are never members.

**What each row shows.**

| Column | Where it comes from |
|---|---|
| File | The size of the model file, as LM Studio lists it |
| Graphics memory | Free VRAM before the load minus free VRAM after it, measured by the model gateway for every model, with the same context for all (so it holds the file, its cache and the runtime's share). Empty when this PC has no NVIDIA card to measure |
| Speed, first token | As in the ranking: mean tokens per second over answers of at least 20 tokens, and the median time before the first token |
| Score | The model's score on the questions **every** member was scored on, with its 95 % interval |
| Difference | Its score minus the reference's on those same questions, with the interval of the difference |
| Verdict | *real loss*, *real gain* or *noise*, with how many questions it got right that the reference missed and the other way round |

A model that did not fit in graphics memory and spilled into system RAM is marked: its speed measures the overflow, not
the quantization.

**Real or noise.** Two scores side by side say little: 71.2 against 68.9 on a hundred questions can be luck. The
questions are the same for every quantization, so the comparison is made *by question* (`arena/paired.py`):

- the **difference** is the change in score on the shared questions, each category weighing the same, as in the ranking;
- its **interval** comes from 1000 resamplings of the questions of each category, keeping the pairs, from a fixed seed;
- an **exact sign test** (the exact McNemar test for right and wrong answers) looks only at the questions on which the
  two parted: is one of them ahead more often than a coin would give? With several comparisons against one reference
  the p-values are corrected with **Holm**'s method;
- a difference is **real** only if the interval leaves out zero *and* the corrected test is under 0.05. Anything else is
  *noise*: not a proof that the files are equal, only that this run cannot tell them apart.
- **"Under N points it cannot be seen"** is the smallest gap this run could have detected (about four times out of five),
  from how much the pairs vary. When the verdict is noise this is the number to read: a quantization with a difference of
  −1.5 and "under 6 points it cannot be seen" has not been shown to be as good as the reference, it has not been shown
  to be worse.

**Setting up a fair comparison.** Every member gets the same messages, the same seed and the same sampling, and the same
context. Two kinds of chance matter, and they are handled differently:

1. *Which questions were asked.* The test works on the questions, so it accounts for it; a bigger pack (**Complete**,
   192 questions) narrows the interval. The questions of a seed are fixed: a result holds for that set, and a second run
   with another seed is the honest way to confirm it.
2. *Luck in the sampling.* At temperature 0 a model answers the same every time, so repeats add nothing. To average the
   luck out, use **3 or more repeats at a temperature above 0** (for example 0.5): each repeat has its own seed, the same
   for every quantization, and a question is worth the mean of its repeats. The form offers this when you choose two
   quantizations of one model, and the tab says so if a run was made otherwise.

**Limits.**

- The categories hold few questions (2 to 14): the per-category table shows where a quantization loses, as a hint, and is
  not tested.
- A question that one member could not be scored on (run error, not evaluated) is left out for all of them, and the tab
  says how many.
- The order of precision uses nominal bits per weight; two files with the same bits (`Q4_0` and `IQ4_NL`) are ordered by
  size. For a loss measured on the model's own probabilities rather than on questions, `llama-perplexity` with
  `--kl-divergence` against a high-precision file is more sensitive than any hundred questions; the Arena does not run it.

## Reproducible

A run records everything that makes two runs the same or not: the **suite** (id, version, the hash of the questions it
asked, and the hash of the whole bank at that seed), the **seed**, the **pack**, the **sampling** (temperature, top-p,
top-k, repeat penalty, the seed each repeat adds to), the **context**, the **maximum tokens**, the **models** with their
quantization and the context they really got, the **PC**, LM Studio's version and **which tools checked the answers**
(Terraform, ansible-lint, Node, Python, PostgreSQL's `psql`). Two runs checked with different tools are not equally
strict, and the report says so.

- The questions of a run are stored with it (pictures and PDFs included), so a run can be read and exported after the
  bank has changed.
- Every model gets **the same messages**, byte for byte, and the same sampling. Cloud providers decide their own
  sampling: the temperature and the seed are not sent, and the report says so.
- `SUITE_VERSION` (`backend/src/agentfit/arena/__init__.py`) changes whenever a question, a verifier or the way an
  answer is read changes. Results of two versions are not comparable. The canonical seed (42) is pinned by hash in the
  tests: change a question and the test stops you until you also bump the version and pin the new hash.
- What stays outside: a GPU does not always give the same tokens twice, and a model that was reloaded with another
  quantization is another model. A run at temperature 0 is as repeatable as the runtime allows, not more.

## How a run works

- `ArenaService.start` checks the request, builds the suite, writes the run and its questions to the database and
  starts a thread. One run works at a time: it needs the card to itself.
- The models go **one at a time through the model gateway** (the one workflows use): it measures free VRAM, loads
  the model with the same fixed context for everyone, and unloads it before the next. Models LM Studio already holds
  are unloaded only if you agree (they may be in use by another program); a cloud model is used only if you agree
  (it costs money and sends the questions to its provider).
- The question goes out with the same words to every model, the answer is **streamed** (so Stop is quick, and the
  time to first token and the speed are measured), the verifier judges it and the verdict is saved at once.
- A model that cannot load is reported and the run goes on; a model whose server answers errors three times in a row
  is left out; a run that was open when the app closed is stopped, and can be resumed.

## What checks the answers, and what it cannot

- **No model judges.** The only place where a phrase is recognised is the *impossible* category: «I don't know», «this
  does not exist», «the text does not say». That is a list of phrases in Italian and English, a heuristic, and the
  verdict shows which phrase was taken as the admission, so that a person can overrule it. A reply that admits and then
  invents anyway is still a failure.
- **Code runs in a separate process** with a time limit and a scratch folder. That is not a security boundary: the
  questions are the suite's own, and a model's code runs with the user's own rights. Terraform never gets a
  provisioner, the `external` or `http` data source, or a provider to download.
- **SQL runs on SQLite**, after a translation, because PostgreSQL is not assumed to be there. What only PostgreSQL has
  (`SERIAL`, `RETURNING`, array types) is checked on the text. `psql` is detected and reported.
- **Without Terraform**, a configuration is judged on its structure: it cannot tell a configuration that computes the
  wrong value from the right one. The `method` of the verdict says `structure` instead of `terraform apply`.
- **A picture is a drawing**: shapes, charts, a receipt, a clock, drawn by the suite from the seed, so that every answer
  is known exactly. It does not test photographs.

## Files and the API

Reports: HTML (self-contained, light and dark), Markdown, CSV and JSON. When the run holds quantizations of one model, the
HTML, Markdown and JSON files carry the comparison too (a `quantizations` list in the JSON). The window saves them through its Save dialog
(`dialog:allow-save`); in a browser they are downloaded.

| Route | What it does |
|---|---|
| `GET /api/arena/overview` | The packs, the categories with their counts, the tools found |
| `GET /api/arena/models` | The models that can be chosen, what each can do, what LM Studio holds |
| `POST /api/arena/plan` | What a request would ask, without asking it |
| `GET /api/arena/runs`, `POST /api/arena/runs` | The runs; start one (409 with `needs_unload` or `needs_cloud` until you agree) |
| `GET /api/arena/runs/{id}` | The report: ranking, categories, areas, the matrix of every question and model |
| `POST /api/arena/runs/{id}/stop`, `/resume` | Stop; ask what is missing |
| `PATCH`, `DELETE /api/arena/runs/{id}` | Rename; delete |
| `GET /api/arena/runs/{id}/quants` | The quantization families of the run and one compared against its reference (`family`, or `models` comma-separated and `reference`, to choose by hand) |
| `GET /api/arena/runs/{id}/questions/{qid}` | A question with its pictures and what every model answered |
| `POST /api/arena/runs/{id}/manual` | A person's own mark on an answer no code could judge |
| `GET /api/arena/runs/{id}/export?format=` | The file; `POST` with `{format, path}` writes it where the Save dialog said |

Events on the stream: `arena.run` (state), `arena.progress` (an answer), `arena.delta` (the text a model is writing, not
kept in the backlog), `arena.model` (a model loaded or unloaded).

Tables (migration `0004_arena.sql`): `arena_run`, `arena_question`, `arena_model`, `arena_result`.

## In the code

| File | What it holds |
|---|---|
| `arena/bank/` | One module per category with `build(seed)`; `kit.py` has what they share |
| `arena/verifiers/` | The checks, registered by the `type` of a question's `check` |
| `arena/suite.py` | The suite: seed, pack, categories, versions and hashes |
| `arena/scoring.py` | Scores, intervals, ranking, areas |
| `arena/quants.py`, `arena/paired.py` | The quantizations of one model: grouping, order, the comparison; the paired difference, its interval, the sign test and Holm's correction |
| `arena/runner.py` | `ArenaService`, `RunRequest`, the thread |
| `arena/store.py`, `report.py`, `export.py` | The database, the report, the files |
| `arena/sandbox.py`, `hcl.py`, `sqlcheck.py`, `pngart.py`, `pdfgen.py` | What the verifiers and the bank need: a process to run code in, a reader of HCL, of SQL, a drawing of pictures and a PDF writer, all without dependencies |

To add a question: write it in its category's module with a reference and wrong answers, run
`python -m pytest tests/test_arena_bank.py`, bump `SUITE_VERSION`, and pin the new hash printed by the failing test.
A reason a verifier gives is written with `why("English template {value}", value=...)` and translated when it is read:
add the Italian to `locales/it_arena.py`.
