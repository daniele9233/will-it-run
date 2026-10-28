# Master prompts and Site DNA for Clone a site: design

Status: draft for the owner's review. Written 2026-10-28. Not committed (AGENTS.md: commit and push only when asked).

## 1. Purpose and scope

**Purpose.** Let the owner recreate websites of the quality that curated galleries show (recent.design, footer.design, inspora.design,
sceneai.art). The app measures a reference site, picks from a library of fifty original master prompts the ones that fit what it
measured, and fills them with the measured facts. The person pastes the result into Claude Code or another AI. Free, local, automatic:
no paid service, no account, no key, and no model is used by the app's own part.

**What was asked** (2026-10-28, in Italian): are the skills of 17 listed repositories in the app; or add the best others as of that
date; or use the notes of an article on code-rendered motion design; or write, inside Clone a site, a section with about fifty master
AI prompts; or find another way. "Do not be superficial."

**Scope chosen** (asked, answered): the library plus a site DNA, with the prompt pasted by the person into an external AI. Running a
prompt inside the app, capturing and measuring behaviour on the exact copy, and the article's video pipeline are out (section 7).

**Assumptions** (the owner did not state them; they may correct them):

1. The limit that matters in the exact copy is what the page *does*: CLONE.md lists menus, carousels, forms, hover and scroll motion as
   not copied. The prompts give it back.
2. Prompts must be original. sceneai.art sells its prompts; MotionSites keeps the copyright of its example prompts.
3. Prompt bodies are English (they go to models). The interface is Italian-capable like the rest of the app.

**Success criteria.**

1. 50 prompts exist, one file each, and pass the library tests. None is shown as tested without a proof record.
2. `dna.mjs` run on the four reference sites reports every fact of section 5.2 (it may report more). What it cannot measure is `null`
   with a line in `limits`.
3. For each of those four DNAs `compose()` returns a prompt of at most 12K estimated tokens, with a reason for every recommended
   recipe. A site that matches no rule gets an empty list and the response says so.
4. The three new skill packs install at pinned commits, the excluded folders are absent, and "Save into the project" puts the prompt
   and the skills in the chosen folder (an integration test follows the path from the route to the files).
5. The real runs of section 5.3 are recorded: at least two composed prompts built by a real builder, build passing, screenshots at
   390, 768 and 1440 px, and `dna_match` reported with what it does not cover.
6. `python scripts/check.py --all` passes and the Italian catalogs are complete.

## 2. What exists today (read in this session)

- `clone/skills.py`: `SkillPack` (id, repo, commit, license, summary, skills, root) and `PACKS` with five packs (gsap, frontend-design,
  vercel, threejs, playwright). `SkillStore.ensure` fetches one commit with Git and checks it against the hash; `copy_into` copies skills
  to `<project>/.skills/<name>/`; `excerpt` picks a few sentences by phrase. The only reader of a skill is the motion step of "From a
  picture" (two sentences). Lesson (memory #116, 2026-10-28): the skills were copied and no model saw one, because nothing called the
  function that wrote their line. Every new piece here has a test that follows the call from the route to the result.
- `clone/studio.py`: `CloneStudio.start` (modes `replica` and `agent`) and `start_picture`; an entry per clone in the key-value store, runs
  in `RunManager`; `list()` takes status and accuracy from the last run; `_shots()` has one branch per mode.
- `clone/pipeline.py`: `definition()` builds a workflow of checks; `register()` adds check kinds with `CheckSpec`.
  `clone/capture.mjs` has the contract any browser script follows: `node script.mjs <config.json>`, env `WIR_BROWSER_DIR`, JSON lines on
  stdout (`progress`, `done`, `fatal`). The capture drops the page's scripts, so it records no library, no script list and no runtime
  behaviour.
- `api/clone_routes.py`: the skills routes (`/api/clone/skills`, `/api/clone/skills/install`) are registered before the
  `/api/clone/{study_id}` routes, which would otherwise take them.
- `desktop`: `CloneMode = "replica" | "agent" | "picture"` (`lib/cloneSite.ts:39`); `CloneSite` renders `StartPanel`, which renders
  `MethodCard` and `PictureStart` (with `SkillsCard`); `Tabs` is in `components/ui.tsx`.
