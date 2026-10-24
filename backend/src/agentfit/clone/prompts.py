"""What the cloning agent is told.

The kit's skill is given as its author wrote it, with ``$ARGUMENTS`` replaced by the address (and
the person's extra instructions), as opencode does for ``/clone-website <address>``. What is added
here is short: where the skill says "Bash", "sub-agents", "the browser MCP" or "ask the user", this
app has other things, and a model that is told the truth about its tools does not spend its rounds
calling tools it has not got.
"""

from __future__ import annotations

from agentfit.clone.kits import KitStore

ENVIRONMENT = """## How this app runs the skill
You are running `/clone-website {url}`. The instructions above were written for a coding agent with a shell, sub-agents and a browser MCP server. Here you are the whole team: the foreman and the builder. The project is your workspace: every path is relative to it and you cannot leave it. The kit's scripts call this site `{host}` (its folder is `docs/research/{host}/`).
- **Bash is `run_command`.** It takes one command line, or several joined with `&&`, and runs them in the project's root: `node scripts/...`, `npm run <script>`, `npm install`, `npx tsc ...`, `npx eslint ...` and read-only git (status, log, diff, show). No `cd`, and no shell syntax (pipes, redirects, `&`, `$VAR`): run what you need as plain commands and read their output. A command has 10 minutes; pass `timeout_seconds` for a longer one.
- **"Start the clone with `npm start`, in the background"** is `preview_start` with production=true: it builds, serves the project on this PC and returns its address. `preview_start` alone serves `npm run dev`. After you change code, `preview_stop` and `preview_start` again, as the skill says for the production build.
- Playwright's Chromium is already installed: the scripts launch it themselves.
- **You have no sub-agents and no git worktrees.** Where the skill says to dispatch builder agents, build each section yourself, one after the other, from its lint-passing spec (the skill says the same for a tool without sub-agents). Every quality gate still applies.
- **Where the skill says to ask the user** (Phase 0: which pages to clone) call `ask_user` once. Put the discovered pages in the question and give 3 to 6 short options, for example "Only the home page", "Home page + 2 more pages", "Home page + 3 more pages", "All the pages (at most 10)"; the person may also answer in their own words. Wait for the answer and clone exactly what they chose. Do not ask anything you can decide yourself.
- The browser MCP the skill mentions for judgment work is the `browser_*` tools; the scripts are enough without them. A screenshot is a PNG file, not text: `read_file` cannot show it. {sight}
- Custom fonts in `src/app/layout.tsx` (Next.js 16): `import localFont from "next/font/local"` (a default import; there is no `next/font`), with `src: "../../public/fonts/<file>.woff"` or a list of `{ path, weight, style }`, the paths relative to the layout file; `next/font/google` for a Google family. Run `npm run build` after the foundation, as the skill says.
- Write a long file in parts of at most about 10,000 characters: `write_file` with the first part, then `write_file` with append=true for each next one. Change small things with `edit_file`.
- **Your working memory is small.** Only the most recent part of this conversation fits: when it grows past that, what you read earlier is cut. A file you read is not in a file you wrote. So: (1) look into a big JSON file (tokens.json, css.json, assets.json, the manifest) with `json_outline`, and zoom into one part with its `pointer`; do not page through it with `read_file`, and do not write a helper script to inspect it (`node -e` is not available); (2) the moment you have what you need, put it where it belongs (the spec, the component, `docs/research/{host}/NOTES.md`) instead of keeping it in your head; (3) work on one section at a time: `node scripts/manifest.mjs next`, its spec, its component, `manifest.mjs set`; (4) if you lose your place, `node scripts/manifest.mjs resume` and the files already written say where you are: do not start the recon again; (5) if something small will not work after two tries (a font, a colour), take the best value you have and go on; (6) reading is not progress: read a file once, whole, and write; after about ten rounds in a row in which no file changed, this app takes your reading tools away until you write one.
- Never claim a result you did not check: the Completion Report quotes the real numbers (`manifest.mjs status`).
- Answer in the language of the request."""

SIGHT_YES = "To see one, call `look` with its path (the `-review.png` twin is the half-size picture the skill tells you to open; `other` compares two pictures). It costs a model call each time: look at what matters."
SIGHT_NO = "No model that can see is available in this run: judge by the numbers (the scores and bands of `diff.mjs`, the properties `compare.mjs` names, `probe.mjs`)."


def skill_prompt(store: KitStore, *, url: str, host: str, sees: bool, extra: str = "") -> str:
    """The skill with its arguments in, then the note on this app's tools."""
    arguments = url + (f" {extra.strip()}" if extra.strip() else "")
    skill = store.skill().replace("$ARGUMENTS", arguments)
    note = ENVIRONMENT.replace("{url}", url).replace("{host}", host).replace("{sight}", SIGHT_YES if sees else SIGHT_NO)
    return f"{skill}\n\n{note}"
