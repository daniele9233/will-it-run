# Clone a site

An address in, a Next.js project on this PC out. It is the workflow of
[Mahanaicoach/ai-site-cloner](https://github.com/Mahanaicoach/ai-site-cloner) run the way it runs in a
coding agent in the terminal, with the model you choose (a model in LM Studio, or Claude, ChatGPT,
DeepSeek or Gemini with your own API key): the kit's project goes in a folder of yours or in a copy of a
GitHub repository, its packages are installed, and the model follows the kit's `/clone-website` skill.
It opens the site, finds the pages and **asks you which ones to clone**: only the home page, the home page
and two more, three more, all of them.

It is meant for sites you own (a migration, lost source code) and for learning how a site is built. Check a
site's terms first: its text, logos and brand belong to its owner. This is not legal advice.

## The same as in a terminal

What you would type in a coding agent, and what Will It Run does for you:

| In the terminal | Here |
|---|---|
| `git clone https://github.com/Mahanaicoach/ai-site-cloner.git .` | The kit is fetched at one pinned commit and its project files are put in the folder (never over a file that is already there) |
| `npm install` | The same, in the folder, and `npm run setup` for the Chromium the kit's own scripts use |
| `/clone-website https://nome-sito` | The model gets the text of the kit's `clone-website` skill as its instructions, with the address in it |
| The agent asks "the home page, or 2 pages, 3 pages…?" | The question appears on the page, with buttons; you click or write your own answer and the agent goes on |

The skill is given exactly as its author wrote it. Will It Run adds a short note after it
(`backend/src/agentfit/clone/prompts.py`) on how its tools map to the app's: where the skill says Bash,
`run_command`; where it says `npm start`, `preview_start`; where it would spawn sub-agents, the agent does the
work itself, one section after another.

### Licence, and why nothing of it is bundled

The kit is MIT. Even so, its files are not shipped with this program: the app fetches them from GitHub on your
PC (`git fetch` of the pinned commit, which Git checks against the commit's hash; a different commit is
refused). See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## What runs

```
Preparation  ->  Cloner  ->  Final check
```

- **Preparation** (code, no model): checks Node.js (22 or newer, as the kit asks), npm and Git; fetches the
  kit; puts its project in the folder unless the folder already is one; runs `npm install`; runs `npm run
  setup` (downloads Playwright's Chromium, about 170 MB, the first time only).
- **Cloner** (the model): one agent that follows the skill. Phase 0 is the question to you. Then it records the
  site with the kit's scripts (`scripts/extract/page.mjs`, `scripts/manifest.mjs`, ...), takes screenshots at
  desktop and mobile width, writes `docs/research/<site>/` and builds the components in
  `src/components/sites/<site>/`, page by page, checking each against the original. The work is **passes of
  up to 150 tool calls, each a fresh conversation** that resumes from the kit's manifest: a local model's
  context (about 30K tokens for a 27B on a 16 GB card, 12K of it the skill) fills with its own history and
  trims what it read, so in one long conversation it reads the same files again and again (brioni.com: 117
  reads against 14 edits). A round in which anything in the project folder changed, even through a script, is
  progress. After 20 rounds in a row in which nothing changed (counted from the pass's first round) the agent
  keeps only the tools that write, and the question to you, until it changes a file; 4 more rounds without one
  and the pass ends. (Seen on brioni.com with a 27B: 110 rounds of reading, the same 9 KB file over twenty
  times, not one file written, and a closing report that showed it knew what to write. A reminder in the
  conversation did not move it; no reading tool to call did. The first version counted only the agent's own
  file edits and ended a run that was doing real work with the kit's scripts.) Its temperature is 0.6, not
  near zero: greedy decoding is what makes a local model repeat itself endlessly.
- **Final check** (code): `npm run check` (lint, typecheck, production build), and a gate: the kit's own blank
  page builds fine, so a build proves nothing alone. A source file of the site in `src/components/sites/`, a
  page of another route or a changed `src/app/page.tsx` must be there. A check that fails goes back to the
  Cloner, with the end of the log, **while the work moved** since the last check (sections by stage in the
  manifest, specs written, size of the project's source): up to 8 more passes, and a pass that changed
  nothing ends the run. A second gate is the kit's own definition of done: its manifest
  (`docs/research/manifest.json`) moves every section through `discovered`, `extracted`, `specd`, `built`,
  `merged` and `qa_passed`, and a build with sections that are not `qa_passed` goes back to the Cloner with the
  list (the skill is resumable: `node scripts/manifest.mjs status`). If a check still fails the run ends as
  *blocked*, with the reason, instead of "completed": the files written stay in the project and *Improve it*
  resumes from the manifest.

It is a workflow like any other: its live graph is in *Sessions*, models go through the gateway (one at a time
in VRAM; a cloud model costs no VRAM), Stop works, risky actions ask first or are ignored if you chose so.

### The question

While the agent waits for you, the run shows a card on the clone's page ("The agent is waiting for your
answer") and the app notifies you. The agent asks once, in Phase 0, with the pages it found in the site's
menu and 3 to 6 options. Your answer goes to the agent as the tool's result (`POST /api/clone/{id}/answer`).
Stopping the run ends the wait. Your choices stay on the page.

## The agent's tools

Names follow the Playwright MCP server the kit was written for.

| Tool | What it does |
|---|---|
| `browser_navigate`, `browser_resize`, `browser_scroll`, `browser_click`, `browser_hover`, `browser_press_key`, `browser_wait_for` | Drive the page like a visitor (the scroll is the mouse wheel, so smooth-scroll libraries and scroll-linked animations react) |
| `browser_take_screenshot` | A PNG in the project (`docs/design-references/` unless the agent names a place under `docs/` or `.tasks/`) |
| `browser_snapshot`, `browser_evaluate` | The page's structure; any JavaScript in the page, for computed styles, sizes and sources |
| `browser_network_requests`, `browser_console_messages` | What the page loaded and what it logged |
| `download_asset` | A file the page uses, with the page's cookies; it warns when the server's type and the file's name disagree (a `.png` that is AVIF) |
| `look` | A picture described by a model that can see (see below) |
| `run_command` | The skill's Bash, with a fixed list of commands (below) |
| `json_outline` | The shape of a big JSON file (tokens.json, css.json, the manifest) in a few lines, with a `pointer` to zoom in; a part under 3000 characters is shown whole, with every value: the agent's memory is too small to page through such files |
| `preview_start`, `preview_stop` | Serve the project on 127.0.0.1: `production=true` builds first and serves the build, as `npm start` does |
| `ask_user` | The question to you |
| `read_file`, `write_file`, `edit_file`, `list_dir`, `delete_file` | The workspace, as for every team |

Everything the tools write stays inside the run's folder. Only `http` and `https` addresses open.

### `run_command`

It is not a shell. The line is split on `&&`, `;` and new lines, each command is checked, and they run in
order, stopping at the first failure. Allowed:

- `node scripts/<file>.mjs ...`: the project's own scripts, inside the workspace (no `node -e`, no file outside);
- `npm run <script>` for a script of `package.json` that ends (not `dev` or `start`: use `preview_start`),
  `npm install` and `npm ci` (a few options; adding a package asks first), `npm ls`;
- `npx tsc`, `npx eslint`, `npx next build|lint|info`, `npx playwright install ...`: programs the project already
  has, never downloaded;
- `git status|log|diff|show|rev-parse|ls-files|describe|blame` and `git branch` to list: read-only.

Pipes, redirects, `&`, `$VARIABLES` and quotes left open are refused with a message that says what to do
instead. A long output keeps its beginning and its end.

### Models that cannot see

`look` calls a second model: the agent's own, when it can see (the picker says *sees*), else the *model that
sees* you choose, else none. Without one the agent is not offered `look` and measures instead
(`browser_evaluate`, `browser_snapshot`, the kit's `diff.mjs`). Cloud models: Claude, ChatGPT and Gemini see;
DeepSeek's chat models do not.

## Where the project goes

| You choose | The project is built in |
|---|---|
| nothing | a folder of its own in Will It Run's data (`workspaces\clone-<id>`), removed with the clone |
| an empty folder, or a project of the kit | that folder |
| any other folder | a subfolder named after the site |
| a GitHub repository | a copy of it in Will It Run's data (`workspaces\repos`); nothing is pushed unless you approve it |

In a folder of yours, a file that was there before is not overwritten without asking (*ask before
overwriting*); files the preparation copied from the kit are not "yours" and are rewritten freely. A folder or
repository of yours is never deleted with the clone.

## Getting ready

The browser engine is installed up front: Playwright's core library (`playwright-core`, pinned), in
`%LOCALAPPDATA%\Will It Run\Data\tools\clone-browser`, by `npm install --prefix`. It drives the Chrome or Edge
you already have for the agent's browser tools. The kit is fetched at the first clone (a few seconds), and its
`npm run setup` downloads a Chromium of Playwright's own for the kit's scripts. Needs Node.js 22 or newer, npm
and Git on the PC. Windows 11 always has Edge.

## Known limits

- Scroll-linked motion, WebGL, Lottie and canvas art are what the kit can do by observing; a small local model
  will often get the layout right and the motion approximately. Frontier models do better; try the same site
  with both.
- A site behind a login or a CAPTCHA is not cloned: the agent stops and says so. The agent's browser tools use
  your Chrome or Edge, headless, introducing itself as a normal one (a headless Chrome says "HeadlessChrome",
  and Akamai-type protection answered 403 to it on brioni.com). The kit's own scripts use Playwright's Chromium
  with the user agent the kit chose; a site with stronger checks can still refuse them.
- The skill describes sub-agents building sections in parallel in separate worktrees. Here one agent does all of
  it, in order: a run takes from minutes (a frontier model) to over an hour (a 27B on a busy GPU), and a small
  model may get lost on a page with many sections. Choosing fewer pages when it asks helps.
- A model with a short context fills it on a long page. Use a model with at least 32k tokens of context, or a
  cloud model.