- `scripts/build.ps1` freezes the core with `--collect-data agentfit`: data files inside the package (like `capture.mjs`) travel with it.
- None of the 17 repositories is in the app. The only "Three.js" is `cesartevisual/threejs-skills`.

## 3. Evidence

### 3.1 The 17 repositories (GitHub, 2026-10-28)

| Group | Repository | Licence | Decision |
|---|---|---|---|
| Agent skills | `Leonxlnx/taste-skill` (104k stars) | MIT | Add: text and data, scripts excluded |
| | `pbakaus/impeccable` (91k) | Apache-2.0 | Add: `SKILL.md` and `reference/`, scripts excluded |
| | `Motionsites-AI/claude-skill` (0.6k) | MIT, except `examples/` and `media/` | Add the MIT parts only |
| Video | `heygen-com/hyperframes`, `remotion-dev/remotion` (+ `remotion-dev/skills`, no licence) | Apache-2.0 / not recognised by GitHub | Not added: video is out of scope |
| Libraries (12) | three.js, pixijs, ogl, p5.js, paper.js, matter-js, rapier, anime, react-spring, tsparticles, gl-transitions, FFmpeg | various | Not skills. Recipes name them at a pinned version. `ogl` has had no push since 2025-04-13: check before recommending |

Also seen and not added: Awwwards-style skills found by search (33 stars at most); `luukalleman/clone-study` (MIT, 26 stars: a
capture, brief and diff method, which the app already covers better); `nexu-io/open-design` (104k, an app, not a skill; not evaluated).
HEAD commits observed on 2026-10-28, to be pinned again at implementation: taste-skill `12af471f627f866411b4bd785b19646d3672784c (tag v2.1.5)`,
MotionSites `faa30d512e3973e58675c72101a7ef42ed37ae83`, impeccable `3e3a9179e01a60265000ce239c0a99803ec1e60d`.

### 3.2 The four sites (built-in browser, landing views, 2026-10-28)

| Site | Stack | Media and motion |
|---|---|---|
| recent.design/websites | A Vite-style build (`assets/index-*.js`), 65 JS files, one external script (analytics) | 20 `<video>` (`.../recording/480.mp4`, loop, muted, preload metadata, not autoplay, started by JS); 0 canvas; `startViewTransition` in the main bundle; Inter and Departure Mono; no GSAP or Lenis signature |
| footer.design | Webflow (`Webflow` and `jQuery` globals, `w-mod-ix`) | Lottie signature in one script; 0 video, 0 canvas; Inter; chip filters (Typography, Small Type, Illustrative, Grid, Flat, Animated) |
| inspora.design/?category=Web | Next.js, PostHog | GSAP signature in 2 chunks; 15 videos (the first autoplays, the others preload none); 6 scroll-snap rules; 235 CSS custom properties; 0 canvas; chips (All, Web, Branding, Product, Motion, Illustration, 3D) |
| sceneai.art | Next.js | `lenis` class on `<html>` and a Lenis signature; 10 videos (preload none, lazy); 0 canvas; Geist. It is a paid prompt library: 170+ landing and section prompts, 80+ animated backgrounds, each with Preview and Copy Prompt, sorted by section type |

In the pages opened, none of the four has a canvas or WebGL. A text scan matched "rapier" in recent.design's bundle: it is Sentry's
`browserapierrors`, a false positive, and the reason the signature rule of 4.2 exists.

### 3.3 Licences and scripts

- **taste-skill** (MIT): one skill (`SKILL.md`, 39,537 bytes), `references/` (`motion.md` 55 KB, `imagery.md` 51 KB, ...), `library/*.json`
  (`sites.json` 869 KB describing 247 gallery sites, `sections.json`, `heroes.json`, `palettes.json`, `type.json`, `directions.json`,
  `motions.json`) and `scripts/*.mjs`. The library carries numbers ("about 900 ms, the second line 110 ms after the first").
- **MotionSites**: part 1 of its LICENSE is MIT for everything except `examples/` and `media/`. Part 2: those are copyright MotionSites;
  they may be used to build websites with the skill, also for clients, and may not be resold or redistributed as a prompt or media
  collection. So only the MIT parts are fetched, and the library never copies or paraphrases the examples. Its 7 technique files
  (`references/techniques/*.md`, about 275 KB in total) are MIT.
