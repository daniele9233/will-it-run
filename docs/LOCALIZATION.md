# Localization

Will It Run ships in English and Italian. The language is chosen in Settings → General
(*Windows language*, *Italiano*, *English*), on the welcome screen, or from the command
palette. Switching reloads the window and returns to the page you were on.

## How it works

Both sides use the English text as the key, gettext style. There are no string ids to
keep in sync and an untranslated string simply stays English.

| | Interface (`desktop/src`) | Core (`backend/src/agentfit`) |
|---|---|---|
| Call | `t("Explore Models")`, `t("{n} GB free", { n })` | `tr("Fits completely on your GPU.")`, `tr("Leaves {gb:.1f} GB ...", gb=x)` |
| Catalog | `locales/it.txt` → generated `locales/it.ts` | `locales/it.py` |
| Language | Fixed per page load, read from `localStorage` before anything renders | Per request: the interface sends `X-WillItRun-Lang`; background work uses the saved setting |

The interface reads its language synchronously at start, so `t()` is safe in
module-level constants. The saved setting lives in the core; on start the interface
compares the two and reloads once if they differ.

The core translates what it explains to the user: fit reasons, why a quantization was
chosen, accuracy wording, disk-safety verdicts, download errors and notes,
specialization names. Text that is sent to models (role prompts, tool descriptions,
workflow content) is never translated, and logs stay in English.

## Adding or changing a string

Interface:

1. Write the English text in the code: `t("New text")`.
2. Add a line to `desktop/src/locales/it.txt`: `New text ||| Nuovo testo`.
3. Run `python scripts/build_locale.py`. It lists missing keys, checks that every
   `{placeholder}` survives, and writes `it.ts`.

Core:

1. Write `tr("New text {n}", n=3)`.
2. Add the pair to `backend/src/agentfit/locales/it.py`. Format specs such as
   `{gb:.1f}` must be identical on both sides.
3. `python scripts/list_core_strings.py` lists what is still untranslated.

Both are enforced by tests (`desktop/src/lib/i18n.test.ts`,
`backend/tests/test_i18n.py`): a string without an Italian translation, or a translation
that drops a placeholder, fails the suite.

Some core text reaches `tr()` through a variable, where the scan for `tr("...")` cannot
see it: the names and descriptions of workflow templates and of checks. Their English
text lives in the template or check definition and their Italian in the same `it.py`;
`test_text_translated_through_a_variable_is_in_the_catalog` fails when one is missing.

## The Arena

The Arena says *why* an answer was judged as it was, and that sentence is kept in the database as an
English template with its values (`why("Expected «{expected}», found «{found}».", expected=1, found=2)`), not
as text. It is translated when it is read, in the language of whoever reads it, so one run can be read in
Italian and in English. A value that is itself words («Words», «at least») is marked `tl("Words")`. The
Italian for all of it is in `locales/it_arena.py`, merged into `it.py`; the names of categories, areas,
verdicts and kinds of failure are in `arena/labels.py`. `scripts/list_core_strings.py` and
`test_every_core_string_has_an_italian_translation` look at `why(` and `tl(` as well as `tr(`.

## Adding a language

Add a catalog next to the Italian one, register it in `CATALOGS` (and `LANGUAGES` in
`desktop/src/lib/i18n.ts`, `SUPPORTED` in `backend/src/agentfit/i18n.py`), and extend
`system_language()` / `systemLang()` if it should follow the Windows display language.

## Not translated

Team plans and their explanations, workflow validation messages, the Doctor report,
logs and the command line are English. A workflow template's name and description are
translated in the list; what is inside the workflow is not: roles, prompts, edge labels
and the verdicts and summaries of checks are part of what the models read and stay
English. So is everything a model writes: agents answer in the language of the task they
are given.

## Interface size

Settings → General → *Interface size*, or Ctrl + / Ctrl − / Ctrl 0, or Ctrl with the
mouse wheel. Range 80% to 160%. In the desktop app this is the webview's own zoom; in a
plain browser (development) it falls back to CSS zoom.