- **impeccable** (Apache-2.0): `.claude/skills/impeccable/` holds `SKILL.md` (12 KB) and `reference/*.md`. Its 62 deterministic rules
  run in a native engine, which is not used here.
- **Scripts audit** (read, not run): taste-skill `scripts/lib/cdp.mjs` uses `child_process.spawn`; `assets.mjs` makes 5 `fetch()` calls
  to public image APIs (Wikimedia, NASA, museum APIs, Openverse, archive.org); `draw.mjs` mentions picsum.photos; `inspect.mjs` and
  `scenes.mjs` talk to 127.0.0.1; `lint.mjs`, `lib/probe.mjs` and `lib/rules.mjs` use neither network nor processes. MotionSites
  `scripts/shoot.py` uses `subprocess` and localhost. Nothing looks hostile, but it is third-party code an agent could run on the
  owner's PC, so it is not copied until someone has verified it (4.5).

### 3.4 The article (code-rendered motion design)

Adopted: the brief as a production document (the shared blocks of 4.1); the grammar of a reference taken, never its content (F01 and the
DNA); a state list with a purpose for each state (the States block); approval gates before code (4.1); real assets only; looking at
the result at several sizes (F03). Not adopted: a model that scores its own frames, which conflicts with "no model judges" (the evidence
here is a build, screenshots and `dna_match`, all measured by code); the video pipeline (a seekable frame function, ffmpeg, synthesized
audio), which is another product.

## 4. Design

Decisions and why:

| Decision | Why |
|---|---|
| A curated static library, not prompts generated at run time from the skills' data | Quality is reviewed prompt by prompt; no dependency on Git or on installed packs; testable |
| A deterministic composer, no model | A model can invent facts about a site; the owner wants answers grounded in generated facts |
| DNA in its own script and mode (`study`), not inside `capture.mjs` | No regression risk for the exact copy; reuses the run infrastructure |
| Recipes in files, shared blocks in one file, assembled by code | The asset policy and the hard lines live in one place and cannot drift across 50 files |
| Third-party skill scripts are not copied | Unverified code; section 3.3 |
| Prompt bodies in English; titles, summaries, reasons and UI in Italian-capable text | Bodies go to models; the rest is read by the owner |

### 4.1 The library

Location: `backend/src/agentfit/clone/prompt_library/` (travels with the package, see 4.6): `shared.md`, `proofs.json`, and one file per
prompt, `<ID>-<slug>.md`: YAML front matter, then a body in English. The loader, the validator and the standalone assembly are `clone/library.py` (`clone/prompts.py` already exists: it is the kit agent's prompt).

```
---
id: C01
slug: smooth-scroll-lenis-gsap
group: motion                 # pages | gallery | motion | surfaces | three-d | process | after-copy
title: Smooth scroll with Lenis, synced with GSAP ScrollTrigger
title_it: Scroll fluido con Lenis, sincronizzato con GSAP ScrollTrigger
summary: One line of what the person gets.
summary_it: Una riga su cosa ottiene la persona.
tags: [lenis, gsap, scroll]
size: M                       # S, M, L: how much an AI has to write (1 file; up to 4 files; up to 8)
libs: {lenis: "<version looked up when written>", gsap: "3.13.0"}
skills: [gsap-scrolltrigger, gsap-react]    # installed skills it cites by name
---
## Goal
## Stack
## States
## Motion
## Hard lines
## Verify
```

Only the blocks that are specific to the recipe are in the file. **`shared.md`** holds what every master prompt says, once: the *gates*
(1: write `docs/style_guide.md` and `docs/spec.md` from the DNA and stop for approval, or write them and go on if the builder cannot pause; 2: stills of every section, looked at; 3: the full
build and the checks), the *asset policy* (real assets only; placeholders marked `[PLACEHOLDER: ...]`; never invent product screens,
metrics, logos, names or testimonials; no hotlinked third-party media), the *base hard lines* (take the grammar of the reference, never
its text, logos, photographs or videos; banned defaults: a centred title over a soft gradient, a purple-to-blue gradient, everything
fading in on load, a label above three icon cards, a card inside a card; animate transform and opacity only; respect
`prefers-reduced-motion`; no layout shift from an animation), the *base verify* (build passes; no console error; screenshots at 390, 768
and 1440 px; no horizontal overflow; say what was not checked) and *deliver* (files, and a README that states what was verified).

Required blocks: Goal, Stack, States and Verify for every group except `process` (Goal, Steps, Verify). Motion is required for `motion`,
`surfaces`, `three-d` and for the hero and navigation prompts, and may say "Not applicable" elsewhere. The numbers in Motion are the
point: durations, named easings, staggers, distances, triggers, and the reduced-motion fallback. States is the list of states the thing
goes through: for each, how it is entered, how it is left and why it exists (a state with no reason is cut). The IDs of the table below
follow the order of the titles.

Placeholders are a closed set, tested: `{{host}}`, `{{palette}}`, `{{fonts}}`, `{{type_scale}}`, `{{spacing}}`, `{{motion}}`,
`{{sections}}`, `{{libs}}`, `{{viewport}}`. With no DNA, a placeholder renders as `Unknown: decide it and say so`, never as a default.

Limits (tested): a recipe at most 6,000 characters; `shared.md` at most 3,000; code blocks must parse as TypeScript (the compiler of the
desktop package). `LIBRARY_VERSION` is a string in `library.py` and the test pins the hash of the prompt files (blind to a BOM and to Windows line endings), like `SUITE_VERSION` and
the bank test of the Arena; changing a prompt means bumping the version and the hash.

**`proofs.json`** starts empty. A proof is `{id, builder, date, build_ok, widths, dna_match, notes}`. The interface shows "Not tested yet"
until a proof exists, then "Tested with <builder> on <date>". It is outside the hash.

**Authoring rules.** Written by hand, original, from measured facts (the DNA fixtures of the four sites), the MIT data of taste-skill and
MotionSites' technique files, and the documentation of each library at its pinned version. Nothing is copied or paraphrased from a
protected prompt collection. A library is named only with a version that was looked up when the prompt was written.

**The 50 prompts** (titles are proposals for the owner's review):

| Group | ID | Title |
|---|---|---|
| Pages and sections (10) | A01-A10 | hero with a looping video background; hero where the name is the architecture; split hero (headline and product frame); glass navbar that hides on scroll; bento feature grid; sticky scroll story; pricing tiers with toggle; two-row testimonial marquee; footer with a giant wordmark; login page |
| Galleries (7) | B01-B07 | grid or masonry with chip filters and sort; cards that play a muted video on hover or in view; detail dialog with View Transitions; "Editor's choice" feature block; subscribe strip with states; paged loading with skeletons; dark and light theme from tokens |
| Motion and scroll (12) | C01-C12 | Lenis with ScrollTrigger; masked line headline; word-by-word blur-in; pinned story with `position: sticky`; horizontal scroll section; parallax layers; scroll-scrubbed video; marquee; magnetic buttons and cursor follower; image shutter reveal; counters; page transitions with the View Transitions API |
| Backgrounds and surfaces (7) | D01-D07 | animated mesh gradient; aurora; grain overlay; liquid glass; full-screen shader background; particle field; WebGL image distortion on hover |
| 3D and interactive (3) | E01-E03 | Three.js product viewer; scroll-driven 3D camera; physics hero |
| Process (5) | F01-F05 | study a reference into a style guide (grammar, never content); write the spec from a brief; look-and-fix loop on measured evidence; asset audit; accessibility and performance pass |
| After the exact copy (6) | G01-G06 | give the frozen menu and dialogs their behaviour; carousels; forms with validation and a submit stub; replace a frozen canvas with a real effect; extract tokens and components; replace the original's text, brand and media with the owner's |

### 4.2 Site DNA

`clone/dna.mjs` follows the contract of `capture.mjs` (config JSON, `WIR_BROWSER_DIR`, JSON lines). It opens the address as the visitor's
Chrome (the user agent fix of `capture.mjs`), at 1440x900, lets the page settle, scrolls it once, hides consent banners with an
attribute (never removing nodes), and measures. It only collects: it writes `dna-raw.json` (counts, histograms, the library signatures it matched). `clone/dna.py` interprets it: `analyze(raw)` returns the Pydantic model `SiteDNA` (schema 1), and the module holds `style_guide(dna)`
(Markdown written by code from the JSON) and `dna_match(a, b)`. The signature table is data (`clone/dna_signatures.json`: plain regexes that mean the same in JavaScript and in Python), applied by `dna.mjs` and tested through Node.

| Field | Measured by |
|---|---|
| `stack` | URL patterns of the loaded scripts and page globals: Next, Nuxt, Astro, Vite, Webflow, Framer, WordPress, Shopify |
| `libs` | signatures in the loaded scripts, fetched in the page; see below |
| `tokens` | computed styles of visible elements: palette (colours quantized and weighted by area, with roles by rule: the largest area is the background, the most used text colour is the text, the most saturated non-neutral colour is the accent), font families with share and weights, the distinct font sizes, the most common paddings, margins and gaps, radii, shadows, the container width, the breakpoints of the style sheets |
| `sections` | the header, each block of `main`, the footer: tag, height, role by rule (hero, gallery, footer), counts of pictures, videos, canvases, heading size |
| `motion` | transition durations and easings (histogram), `@keyframes` names, sticky and fixed elements, scroll-snap, scroll and view timelines, View Transitions, smooth-scroll engine, blur, mask, clip-path and blend-mode use, videos (count, loop, muted, autoplay, preload, started by script), canvases (context type, size), Lottie players |
| `patterns` | DOM rules: chip filters (four or more buttons in a row, one active, a grid below), dialogs, carousels, masonry, sticky header, newsletter form |
| `probes` | scroll: elements that move differently from the page between two scroll positions (parallax or pinned), sampled; hover: the first six card-like elements, what their computed style changes and whether a video inside starts |
| `limits` | what could not be measured and why, in words the owner reads (`tr()`) |

Every unknown is `null`, never a convenient value. The file never holds the page's text, logos, picture addresses or video addresses.

**Signature rule.** A library is reported only on a specific signature: a page global (`window.gsap`), a class on `<html>` (`lenis`), a
package path (`@dimforge/rapier`, `node_modules/three`), or a set of distinctive identifiers together (`WebGLRenderer` and
`BufferGeometry` for three.js); never a bare word. The `browserapierrors` string of recent.design is a regression test for it. A bundler
can still hide a library: that is a line in `limits`, not a claim.

**`dna_match(a, b)`** returns each part and an overall number, all from code: palette (perceptual distance between the top five colours,
matched one to one), fonts (overlap of families), libs (overlap), motion (parity of each feature present in `a`), sections (ratio of
counts). It says what it does not cover: layout, text, and the feel of the motion.

### 4.3 The composer

`clone/library.py` holds the loader, the validator and the standalone assembly; `clone/compose.py` holds the rules and `compose(dna, picked)`, which returns `{prompt, used, reasons, dropped, tokens}`.

- **Rules** are a table in code (not in the prompt files), each with the DNA condition, the recipe and the reason shown to the owner
  (`tr()`). Initial set, extended as prompts are written:

| DNA says | Recommends | Reason shown |
|---|---|---|
| 6 or more videos, most loop and muted | B02 | "N muted looping videos" |
| first section holds an autoplay muted video | A01 | "the first screen has a looping video" |
| Lenis signature or class | C01 | "Lenis smooth scroll" |
| chip filters pattern | B01 | "a row of filter chips above a grid" |
| View Transitions used | B03, C12 | "the page uses the View Transitions API" |
| sticky header with backdrop blur | A04 | "a sticky header with blur" |
| a WebGL canvas | D05 if it covers the viewport, D07 if it sits inside a card or picture, E01 if three.js is detected and it is neither | "N WebGL canvases" |
| pinned elements found by the scroll probe | C04 | "N pinned elements" |
| three or more parallax-like elements | C06 | "N elements move against the page" |
| a keyframes name with marquee or ticker | C08 | "a marquee animation" |
| a footer heading of 12 vw or more | A09 | "a giant footer wordmark" |
| a hero heading of 14 vw or more | A02 | "a hero set at the width of the screen" |
| newsletter form | B05 | "a subscribe form" |

  F01 and F03 are always added, with the reason "every master prompt starts from a style guide and ends with a look-and-fix loop". At
  most six recommended recipes besides those. If no rule matches, the list is empty and the response says so.
- **Filling**: the placeholders take measured values; unknown ones render as `Unknown: decide it and say so`.
- **Assembly**: opening (goal, and "take the grammar, never the content") + a DNA block + the picked recipes (Goal, Stack, States,
  Motion, extra hard lines) + the shared blocks + the gates.
- **Budget**: 12K estimated tokens (characters / 4). Over it, the lowest-priority recipes are dropped, named in `dropped`, and the
  interface says so.

### 4.4 Study mode, routes, interface

**Mode `study`** is a fourth way beside exact copy, agent and picture. `CloneStudio.start_study(url)` (like `start_picture`) creates an
entry with `mode: "study"` and starts a one-check workflow, `dna.definition(url)`, whose check kind `clone_dna` is registered in
`dna.register()` like `pipeline.register()` (at module level, as they do). The workflow validator requires at least one agent today; it is relaxed for a workflow that has checks (one test each way), so a study stays a run and uses no model. It builds no project. It writes `docs/research/<host>/dna/dna.json`,
`style_guide.md` and a small `thumb.jpg` of the first screen in the entry's workspace. `start()` sends a new run of a `study` entry
to `start_study`, as it does for `picture`. `list()` shows the entry with its badge; `_shots()` returns the thumbnail. The target is
about 15 s, to be measured, not promised. A study is a run like the others: its graph is in Sessions and Stop works.

**Routes** (new `api/prompt_routes.py`, installed in `api/server.py` before `clone_routes`, so that `/api/clone/prompts` is not taken by `/api/clone/{study_id}`):
`GET /api/clone/prompts` (id, group, title, summary, tags, size, skills, proof), `GET /api/clone/prompts/{id}` (the text),
`POST /api/clone/study` `{url}`, `GET /api/clone/{id}/dna`, `POST /api/clone/{id}/compose` `{picked?}` (the composed prompt with
reasons, tokens, dropped), `POST /api/clone/{id}/prompts/save` `{text, target?}`, and last in the order of work
`GET /api/clone/{id}/dna/match?with=<id>`.

**Save into the project** writes `docs/prompts/<host>-master.md` and copies the skills the prompts cite into `.claude/skills/<name>/`
(and `.skills/<name>/`, as today) of the target folder. The target is a clone chosen in a list, by default the latest clone of the same
host; with none, the study's own folder. Claude Code finds `.claude/skills/` by itself; the real run of 5.3 confirms it.

**Interface.** `CloneSite` gets two tabs, *Clone* and *Master prompts* (`Tabs`). `lib/cloneSite.ts` adds `"study"` to `CloneMode` (and
to `stagesOf` and `stageText`). New `pages/clone/MasterPrompts.tsx` and `lib/masterPrompts.ts` (logic and tests):

```
From a site: [ https://...  ] [Study]    or choose a studied site v
DNA: palette swatches, fonts, libraries, motion facts, limits
Recommended: [x] Cards that play on hover - "20 muted looping videos"   ...
[ composed prompt ]                ~7,400 tokens   [Copy] [Save into the project]
Library (50): [All|Pages|Galleries|Motion|Surfaces|3D|Process|After the copy]  search
cards: title, summary, size, "Not tested yet"   -> drawer with the text and [Copy]
```

A study card in "Sites cloned" shows its badge. A clone's detail view gets "Master prompts from this site", which opens the tab with the
site studied. Last in the order of work: "Compare with another study" showing `dna_match`.

### 4.5 Skills

Three packs added to `PACKS` at commits pinned and verified like the others, with their licences kept:

| Pack | Repository (root) | Copied | Excluded |
|---|---|---|---|
| `taste` | `Leonxlnx/taste-skill` (`skills`) | `taste-skill/`: `SKILL.md`, `references/`, `library/` | `scripts/`, `assets/` |
| `motionsites` | `Motionsites-AI/claude-skill` (`plugins/motionsites-web-builder/skills`) | `motionsites-web-builder/`: `SKILL.md`, `references/` | `examples/`, `media/`, `scripts/` |
| `impeccable` | `pbakaus/impeccable` (`.claude/skills`) | `impeccable/`: `SKILL.md`, `reference/` | `scripts/` |

`SkillPack` gets an `exclude` field (names of entries directly inside a skill's folder that `ensure` does not copy) and a `purpose` (`picture` for the five existing packs, `design` for the new ones). `install()`, `copy_into()` and `ready` default to the picture packs, so "From a picture" fetches and copies exactly what it does today; design packs are asked for by id, and a new `install_into()` copies them to `.skills/` and `.claude/skills/`. A skill copied into `.claude/skills/` carries a marker file: a folder without it (the person's own skill of the same name) is left alone and reported. The skills are cited by folder name in the
prompts and copied into a project on request. They are never put whole into a small model: the kit's 12K-token skill is the lesson that
broke a 27B model (DECISIONS, 2026-10-27). Taste-skill's rules (`rules.mjs`, `probe.mjs`) are the candidate for a later quality gate
(section 7). The 12 libraries are not skills: a recipe names them with a pinned version, and the generated project installs them with npm.

### 4.6 Texts, documents, notices, packaging

- Core texts the owner reads (reasons, `limits`, errors) go through `tr()` with the pair in `locales/it.py`; the titles and summaries of the prompts carry their Italian in the file (`title_it`, `summary_it`: data, not literals the string lister could find). Prompt
  bodies, the shared blocks and tool text stay English. Interface strings are `t("English")` with a line in `desktop/src/locales/it.txt`.
- `docs/CLONE.md`: the mode table gets Study; new sections *Master prompts* and *Site DNA*. `docs/DECISIONS.md`: the decisions of section 4.
  `docs/LESSONS.md`: the `browserapierrors` false positive. `THIRD_PARTY_NOTICES.md`: the three packs, with MotionSites' split licence
  quoted. `docs/map/` is generated, never edited.
- Packaging: new files live under `backend/src/agentfit/clone/`, so `--collect-data agentfit` carries them. The plan includes a check
  of the frozen build, because a missing data file shows up only there.
- Import contracts: the new modules are in `clone`, which depends on `core` and `workflows`, as now.

## 5. Tests and real runs

### 5.1 Tests (no network, GPU or LM Studio; `tmp_path`, fakes)

- **Library**: ids unique and in the table of 4.1; required blocks per group; placeholders in the closed set; length limits; code
  blocks parse; the hash pin; every composer rule names a prompt that exists.
- **DNA model and match**: `SiteDNA` validation; `null` for unknown; `dna_match` on identical, close and different fixtures.
- **Signatures**: a table of true and false cases, including `browserapierrors` and a bare "three" in prose.
- **Composer**: the four real DNAs as fixtures (numbers and names only, no text, no logos, no addresses of media); reasons; the empty
  case; the budget and `dropped`; unknown values.
- **`dna.mjs`**: an opt-in test (`WIR_LIVE_BROWSER`, like `test_clone_replica.py`) against a page served from `127.0.0.1` with a muted
  looping video, a sticky header, a `lenis` class, chip filters and a canvas; it asserts what it reports.
- **Skills**: the three packs with the Git fake (`git_that_serves`), `exclude` honoured, the licence kept, the copy into `.claude/skills/`.
- **Integration**: from `POST .../prompts/save` to the files in the folder (the lesson of #116).
- **Routes and study mode**: the routes return the shapes; a `study` entry lists, runs again and deletes.
- **UI**: vitest for the pure functions of `lib/masterPrompts.ts`; `lib/i18n.test.ts` for the catalog.
- `python scripts/check.py` while working; `--all` before the end; `lint-imports`; `python -X utf8 scripts/build_locale.py`.

### 5.2 Acceptance of the DNA on the four sites (hand measurements of 2026-10-28)

`dna.mjs` must report at least: recent.design: 20 videos (loop, muted), 0 canvas, View Transitions, Inter and Departure Mono, no GSAP or
Lenis. footer.design: Webflow, jQuery, Lottie, 0 video, 0 canvas, the chip filters. inspora.design: Next.js, GSAP, 15 videos, scroll-snap,
0 canvas, the chips. sceneai.art: Next.js, Lenis, 10 videos, 0 canvas, Geist. More is welcome; a difference is a failure to explain
(the page may have changed, or a signature is wrong).

### 5.3 Real runs (the owner judges by what runs)

Isolated home as in the live-test recipe (scratch `AGENTFIT_HOME`, core on 8766). (1) `dna.mjs` on the four sites; the numbers are
compared with 5.2 and the run time is reported. (2) The four prompts composed: tokens and reasons reported. (3) At least two composed
prompts given to a real builder (a strong cloud model or a large local one) in a scratch folder: build passes, screenshots at 390, 768
and 1440 px, the DNA of the result and `dna_match` against the reference. The proof records go in `proofs.json`. Claude Code's discovery
of `.claude/skills/` is checked in the same run. What the numbers do not cover is written next to them: whether the page looks like the
reference to an eye, fine behaviour, inner pages.

## 6. Risks and open points

- **Volume.** Fifty prompts written with care is the bulk of the work. The plan groups them in batches of one group each, with the tests
  green before the next, and can give a batch to a separate worker with the template and the DNA fixtures.
- **Signatures** can miss or mislabel; minified code hides names. The rule is specific signatures, and `limits` says what is unsure.
- **Sites with bot protection** can refuse the visit; the run ends and says what the site answered, as the capture does.
- **Legal.** The prompts take a grammar, never content; the interface says to check the site's terms (as CLONE.md does). Authoring from
  protected collections is forbidden by the rule of 4.1 and checked by the owner's review, not by a test.
- **Pack size.** taste-skill with its `library/` is about 2.4 MB on disk; confirm at implementation. Local only, once.
- **Claude Code discovery** of `.claude/skills/` is believed, and checked in 5.3.
- **Duplicated code.** `dna.mjs` repeats the launch and the banner hiding of `capture.mjs`. Extract a shared module once both are stable,
  not now (no change to the exact copy).
- **Library versions** change; a recipe's pinned versions are looked up when it is written and appear in its front matter.

## 7. Out of scope (candidates for later)

Capturing and measuring behaviour (hover, scroll, video) on the exact copy, and giving it back; running a prompt inside the app with a
model and measuring the result; taste-skill's and impeccable's deterministic rules as a quality gate on a built page (after their
scripts are verified); the article's video pipeline; per-prompt translations of the bodies.

## 8. Order of work (the plan will detail it)

1. Skills (three packs, `exclude`, `.claude/skills/`), the library structure and validator, five pilot prompts, the Master prompts tab with
   the library and Copy.
2. `dna.mjs`, `SiteDNA`, `style_guide`, mode `study`, the composer, the rest of the routes and the From a site panel.
3. The other 45 prompts, a group at a time, each with its tests; fill the rules table as prompts land.
4. Real runs, the proof records, `dna_match` in the interface, documents and notices, `check.py --all`.

## 9. Changes made while planning (2026-10-28)

Found by reading the code before writing the plan. None changes the scope.

1. `clone/prompts.py` already exists (the kit agent's prompt), so the library is `clone/library.py` and the composer `clone/compose.py`.
2. Prompt titles and summaries carry their Italian in the file (`title_it`, `summary_it`) instead of going through `tr()`.
3. `picture.py` fetches every pack when none is installed and copies every installed skill into each picture project. A `purpose` on the
   pack keeps "From a picture" exactly as it is.
4. The workflow validator requires an agent and a study has none; it is relaxed for a workflow that has checks.
5. `dna.mjs` only collects and `dna.py` interprets. The signature table is one JSON file, applied by the script and tested through Node.
6. The new routes are in `api/prompt_routes.py`, installed before `clone_routes`.
7. `.claude/skills/<name>` may be the person's own skill: it is never overwritten.
8. The plan has no commit steps: AGENTS.md says to commit only when asked.
9. `POST /api/clone/study` sits in `clone_routes.py` beside `/api/clone/picture`, to reuse the engine checks of `clone_start`; the other new routes are in `prompt_routes.py`.
