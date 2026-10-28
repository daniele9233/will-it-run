# Master prompts and Site DNA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In Clone a site, give the owner a library of 50 original master prompts and a study mode that measures a reference site's DNA and composes the right prompts, filled with measured facts.

**Architecture:** Prompts are Markdown files validated at load (`clone/library.py`). A Node script only collects facts from a live page (`clone/dna.mjs`) and Python interprets them (`clone/dna.py`). A deterministic composer (`clone/compose.py`) turns a DNA into one prompt with the reason for every choice. A study is a model-less workflow run, and the interface gets a "Master prompts" tab.

**Tech Stack:** Python 3.11 (FastAPI, Pydantic, PyYAML, pytest), Node 22+ with the app's Playwright engine, React + TypeScript (vitest), Markdown prompt files.

**Spec:** `docs/superpowers/specs/2026-10-28-clone-master-prompts-design.md` (amended; its section 9 lists what changed while planning).

## Global Constraints

- Free, local, automatic: no paid service, no account, no key; no model is used by the app's own part.
- No commit, push or branch change unless the owner asks (AGENTS.md). A task ends with its checks passing, not with a commit.
- Commands, from the repository root unless noted: tests `cd backend; .venv\Scripts\python.exe -m pytest <file> -q`; lint `cd backend; .venv\Scripts\ruff.exe check src tests ../scripts`; contracts `cd backend; .venv\Scripts\lint-imports.exe`; UI `cd desktop; npm run typecheck; npm run lint; npm test`; text `python -X utf8 scripts/build_locale.py`; checks `python -X utf8 scripts/check.py` (`--all` in Task 24).
- Python: type hints everywhere, Pydantic at boundaries, ruff clean (line length 110), `pathlib`, text read with `utf-8-sig`.
- Tests never touch the network, a GPU, LM Studio or the owner's data. A browser test is opt-in (`WIR_LIVE_BROWSER`, as `tests/test_clone_replica.py:555`).
- Prompt bodies are English. Prompt titles and summaries carry their Italian in the file (`title_it`, `summary_it`). Owner-facing text built in code goes through `tr("English")` with the pair in `backend/src/agentfit/locales/it.py`. Interface strings are `t("English")` with a line in `desktop/src/locales/it.txt`. Never edit `desktop/src/locales/it.ts` or `docs/map/`.
- Every unknown is `None`/`null`, never a convenient value. A choice made for the person returns its reasons.
- Limits: a recipe at most 6,000 characters, `shared.md` at most 3,000, a composed prompt at most 12,000 estimated tokens (characters / 4).
- Pinned commits (observed 2026-10-28): taste-skill `12af471f627f866411b4bd785b19646d3672784c (tag v2.1.5)`, MotionSites `faa30d512e3973e58675c72101a7ef42ed37ae83`, impeccable `3e3a9179e01a60265000ce239c0a99803ec1e60d`.
- Third-party skill scripts are never copied. Nothing is copied or paraphrased from sceneai.art or from MotionSites' `examples/` and `media/`.
- Routes are written `@app.get("/api/...")`. `/api/clone/prompts*` is registered before `/api/clone/{study_id}`.
- `clone` modules import only `core` and `workflows` (`backend/.importlinter`). `clone/prompts.py` already exists (the kit agent's prompt): the library is `clone/library.py`.
- Say what was not run. Where a task says "real run", use the isolated home of the memory note `isolated-live-test-recipe` (scratch `AGENTFIT_HOME`, core on 8766, UI entry `willitrun-ui-token`).

## Review Focus

1. `.claude/skills/<name>` already holds the person's own skill of that name: it must survive (Task 1, reported by Task 12).
2. A site that answers 403, a captcha or never finishes during a study: the run ends with what the site said, not an empty DNA (Tasks 7, 8).
3. A page that is a canvas, an iframe, or has no `main` and no sections: `sections` stays empty with a limit, and the composer says nothing matched (Tasks 6, 11).
4. A prompt file saved on Windows with a BOM or CRLF line endings loads and hashes the same (Task 2).
5. A composition over budget even with every optional recipe dropped, and saving the same prompt twice (Tasks 11, 12).

## File structure

| File | Responsibility |
|---|---|
| `backend/src/agentfit/clone/skills.py` (modify) | Design packs, `exclude`, `purpose`, `install_into` |
| `backend/src/agentfit/clone/library.py` (new) | Load, validate and assemble the prompt files |
| `backend/src/agentfit/clone/prompt_library/` (new) | `shared.md`, `proofs.json`, 50 prompt files |
| `backend/src/agentfit/clone/dna_signatures.json` (new) | Library signatures, plain regexes |
| `backend/src/agentfit/clone/dna.mjs`, `dna_lib.mjs` (new) | Collect raw facts from a live page; pure helpers |
| `backend/src/agentfit/clone/dna.py` (new) | `SiteDNA`, `analyze`, `style_guide`, `dna_match`, the study check and workflow |
| `backend/src/agentfit/clone/compose.py` (new) | Rules, `compose`, `save` |
| `backend/src/agentfit/clone/studio.py` (modify) | `start_study`, `dna`, study branches |
| `backend/src/agentfit/workflows/validator.py` (modify) | A workflow with checks needs no agent |
| `backend/src/agentfit/api/prompt_routes.py` (new), `server.py`, `clone_routes.py` (modify) | Routes |
| `desktop/src/lib/masterPrompts.ts`, `dna.ts` (new), `cloneSite.ts` (modify) | Types and logic with tests |
| `desktop/src/pages/clone/MasterPrompts.tsx`, `DnaCard.tsx` (new), `CloneSite.tsx` (modify) | Tab, panels |

## Raw probe schema (the contract between `dna.mjs` and `dna.py`)

`dna-raw.json`, snake_case, any field `null` when it could not be measured (and a `limits` entry says why):

```json
{"raw_version": 1, "url": "", "final_url": "", "captured_at": "ISO-8601", "seconds": 0.0, "viewport": [1440, 900],
 "page_height": 0, "title": "", "lang": "", "framework": "next|nuxt|astro|webflow|framer|wordpress|shopify|vite|null",
 "html_class": "first 200 characters", "globals": ["names present on window from a fixed list"],
 "scripts": {"urls": ["loaded JS, at most 80"], "scanned": 0, "failed": 0, "inline": 0},
 "signature_hits": {"gsap": ["labels of the scripts that matched"]},
 "styles": {"backgrounds": {"#0a0a0a": 0}, "text_colors": {"#ffffff": 0},
            "fonts": {"Inter": {"chars": 0, "weights": {"400": 0}}}, "sizes": {"16": 0}, "spacings": {"16": 0},
            "radii": {"8": 0}, "shadows": {"css value": 0},
            "transitions": [{"duration_ms": 200, "easing": "ease", "count": 0}], "keyframes": ["name"],
            "container_px": 0, "breakpoints": [768],
            "counts": {"sticky": 0, "fixed": 0, "backdrop": 0, "mask": 0, "clip_path": 0, "blend": 0, "blur": 0,
                       "scroll_snap": 0, "scroll_timeline": 0, "view_transition_rules": 0}},
 "dom": {"sections": [{"index": 1, "tag": "header", "height": 0, "heading_px": null, "heading_vw": null, "img": 0,
                       "video": 0, "canvas": 0, "svg": 0, "repeat": 0, "first_video_autoplay": false}],
         "videos": [{"loop": true, "muted": true, "autoplay": false, "preload": "none", "playing_without_autoplay": true}],
         "canvases": [{"context": "webgl|webgl2|2d|unknown", "width": 0, "height": 0, "covers_viewport": true, "in_card": false}],
         "lottie": 0, "iframes": 0,
         "patterns": {"chip_filters": false, "dialog": false, "carousel": false, "masonry": false, "sticky_header": false, "newsletter_form": false},
         "smooth_scroll": {"lenis_class": false, "css_smooth": false}},
 "probes": {"scroll": {"sampled": 0, "parallax_like": 0, "pinned": 0},
            "hover": {"sampled": 0, "changed": 0, "plays_video": 0, "props": ["transform"]}},
 "limits": [{"code": "script_unreadable|no_sections|scroll_probe_skipped|hover_probe_skipped|stylesheet_unreadable|visit_slow", "count": 0}]}
```

Colours are quantized to 5 bits per channel and written as the centre hex of the bucket. `backgrounds` is visible area in px², `text_colors` and `fonts` count characters.

---

## Phase 1: skills, library core, pilot prompts, the tab

### Task 1: Design skill packs

**Files:**
- Modify: `backend/src/agentfit/clone/skills.py` (`SkillPack` line 51, `PACKS` 71, `ensure` 174, `install` 215, `copy_into` 249, `status` 161)
- Modify: `backend/tests/test_clone_skills.py` (the pack-id test at line 46; `git_that_serves` at line 21)
- Modify: `desktop/src/lib/cloneSite.ts` (`SkillPackInfo` line 42, `SkillsStatus` 52), `desktop/src/pages/clone/PictureStart.tsx` (`SkillsCard` line 32)
- Modify: `THIRD_PARTY_NOTICES.md` (list under "Agent skills (not bundled)")

**Interfaces:**
- Produces:
```python
@dataclass(frozen=True)
class SkillPack:           # existing fields, then:
    purpose: Literal["picture", "design"] = "picture"
    exclude: tuple[str, ...] = ()     # entries directly inside a skill's folder that ensure() does not copy
MARKER = ".willitrun-skill"
class SkillStore:
    def catalog(self, purpose: str | None = None) -> list[Skill]
    def install(self, ids: list[str] | None = None, emit=None) -> list[str]   # default: the picture packs only
    def copy_into(self, workspace: Path, names: list[str] | None = None) -> list[Skill]
        # unchanged contract; names=None means the installed picture packs; a name matches a skill's header name or its folder name
    def install_into(self, project: Path, names: list[str], folders: tuple[str, ...] = (SKILLS_FOLDER, ".claude/skills")) -> dict[str, list[str]]
        # {"copied": [...], "skipped": [...], "missing": [...]} (skill header names, names asked for but not on this PC)
    # status(): each pack gains "purpose"; the payload gains "design_ready"; "ready" covers the picture packs only
```
- New packs (ids, repo root folder, skills by folder name, exclude): `taste` (`Leonxlnx/taste-skill`, root `skills`, `("taste-skill",)`, `("scripts", "assets")`, MIT); `motionsites` (`Motionsites-AI/claude-skill`, root `plugins/motionsites-web-builder/skills`, `("motionsites-web-builder",)`, `("examples", "media", "scripts")`, license "MIT (examples and media excluded)"); `impeccable` (`pbakaus/impeccable`, root `.claude/skills`, `("impeccable",)`, `("scripts",)`, Apache-2.0). All `purpose="design"`, commits from Global Constraints.

- [ ] **Step 1: Write the failing tests** in `test_clone_skills.py` (use `git_that_serves`, extended to also write `scripts/x.mjs`, `assets/a.css`, `examples/e.md`, `media/m.jpg`, `references/assets/keep.md` inside each skill folder):
```python
def test_the_design_packs_are_pinned_and_the_picture_packs_are_unchanged():
    # picture ids == ["gsap","frontend-design","vercel","threejs","playwright"]; design ids == ["taste","motionsites","impeccable"]
    # the three commits equal the Global Constraints; exclude tuples and roots as in Interfaces
def test_an_excluded_entry_is_not_copied_but_a_nested_one_of_the_same_name_is(tmp_path): ...
    # after ensure(taste): no scripts/ or assets/ at the skill's top; references/assets/keep.md exists
def test_install_and_copy_into_default_to_the_picture_packs(tmp_path): ...
    # install() fetches 5 packs, not 8; with a design pack installed, copy_into(ws) copies none of its skills
def test_install_into_copies_to_both_folders_under_the_header_name_and_marks_them(tmp_path): ...
    # <project>/.skills/<name>/SKILL.md and <project>/.claude/skills/<name>/MARKER exist; result["copied"] == [name]
def test_a_claude_skills_folder_the_person_made_is_left_alone_and_reported(tmp_path): ...
    # pre-create <project>/.claude/skills/<name>/SKILL.md without MARKER: content unchanged, result["skipped"] == [name]
def test_a_skill_that_is_not_installed_is_reported_missing(tmp_path): ...
```
- [ ] **Step 2: Run** `cd backend; .venv\Scripts\python.exe -m pytest tests/test_clone_skills.py -q`. Expected: the new tests FAIL (unknown fields, `install_into` missing).
- [ ] **Step 3: Implement** in `skills.py`: the fields, the three packs, `catalog(purpose)`, `status()`, `install()` default, `ensure()` passing `ignore=` to `shutil.copytree` that drops names in `pack.exclude` only when the directory being listed is the skill's own folder, `copy_into` using `catalog("picture")` when `names is None` and matching `skill.name` or `skill.folder.name`, and `install_into` (marker rule: a destination under a folder other than `SKILLS_FOLDER` that exists without `MARKER` is skipped; otherwise replaced and marked). Update the pack-id test to filter by `purpose`.
- [ ] **Step 4: Run** the file again plus `tests/test_clone_picture.py` (guards the picture path). Expected: PASS.
- [ ] **Step 5: UI plumbing.** `SkillPackInfo.purpose: "picture" | "design"`, `SkillsStatus.design_ready: boolean`; `SkillsCard` lists and counts only `purpose === "picture"` packs. Run `cd desktop; npm run typecheck; npm test`.
- [ ] **Step 6: Notices.** Add the three packs to `THIRD_PARTY_NOTICES.md` in the existing format (commit short hash, licence, author; for MotionSites say that `examples/` and `media/` are © MotionSites and are not fetched).
- [ ] **Step 7: Real fetch (a script, not a test).** In a scratch folder run `SkillStore(Path(scratch)).install(["taste","motionsites","impeccable"])` with the real Git; print each skill's header name, the entries at its top, and its size. Expected: three packs ready; no `scripts`, `assets`, `examples`, `media` at any skill's top. Note the header names: they are the folder names `install_into` creates under `.claude/skills/`. Prompts cite skills by folder name, which is what `PACKS` holds.
- [ ] **Step 8: Checkpoint:** `python -X utf8 scripts/check.py` passes. No commit.

### Task 2: The library loader and validator

**Files:**
- Create: `backend/src/agentfit/clone/library.py`, `backend/src/agentfit/clone/prompt_library/shared.md`, `backend/src/agentfit/clone/prompt_library/proofs.json` (`{}`)
- Test: `backend/tests/test_clone_library.py`

**Interfaces:**
- Consumes: `skills.PACKS` (known skill folder names).
- Produces:
```python
LIBRARY_VERSION = "1"
GROUPS = {"A": "pages", "B": "gallery", "C": "motion", "D": "surfaces", "E": "three-d", "F": "process", "G": "after-copy"}
PLACEHOLDERS = ("host", "palette", "fonts", "type_scale", "spacing", "motion", "sections", "libs", "viewport")
RECIPE_MAX_CHARS, SHARED_MAX_CHARS = 6000, 3000
class LibraryError(ValueError): ...            # message = every problem found, one per line
@dataclass(frozen=True)
class Prompt:
    id: str; slug: str; group: str; title: str; title_it: str; summary: str; summary_it: str
    tags: tuple[str, ...]; size: Literal["S", "M", "L"]; libs: dict[str, str]; skills: tuple[str, ...]
    blocks: dict[str, str]                      # heading -> text, in file order
    def view(self, lang: str = "en") -> dict[str, Any]   # id, group, title, summary (in lang), tags, size, skills, libs
@dataclass(frozen=True)
class Library:
    prompts: tuple[Prompt, ...]; shared: dict[str, str]; proofs: dict[str, dict[str, Any]]
    def get(self, prompt_id: str) -> Prompt | None
def parse_prompt(text: str, filename: str) -> Prompt
def load(root: Path | None = None) -> Library   # default root: the prompt_library folder next to this module
def get_library() -> Library                     # load() cached with functools.lru_cache(maxsize=1)
def content_hash(root: Path | None = None) -> str   # sha256 over shared.md and the prompt files by name; BOM and CRLF removed; proofs.json excluded
```
Rules `parse_prompt`/`load` enforce (all problems collected, then one `LibraryError`): file name `<id>-<slug>.md`; id `^[A-G][0-9]{2}$` with the letter matching `group`; ids unique; front matter keys `id slug group title title_it summary summary_it size` required, `tags libs skills` optional; blocks only from `Goal, Stack, States, Steps, Motion, Hard lines, Verify` (level-2 headings); required `Goal, Stack, States, Verify` (group `process`: `Goal, Steps, Verify`); `Motion` also required for groups `motion`, `surfaces`, `three-d` and ids `A01`-`A04`; placeholders `{{name}}` only from `PLACEHOLDERS`; body at most `RECIPE_MAX_CHARS`; every skill in `skills` is a folder name in `PACKS`; every `libs` value is non-empty and holds no `<` or `>`; `shared.md` has exactly the headings `Gates, Assets, Hard lines, Verify, Deliver` and at most `SHARED_MAX_CHARS`; every key of `proofs.json` is a prompt id with keys `builder date build_ok widths dna_match notes`.

- [ ] **Step 1: Write the failing tests** (prompt files built in `tmp_path` from a helper `write(root, id, slug, group, **overrides)`):
```python
def test_a_valid_prompt_loads_with_its_blocks_in_order(tmp_path): ...       # list(p.blocks) == ["Goal","Stack","States","Motion","Verify"]
def test_every_problem_is_reported_not_only_the_first(tmp_path): ...        # three faulty files -> three lines in str(exc)
def test_the_id_must_match_the_file_name_and_the_group_letter(tmp_path): ...
def test_required_blocks_depend_on_the_group(tmp_path): ...                 # process needs Steps and no States; C01 needs Motion
def test_an_unknown_placeholder_or_an_oversized_recipe_is_refused(tmp_path): ...
def test_a_skill_no_pack_has_and_a_template_version_are_refused(tmp_path): ...   # skills: [nope]; libs: {lenis: "<version>"}
def test_files_with_a_bom_and_windows_line_endings_load_and_hash_the_same(tmp_path): ...   # Review Focus 4
def test_the_hash_ignores_proofs_and_changes_when_a_prompt_changes(tmp_path): ...
def test_view_gives_the_italian_text_when_asked(tmp_path): ...              # view("it")["title"] == title_it
def test_the_shipped_library_loads(): ...                                    # load() with the real folder: no error (zero prompts allowed until Task 4)
```
- [ ] **Step 2: Run** `pytest tests/test_clone_library.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `library.py` per Interfaces: strip a BOM and normalise `\r\n` before parsing; front matter by `^---\n(.*?)\n---\n(.*)$` with `yaml.safe_load`; blocks split on lines starting `## `. Write `shared.md` from the spec's 4.1 paragraph: **Gates** (1 style guide and spec written from the DNA, stop for approval or, for a builder that cannot pause, write and go on; 2 stills of every section, looked at; 3 full build and checks), **Assets**, **Hard lines**, **Verify** (390, 768, 1440 px; build; no console error; no horizontal overflow; say what was not checked), **Deliver**. At most 3,000 characters.
- [ ] **Step 4: Run** the file. Expected: PASS. Then `lint-imports` and ruff.
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 3: Standalone assembly and the read routes

**Files:**
- Modify: `backend/src/agentfit/clone/library.py`
- Create: `backend/src/agentfit/api/prompt_routes.py`
- Modify: `backend/src/agentfit/api/server.py` (add the import at line 27 and `prompt_routes.install_routes(app, svc, guard)` immediately before `clone_routes.install_routes` at line 1208)
- Test: `backend/tests/test_clone_library_api.py`

**Interfaces:**
- Produces:
```python
UNKNOWN = "Unknown: decide it and say so"
def fill(text: str, values: dict[str, str] | None) -> str            # {{name}} -> values.get(name) or UNKNOWN
def render_recipe(prompt: Prompt, values: dict[str, str] | None = None) -> str   # "### {id} {title}" then each block as "#### {heading}"
def render_shared(shared: dict[str, str], values: dict[str, str] | None = None) -> str   # "## Shared rules", Gates first
def assemble(prompt: Prompt, shared: dict[str, str], values: dict[str, str] | None = None) -> str   # opening line + recipe + shared
```
  Routes: `GET /api/clone/prompts` -> `{"version": LIBRARY_VERSION, "prompts": [prompt.view(lang) + {"proof": proofs.get(id)}]}`; `GET /api/clone/prompts/{prompt_id}` -> the view plus `"text": assemble(...)`; 404 with `detail` for an unknown id. The language is `agentfit.i18n.current_language()`.

- [ ] **Step 1: Write the failing tests:** `test_the_standalone_prompt_holds_the_recipe_then_the_shared_rules_with_every_placeholder_unknown`; `test_a_value_fills_its_placeholder`; route tests with `TestClient(create_app(AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus())))` as in `tests/test_arena_api.py:30`, with `library.get_library` monkeypatched to a `tmp_path` library: `test_the_list_route_is_not_taken_by_the_study_route` (status 200, not 404), `test_the_list_is_in_italian_when_the_ui_asks` (header `x-willitrun-lang: it`), `test_an_unknown_prompt_is_a_404`.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement** `assemble` and its helpers, `prompt_routes.install_routes(app, svc, guard)` (the same signature as `clone_routes.install_routes`, routes with `dependencies=guard`), and the `server.py` wiring.
- [ ] **Step 4: Run** the file and `tests/test_api.py`. Expected: PASS.
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 4: Five pilot prompts and the hash pin

**Files:**
- Create: `prompt_library/B01-gallery-chip-filters.md`, `B02-card-hover-video.md`, `C01-smooth-scroll-lenis-gsap.md`, `F01-study-reference-grammar.md`, `F03-look-and-fix-loop.md`
- Modify: `backend/tests/test_clone_library.py` (pin)

Follow the **Authoring procedure** below. Grounding for each:
- **B01**: chips of footer.design (Typography, Small Type, Illustrative, Grid, Flat, Animated, See all) and inspora.design (All, Web, Branding, Product, Motion, Illustration, 3D); states idle, active, empty result, loading; keyboard and `aria-pressed`.
- **B02**: recent.design (20 videos: loop, muted, `preload=metadata`, started by script) and inspora.design (the first autoplays, the rest `preload=none`); play on hover or in view, pause off-screen, poster, reduced-motion shows the poster; MotionSites' `references/techniques/video-and-media.md` for numbers.
- **C01**: sceneai.art (`lenis` class on `<html>`); the integration of Lenis with GSAP ScrollTrigger from the version-pinned docs; MotionSites' `references/techniques/scroll.md`; reduced-motion turns Lenis off. Cites skills `gsap-scrolltrigger`, `gsap-react`.
- **F01** (process): read the DNA block, write `docs/style_guide.md` and `docs/spec.md`, gate; the grammar, never the content.
- **F03** (process): screenshots at 390, 768, 1440 px, the checklist of `shared.md` Verify, fix the worst three, repeat; compare with `docs/research/<host>/capture` when a clone exists.

- [ ] **Step 1: Look up** each library version (`npm view lenis version`, `npm view gsap version`); gsap is `3.13.0` in `picture.py`. Fetch the three design packs into a scratch folder (Task 1, Step 7) and read the MIT references named above.
- [ ] **Step 2: Write** the five files. Run `cd backend; .venv\Scripts\python.exe -c "from agentfit.clone import library; library.load()"`: no error.
- [ ] **Step 3: Write the failing tests:** `test_the_shipped_library_has_the_pinned_hash_and_the_expected_ids` (`{p.id for p in load().prompts} == {"B01","B02","C01","F01","F03"}` and `content_hash() == PINNED`; compute `PINNED` with `cd backend; .venv\Scripts\python.exe -c "from agentfit.clone import library; print(library.content_hash())"` and paste it); `test_the_code_blocks_of_the_library_parse`: collect fenced blocks tagged `ts`, `tsx`, `js`, `jsx`; run `node` with `desktop/node_modules/typescript` (`ts.transpileModule` with `reportDiagnostics`); skip when `node` or that folder is missing; fail on any syntactic diagnostic.
- [ ] **Step 4: Run** the file. Expected: PASS (the first run before pasting the hash FAILs by design).
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 5: The Master prompts tab (library)

**Files:**
- Create: `desktop/src/lib/masterPrompts.ts`, `desktop/src/lib/masterPrompts.test.ts`, `desktop/src/pages/clone/MasterPrompts.tsx`
- Modify: `desktop/src/pages/CloneSite.tsx` (tabs in `CloneSite()` after the header, shown only when `routeStudy` is null), `desktop/src/locales/it.txt`

**Interfaces:**
- Produces:
```ts
export type PromptGroup = "pages" | "gallery" | "motion" | "surfaces" | "three-d" | "process" | "after-copy";
export interface PromptProof { builder: string; date: string; build_ok: boolean; widths: number[]; dna_match: number | null; notes: string }
export interface PromptSummary { id: string; group: PromptGroup; title: string; summary: string; tags: string[]; size: "S" | "M" | "L"; skills: string[]; libs: Record<string, string>; proof: PromptProof | null }
export interface PromptDetail extends PromptSummary { text: string }
export const GROUPS: { id: PromptGroup; label: () => string }[]
export function filterPrompts(list: PromptSummary[], group: PromptGroup | "all", query: string): PromptSummary[]  // case-insensitive over id, title, summary, tags
export function proofLabel(proof: PromptProof | null): string   // t("Not tested yet") | t("Tested with {builder} on {date}")
export function copyText(text: string): Promise<boolean>        // navigator.clipboard.writeText; false on failure
```
Uses `api.get<{ version: string; prompts: PromptSummary[] }>("/api/clone/prompts")` and `api.get<PromptDetail>(\`/api/clone/prompts/${id}\`)`; `Tabs` from `components/ui.tsx` (`Clone` | `Master prompts`), the choice remembered with the page's `remember` helper under `agentfit.clone.tab`.

- [ ] **Step 1: Write the failing vitest tests:** `filters by group and by words in the title, summary or tags`; `a group "all" keeps everything`; `proofLabel says Not tested yet without a proof`; `proofLabel names the builder and the date`; `copyText returns false when the clipboard refuses`.
- [ ] **Step 2: Run** `cd desktop; npm test -- masterPrompts`. Expected: FAIL.
- [ ] **Step 3: Implement** the lib and the page: group chips, search box, cards (title, summary, size, `proofLabel`), a drawer that loads the detail and shows `text` with a Copy button (toast on success or failure). Add every `t("...")` with its pair to `it.txt`; run `python -X utf8 scripts/build_locale.py`.
- [ ] **Step 4: Run** `npm run typecheck; npm run lint; npm test`. Expected: PASS.
- [ ] **Step 5: Real run.** Start the isolated core and `willitrun-ui-token`; open Clone a site, the Master prompts tab; screenshot: five cards, the drawer text, Copy pressed (the toast shows). Say that clipboard contents were not read back.

---

## Phase 2: DNA, study, composer

### Task 6: Site DNA model, analysis, style guide, match

**Files:**
- Create: `backend/src/agentfit/clone/dna.py`, `backend/tests/test_clone_dna.py`, `backend/tests/fixtures/dna/raw_example.json` (a hand-written raw probe in the schema above)

**Interfaces:**
- Produces:
```python
class DetectedLib(BaseModel): name: str; evidence: list[str]
class PaletteColor(BaseModel): hex: str; share: float; role: Literal["background", "text", "accent"] | None = None
class FontUse(BaseModel): family: str; share: float; weights: list[int]
class Tokens(BaseModel): palette: list[PaletteColor] | None = None; fonts: list[FontUse] | None = None; type_scale: list[float] | None = None; spacing: list[float] | None = None; radii: list[float] | None = None; shadows: list[str] | None = None; container_px: int | None = None; breakpoints: list[int] | None = None
class Section(BaseModel): index: int; tag: str; role: Literal["header", "hero", "gallery", "content", "footer"] | None = None; height_px: int; heading_px: float | None = None; heading_vw: float | None = None; img: int = 0; video: int = 0; canvas: int = 0; svg: int = 0
class Videos(BaseModel): count: int; loop: int; muted: int; autoplay: int; preload_none: int; playing_without_autoplay: int
class Canvas(BaseModel): context: Literal["webgl", "webgl2", "2d", "unknown"]; width: int; height: int; covers_viewport: bool; in_card: bool | None = None
class Transition(BaseModel): duration_ms: int; easing: str; count: int
class Motion(BaseModel): transitions: list[Transition] | None = None; keyframes: list[str] | None = None; sticky: int | None = None; fixed: int | None = None; scroll_snap: bool | None = None; scroll_timeline: bool | None = None; view_transitions: bool | None = None; smooth_scroll: Literal["lenis", "native", "none"] | None = None; backdrop: int | None = None; blur: int | None = None; mask: int | None = None; clip_path: int | None = None; blend: int | None = None; videos: Videos | None = None; canvases: list[Canvas] | None = None; lottie: int | None = None
class Patterns(BaseModel): chip_filters: bool | None = None; dialog: bool | None = None; carousel: bool | None = None; masonry: bool | None = None; sticky_header: bool | None = None; newsletter_form: bool | None = None
class ScrollProbe(BaseModel): sampled: int; parallax_like: int; pinned: int
class HoverProbe(BaseModel): sampled: int; changed: int; plays_video: int; props: list[str]
class Probes(BaseModel): scroll: ScrollProbe | None = None; hover: HoverProbe | None = None
class Limit(BaseModel): code: str; count: int | None = None
class SiteDNA(BaseModel):
    schema_version: Literal[1] = 1; url: str; host: str; captured_at: str; viewport: tuple[int, int]
    framework: str | None = None; libs: list[DetectedLib]; tokens: Tokens; sections: list[Section] | None = None
    motion: Motion; patterns: Patterns; probes: Probes; limits: list[Limit]
def analyze(raw: dict[str, Any]) -> SiteDNA
def style_guide(dna: SiteDNA) -> str          # Markdown: Palette, Type, Spacing, Layout, Motion, Libraries, Patterns, Limits; None -> "Unknown"
def dna_match(a: SiteDNA, b: SiteDNA) -> dict[str, Any]   # {"overall": float | None, "parts": {...: float | None}, "not_covered": [str]}
def limit_text(code: str, count: int | None) -> str       # English words for each limit code; style_guide() uses it
GLOBAL_LIBS: dict[str, str]                               # window name -> library id, read from dna_signatures.json["globals"]
```
Decisions for `analyze`: palette roles by rule (background = largest `backgrounds` area; text = largest `text_colors` count that is not the background; accent = the most saturated colour, Lab chroma at least 25, with at least 0.5% share); `shares` are fractions of the total; `type_scale` = distinct sizes holding at least 0.5% of characters, ascending, at most 8; `spacing` = the 8 most common values, ascending; fonts: the 4 most used, each with its 3 most used weights; `libs` from `signature_hits` (id -> name, evidence = labels) plus `globals`; `smooth_scroll` = `"lenis"` if `lenis_class` or a `lenis` hit, else `"native"` if `css_smooth`, else `"none"`; `view_transitions` = a `view-transitions` hit; section `role` by rule (tag `header` -> header, `footer` -> footer, the first section with `heading_px >= 32` or a video or canvas -> hero, `repeat >= 6` -> gallery, else content); no sections -> `sections = []` and a `no_sections` limit.
Decisions for `dna_match`: palette = greedy one-to-one match of the top five colours by Lab distance, score `1 - min(1, mean_distance / 40)`; fonts and libs = Jaccard of lower-cased names; motion = share of the features present in `a` (`view_transitions`, `scroll_snap`, `smooth_scroll != none`, `sticky > 0`, `backdrop > 0`, `videos`, `canvases`, `lottie > 0`) that `b` also has; sections = `min(n_a, n_b) / max(n_a, n_b)`; `overall` = mean of the parts that are not `None`; `not_covered` = layout, text and the feel of the motion.

- [ ] **Step 1: Write the failing tests:** `test_analyze_gives_the_palette_roles_by_rule`; `test_an_unmeasured_field_stays_none_and_is_not_defaulted` (raw with `"fonts": null` -> `tokens.fonts is None`); `test_a_page_with_no_sections_leaves_them_empty_and_says_so` (Review Focus 3); `test_a_signature_hit_becomes_a_detected_library_with_its_evidence`; `test_the_style_guide_says_unknown_for_what_was_not_measured`; `test_dna_match_is_one_for_the_same_dna_and_lower_for_a_different_palette`; `test_dna_match_says_what_it_does_not_cover`; `test_limit_text_exists_for_every_limit_code`.
- [ ] **Step 2: Run** `pytest tests/test_clone_dna.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `dna.py` (models, `analyze`, `style_guide`, `dna_match`, `limit_text`). The style guide and `limit_text` are English because models read them; the interface translates the limit codes itself (Task 13). `analyze` adds a library for every name of `GLOBAL_LIBS` found in `raw["globals"]`, besides the `signature_hits`.
- [ ] **Step 4: Run** the file and ruff. Expected: PASS.
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 7: The collector `dna.mjs`

**Files:**
- Create: `backend/src/agentfit/clone/dna.mjs`, `backend/src/agentfit/clone/dna_lib.mjs`, `backend/src/agentfit/clone/dna_signatures.json`
- Test: `backend/tests/test_clone_dna_script.py`, `backend/tests/fixtures/dna/page/` (a static fixture page and `vendor.js`)

**Interfaces:**
- Produces: `node dna.mjs <config.json>`, config `{"url", "out", "executable", "locale"?: string}`, env `WIR_BROWSER_DIR`; writes `<out>/dna-raw.json` (schema above) and `<out>/thumb.jpg` (first screen, JPEG); stdout JSON lines `{"event":"progress","step","detail"}`, `{"event":"done","result":{...}}`, `{"event":"fatal","error"}` as `capture.mjs` does (header lines 1-11). Steps named `visit`, `scroll`, `styles`, `scripts`, `probes`, `write`.
- `dna_lib.mjs` exports the pure helpers `dna.mjs` uses: `matchSignatures(text: string, signatures: Signature[]): string[]` (ids), `quantizeHex(r, g, b): string`, `parseDurationsMs(text: string): number[]`. `dna_signatures.json` is `{"signatures": [{"id": "gsap", "any": ["regex", ...], "all": ["regex", ...]}], "globals": {"jQuery": "jquery", ...}}`: the regexes are plain (no lookbehind, no named groups, no inline flags), and `globals` maps the names the script reads from `window` to library ids (gsap, lenis, three, pixi, p5, paper, matter, tsParticles, lottie, Swiper, LocomotiveScroll, jQuery), shared with `dna.py`. Signature ids at least: `gsap lenis locomotive three ogl pixi p5 paper matter rapier tsparticles lottie swiper framer-motion view-transitions glsl webgpu`. A signature is specific: a page global, a package path, a distinctive call (`new Lenis(`, `ScrollTrigger.create(`), or identifiers together (`WebGLRenderer` and `BufferGeometry` for `three`); never a bare word.
- Behaviour to pin: visit as the visitor's Chrome (the user-agent fix and `serviceWorkers: "block"` of `capture.mjs`), 1440x900; wait for `load`, network idle (at most 8 s) and 1.5 s; one scroll pass down and back in steps of 0.8 viewport with 200 ms pauses; consent banners hidden with an attribute, never removed; scripts read from the resource-timing list through Playwright's request context (no CORS); a main document answered with status 400 or more, or a navigation that fails or exceeds 60 s, ends with a `fatal` event holding the status text (Review Focus 2). Probes: scroll probe over at most 60 visible elements of 5,000 px² or more, at scroll 0 and 400 after a 800 ms wait (`pinned` = fixed or sticky, or moved less than 8 px; `parallax_like` = moved by a distance that differs from 400 by more than 12%, and not pinned); hover probe over at most six card-like elements (anchor, figure or list item with a picture or video, 20,000 px² or more), 350 ms after the pointer arrives, comparing `transform opacity box-shadow filter background-color border-color` and whether a child video started. Each probe has a 10 s cap; on timeout the probe is `null` with its `*_probe_skipped` limit.

- [ ] **Step 1: Write the failing tests** in `test_clone_dna_script.py`:
```python
def test_the_signature_table_matches_what_it_should_and_nothing_else(): ...
    # runs node on dna_lib.mjs for these cases (skip if node is missing):
    # true: "gsap.registerPlugin(ScrollTrigger)" -> gsap; "const l = new Lenis({" -> lenis; "new THREE.WebGLRenderer BufferGeometry" -> three;
    #       "@dimforge/rapier3d" -> rapier; "startViewTransition(" -> view-transitions; "gl_FragColor" -> glsl
    # false: "browserapierrors.xhr" -> no rapier; "three" in prose -> no three; "the lenis of the eye" -> no lenis; "registerPlugin(" alone -> no gsap
def test_quantize_and_durations(): ...    # quantizeHex(255, 0, 0) == "#f80000"; parseDurationsMs("0.2s, 150ms") == [200, 150]
@pytest.mark.skipif(not os.environ.get("WIR_LIVE_BROWSER") or find_browser() is None, reason="set WIR_LIVE_BROWSER ...")
def test_the_collector_reports_a_local_fixture_page(tmp_path): ...
    # serves tests/fixtures/dna/page with http.server on 127.0.0.1: a muted looping video, a sticky header with backdrop-filter, class "lenis"
    # on <html>, chip filters above eight cards, a WebGL canvas covering the viewport, @keyframes marquee-x, vendor.js holding ScrollTrigger.create(
    # asserts dna-raw.json: videos[0].loop and muted, canvases[0].context startswith "webgl", patterns.chip_filters, patterns.sticky_header,
    # smooth_scroll.lenis_class, "gsap" in signature_hits, "marquee-x" in keyframes, thumb.jpg exists
@pytest.mark.skipif(...)
def test_a_page_that_answers_403_ends_with_a_fatal_event_naming_the_status(tmp_path): ...   # Review Focus 2
```
- [ ] **Step 2: Run** `pytest tests/test_clone_dna_script.py -q`. Expected: the Node-driven tests FAIL (files missing); live tests are skipped without `WIR_LIVE_BROWSER`.
- [ ] **Step 3: Implement** `dna_lib.mjs`, `dna_signatures.json`, `dna.mjs`. The page-side function that gathers styles and DOM facts (named `probePage`) returns only histograms, counts and the small lists of the schema, never text of the page, picture addresses or video addresses.
- [ ] **Step 4: Run** with the engine: in PowerShell `$env:WIR_LIVE_BROWSER = "$env:LOCALAPPDATA\Will It Run\Data\tools\clone-browser"`, then the file. Expected: PASS, none skipped.
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 8: A study is a run (validator, definition, check)

**Files:**
- Modify: `backend/src/agentfit/workflows/validator.py` (line 94)
- Modify: `backend/src/agentfit/clone/dna.py` (add the workflow part)
- Modify: `backend/src/agentfit/clone/studio.py` (import `dna` beside `picture, pipeline` so its `register()` runs)
- Test: `backend/tests/test_clone_dna.py` (extend), the validator's existing test file (find it with `grep -ln "validate(" backend/tests`)

**Interfaces:**
- Consumes: `pipeline._install`, `pipeline._run_script`, `pipeline._step`, `pipeline._fail`, `pipeline.research(workspace, host)`, `host_of`.
- Produces:
```python
WORKFLOW = "clone-site-study"
DNA_SCRIPT = HERE / "dna.mjs"
def dna_dir(workspace: Path, host: str) -> Path                 # pipeline.research(workspace, host) / "dna"
def _script(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]   # the seam tests replace, as picture._script
def _clone_dna(check: CheckDef, ctx: CheckContext) -> dict[str, Any]
    # writes dna.json, style_guide.md, thumb.jpg in dna_dir; returns {"passed": True, "verdict": "STUDIED", "summary": str, "dna": {...}}
    # or _fail(reason, "STUDY_FAILED"); params: url, host, locale
def definition(*, url: str) -> WorkflowDef                       # one check "dna", edges START -> dna -> END, no agent, no model
def register() -> None                                           # CheckSpec(kind="clone_dna", listed=False); called at module level like pipeline.py:475
```
  Validator change: `if not definition.agents and not definition.checks: error("A workflow needs at least one agent or check.")`.

- [ ] **Step 1: Write the failing tests:** `test_a_workflow_with_a_check_and_no_agent_is_valid_and_one_with_neither_is_not` (`validate(...)`); `test_a_study_writes_the_dna_the_style_guide_and_the_thumbnail` (`monkeypatch.setattr(dna, "_script", fake)` that writes `dna-raw.json` from the Task 6 fixture and a one-byte `thumb.jpg`; `pipeline.install_source` and `find_browser` patched as `engine` in `test_clone_picture.py:158`); `test_a_study_that_the_site_refuses_ends_with_what_the_site_answered` (fake returns `(False, "HTTP 403 Forbidden")` -> `verdict == "STUDY_FAILED"` and the summary holds `403`); `test_the_study_check_is_registered_and_not_listed`.
- [ ] **Step 2: Run** the two files. Expected: FAIL.
- [ ] **Step 3: Implement** the validator change and the workflow part of `dna.py`. Real engine question: start a model-less run through `RunManager` with a fake check and confirm the compiler and runner accept zero agents (`compiler.py:1573`, `runner.py:345` only iterate). If something else assumes an agent, fix it minimally and say so; if it needs more than a guard, stop and ask the owner.
- [ ] **Step 4: Run** the files plus `tests/test_workflows*.py` (every workflow test). Expected: PASS.
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 9: Study entries and routes

**Files:**
- Modify: `backend/src/agentfit/clone/studio.py` (`start` line 474, `list` 422, `_shots` 433, add `start_study`, `dna`)
- Modify: `backend/src/agentfit/api/clone_routes.py` (extract `require_engine()` from `clone_start` lines 142-149; add `POST /api/clone/study` beside `/api/clone/picture`)
- Modify: `backend/src/agentfit/api/prompt_routes.py` (add `GET /api/clone/{study_id}/dna`)
- Test: `backend/tests/test_clone_study.py` (the studio factory as `test_clone_picture.py:510`)

**Interfaces:**
- Produces:
```python
class CloneStudio:
    def start_study(self, url: str, *, study_id: str | None = None) -> tuple[dict[str, Any], RunRecord]
        # entry: id, url, title, slug, host, created_at, updated_at, mode "study", workspace (self.home(id)), source {"kind": "none"}
    def dna(self, study_id: str) -> SiteDNA | None       # reads dna.json from the entry's workspace; None before the run finished
```
  Consumes `dna.definition(url=...)`, `dna.dna_dir(workspace, host)` and `SiteDNA` (Tasks 6, 8). `start()` sends a `study` entry's new run to `start_study`, as it does for `picture`. `_shots` for `study` returns `docs/research/<host>/dna/thumb.jpg` when it exists. Routes: `POST /api/clone/study` body `{"url": str}` -> `{"study": id, "run": run_id}` (400 for `CloneError`, the 409s of `require_engine`); `GET /api/clone/{study_id}/dna` -> the DNA JSON, 404 when not ready.

- [ ] **Step 1: Write the failing tests:** `test_a_study_entry_is_listed_with_its_mode_and_thumbnail`; `test_a_study_runs_again_from_its_entry_and_keeps_its_mode`; `test_a_study_entry_can_be_read_and_deleted_like_a_clone` (`get()` must not assume a project or a kit); `test_the_dna_is_none_until_the_run_wrote_it_and_then_valid`; `test_the_study_route_refuses_without_the_engine_like_the_clone_routes` (409); `test_the_dna_route_returns_the_dna`.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement.** In `start_study` build the definition with `dna.definition(url=normalized_url)` and start it like `start_picture` does (`self.runs.start(..., protect_existing=False, source={"kind": "none"}, approval="ask")`, then `db.kv_set("run_definition", ...)` and the entry). Read `get()` and `delete()` and add a `study` branch only where they assume a project.
- [ ] **Step 4: Run** the file plus `tests/test_clone.py tests/test_clone_picture.py tests/test_clone_replica.py`. Expected: PASS.
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 10: The DNA of the four real sites (real run and fixtures)

**Files:**
- Create: `backend/tests/fixtures/dna/recent.design.json`, `footer.design.json`, `inspora.design.json`, `sceneai.art.json`
- Test: `backend/tests/test_clone_dna_sites.py`

- [ ] **Step 1: Real run.** With the isolated core, `POST /api/clone/study` for `https://recent.design/websites`, `https://www.footer.design/`, `https://www.inspora.design/?category=Web`, `https://sceneai.art/`. Follow each run; record the seconds each took (the spec's target is about 15 s: report the real number). Fetch each DNA.
- [ ] **Step 2: Compare** with the hand measurements of the spec's section 5.2. Every fact must be present; a difference is a failure to explain (the site may have changed, or a signature is wrong): fix the signature or the analysis, never the expectation. Note what the DNA measured beyond the table.
- [ ] **Step 3: Save** each DNA as a fixture after checking that it holds numbers and names only (no text of the page, no picture or video addresses, no logos).
- [ ] **Step 4: Write the test:** `test_the_four_reference_dnas_hold_the_facts_measured_by_hand` loads each fixture with `SiteDNA.model_validate` and asserts the section 5.2 facts: recent.design 20 videos, loop and muted, 0 canvases, `view_transitions`, fonts include Inter and Departure Mono, no gsap or lenis; footer.design Webflow framework, jQuery and lottie among libs, 0 videos, `chip_filters`; inspora.design framework next, gsap, 15 videos, `scroll_snap`; sceneai.art framework next, `smooth_scroll == "lenis"`, 10 videos, font Geist; all four 0 canvases. A second test, `test_the_fixtures_hold_no_text_and_no_media_addresses`, walks every string of each fixture and fails on one that ends in `.mp4`, `.webm`, `.png`, `.jpg`, `.jpeg`, `.webp` or `.svg`, or starts with `http` (the `url` field is exempt).
- [ ] **Step 5: Run** the test. Expected: PASS. Checkpoint: `python -X utf8 scripts/check.py`.

### Task 11: The composer and its route

**Files:**
- Create: `backend/src/agentfit/clone/compose.py`, `backend/tests/test_clone_compose.py`
- Modify: `backend/src/agentfit/api/prompt_routes.py`, `backend/src/agentfit/locales/it.py`

**Interfaces:**
- Consumes: `library.get_library`, `library.render_recipe`, `library.render_shared`, `library.UNKNOWN`, `dna.SiteDNA`.
- Produces:
```python
BUDGET_TOKENS, MAX_RECOMMENDED, ALWAYS = 12_000, 6, ("F01", "F03")
@dataclass(frozen=True)
class Rule:
    id: str; prompts: tuple[str, ...]
    when: Callable[[SiteDNA], bool]
    reason: Callable[[SiteDNA], str]          # tr("... {n} ...", n=...) with a literal template
RULES: tuple[Rule, ...]                        # initial: B02, B01, C01 and the rest of the spec's table whose prompts exist; each batch adds its rows
def values_of(dna: SiteDNA) -> dict[str, str]  # host, palette, fonts, type_scale, spacing, motion, sections, libs, viewport; None -> UNKNOWN
def recommend(dna: SiteDNA, library: Library | None = None) -> list[dict[str, str]]   # [{"id", "why"}]: matching rules in RULES order, then ALWAYS with their reason, at most MAX_RECOMMENDED plus ALWAYS
def compose(dna: SiteDNA, picked: list[str] | None = None, *, library: Library | None = None) -> dict[str, Any]
    # {"prompt": str, "used": [ids], "reasons": [{"id","why"}], "dropped": [ids], "tokens": int, "over_budget": bool, "recommended_empty": bool}
```
  Layout of `prompt`: opening (goal; "take the grammar, never the content") + `## Site DNA (measured on <date>)` block from `values_of` + each used recipe via `render_recipe` + `render_shared`. Over `BUDGET_TOKENS` (characters / 4) drop the lowest-priority optional recipes (last in the order) and list them in `dropped`; `ALWAYS` are never dropped; if still over, `over_budget` is true. `picked=None` means the recommendation; otherwise exactly those ids plus `ALWAYS`.
  Route: `POST /api/clone/{study_id}/compose` body `{"picked": list[str] | null}` -> the dict above; 404 when the study has no DNA yet.

- [ ] **Step 1: Write the failing tests** with the four fixtures of Task 10: `test_recent_design_recommends_hover_video_and_chip_filters_with_their_reasons`; `test_sceneai_recommends_the_lenis_recipe_because_lenis_was_found`; `test_a_dna_that_matches_no_rule_recommends_only_the_always_on_prompts_and_says_so` (`recommended_empty is True`, Review Focus 3); `test_the_always_on_prompts_are_in_every_composition`; `test_unknown_values_render_as_unknown_not_as_defaults`; `test_a_composition_over_the_budget_drops_the_lowest_priority_recipes_and_says_which` and `test_over_budget_is_reported_when_nothing_more_can_be_dropped` (Review Focus 5; patch `BUDGET_TOKENS` small); `test_every_rule_names_a_prompt_that_exists_in_the_library`; `test_every_reason_has_an_italian_pair` (`tr` with `set_default_language("it")`); `test_the_four_compositions_fit_the_budget`; `test_the_dna_block_holds_the_measured_values` (recent.design's block holds `Departure Mono`, its palette hex values and its 20 videos); `test_the_compose_route_returns_the_composition`.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement** `compose.py` and the route; add the Italian pairs for each reason template to `locales/it.py`.
- [ ] **Step 4: Run** the file, `tests/test_i18n.py`. Expected: PASS.
- [ ] **Step 5: Real run.** Compose for each of the four fixtures; report estimated tokens and the reasons.
- [ ] **Step 6: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 12: Save into the project

**Files:**
- Modify: `backend/src/agentfit/clone/compose.py` (`save`, `needed_packs`), `backend/src/agentfit/api/prompt_routes.py`
- Test: `backend/tests/test_clone_compose.py` (extend)

**Interfaces:**
- Consumes: `SkillStore.install_into` (Task 1), `CloneStudio.workspace(entry)`.
- Produces:
```python
def save(project: Path, host: str, text: str) -> Path        # <project>/docs/prompts/<host>-master.md; never overwrites: -2, -3, ...; written as UTF-8 with LF line endings
def needed_packs(prompt_ids: list[str], library: Library | None = None) -> list[str]   # pack ids owning the skills the prompts cite
```
  Route: `POST /api/clone/{study_id}/prompts/save` body `{"text": str, "used": list[str], "target": str | null}` (`target` = the id of the clone whose folder receives the files; default: the latest entry with the same host and a project; else the study's own folder) -> `{"file": "docs/prompts/<host>-master.md", "folder": str, "skills": {"copied": [...], "skipped": [...], "missing": [...]}}`. A cited skill whose pack is not installed is `missing`; the route does not fetch (the interface offers "Install the skills" through `POST /api/clone/skills/install` with `packs`).

- [ ] **Step 1: Write the failing tests:** `test_saving_writes_the_prompt_under_docs_prompts`; `test_saving_twice_never_overwrites` (second file `-2`, Review Focus 5); `test_the_route_copies_the_cited_skills_to_both_folders_of_the_chosen_project` (integration from the route to the files: `.skills/<name>` and `.claude/skills/<name>` exist; the prompt file exists); `test_a_skill_the_person_already_has_is_reported_skipped_through_the_route` (Review Focus 1); `test_a_skill_whose_pack_is_not_installed_is_reported_missing`; `test_the_default_target_is_the_latest_clone_of_the_same_host`.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement** `save`, `needed_packs`, the route.
- [ ] **Step 4: Run** the file. Expected: PASS.
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 13: Study mode and the DNA in the interface

**Files:**
- Modify: `desktop/src/lib/cloneSite.ts` (`CloneMode` line 39, `stagesOf` 144, `stageText` 158; add `STUDY_STAGES`), `desktop/src/pages/CloneSite.tsx` (`CloneCard`, `CloneView`)
- Create: `desktop/src/lib/dna.ts`, `desktop/src/lib/dna.test.ts`, `desktop/src/pages/clone/DnaCard.tsx`
- Modify: `desktop/src/locales/it.txt`

**Interfaces:**
- Produces: `CloneMode = "replica" | "agent" | "picture" | "study"`; `STUDY_STAGES: Stage[] = [{ id: "dna", check: true }]`; stage text `dna: { label: t("DNA"), what: t("opens the page like a visitor and measures its colours, fonts, libraries, sections and motion") }`; `lib/dna.ts` exports `DnaView` (the `SiteDNA` JSON) and `paletteSwatches(dna): { hex: string; role: string | null }[]`, `libsLabel(dna): string`, `limitLines(dna): string[]` (the limit codes as `t()` texts), `motionFacts(dna): string[]` (only measured facts; an unmeasured one is absent, never zero).

- [ ] **Step 1: Write the failing vitest tests:** `stagesOf study is the single dna stage`; `paletteSwatches lists roles and skips an unmeasured palette`; `motionFacts leaves out what was not measured`; `limitLines translates each code`.
- [ ] **Step 2: Run** `npm test -- dna cloneSite`. Expected: FAIL.
- [ ] **Step 3: Implement.** `DnaCard` shows swatches, fonts, libraries, motion facts and limits, with "Unknown" for what is absent. `CloneCard` shows a "Study" badge for `mode === "study"`. `CloneView` for a study renders the pipeline (one stage) and, when finished, `DnaCard` (from `GET /api/clone/{id}/dna`) and a "Master prompts from this site" button that opens the tab with this study chosen (state passed through `navigate("clone", { tab: "prompts", study: id })` or the page's equivalent; read `lib/store.ts` for the params convention).
- [ ] **Step 4: Run** `npm run typecheck; npm run lint; npm test; python -X utf8 scripts/build_locale.py`. Expected: PASS.
- [ ] **Step 5: Checkpoint:** `python -X utf8 scripts/check.py`.

### Task 14: The From a site panel

**Files:**
- Modify: `desktop/src/pages/clone/MasterPrompts.tsx`, `desktop/src/lib/masterPrompts.ts`, `desktop/src/lib/masterPrompts.test.ts`, `desktop/src/locales/it.txt`

**Interfaces:**
- Produces in `masterPrompts.ts`:
```ts
export interface Recommendation { id: string; why: string }
export interface Composed { prompt: string; used: string[]; reasons: Recommendation[]; dropped: string[]; tokens: number; over_budget: boolean; recommended_empty: boolean }
export function togglePick(picked: string[] | null, id: string, recommended: string[]): string[]   // null starts from the recommendation; toggling removes or adds
export function tokenLabel(tokens: number): string        // t("~{n} tokens") with the thousands separated
```
  Panel behaviour: address box and Study button (`POST /api/clone/study`), a list of the studied sites from `GET /api/clone` filtered `mode === "study"` (polled while one runs), the chosen site's `DnaCard`, the recommendation as checkboxes with `reasons[].why`, `recommended_empty` shown as "No rule matched this site: only the always-on prompts are included", the composed prompt in a read-only box with `tokenLabel`, `dropped` and `over_budget` said in words, Copy, and Save into the project with a target select (clones of the same host first) calling `POST /api/clone/{id}/prompts/save`; its `skills.skipped` and `skills.missing` shown with an "Install the skills" button posting `{ packs }` to `/api/clone/skills/install`. The panel also says that the site's text, logos and brand belong to its owner (reuse the existing `t()` string if the Clone page has one: `grep -rn "belong to its owner" desktop/src`; else add it with its Italian pair).

- [ ] **Step 1: Write the failing tests:** `togglePick starts from the recommendation`; `togglePick removes a picked id and adds an unpicked one`; `tokenLabel separates thousands`.
- [ ] **Step 2: Run** `npm test -- masterPrompts`. Expected: FAIL.
- [ ] **Step 3: Implement** the panel; all strings through `t()` with their Italian pairs.
- [ ] **Step 4: Run** `npm run typecheck; npm run lint; npm test; python -X utf8 scripts/build_locale.py`. Expected: PASS.
- [ ] **Step 5: Real run.** Isolated core and UI: study `recent.design` from the panel, see the DNA, the reasons, the composed prompt and its token count, press Copy, Save into a scratch clone folder; check the two files exist. Screenshot each state. Say what was not exercised (a failing site, a missing pack).

---

## Authoring procedure (Tasks 4 and 15 to 21)

For every prompt file:

1. **Ground it.** Facts from the four DNA fixtures that bear on the recipe; numbers from the MIT packs fetched in Task 1, Step 7 (taste-skill `library/motions.json`, `directions.json`, `references/motion.md`; MotionSites `references/techniques/*.md`); the documentation of each library at the version you pin (`npm view <package> version`, then its docs). Never open, copy or paraphrase sceneai.art's prompts or MotionSites' `examples/`.
2. **Write the file** with all front matter fields (`title_it`, `summary_it` in natural Italian), blocks in order, numbers explicit (durations in ms, named easings, staggers, distances in px or vw, thresholds), the reduced-motion fallback, States with entry, exit and why, extra Hard lines and Verify that are specific to the recipe, code blocks only where an API call is non-obvious, at most 6,000 characters.
3. **Validate:** `library.load()` passes and the TypeScript parse test passes.
4. **Rules.** If the spec's rules table (4.3) names the prompt, add its `Rule` to `compose.RULES` with a literal `tr()` reason, its Italian pair, and a test against the fixtures or a hand-built DNA.
5. **Pin.** Add the ids to the expected set in `test_the_shipped_library_has_the_pinned_hash_and_the_expected_ids` and replace `PINNED` with the new `content_hash()`. `LIBRARY_VERSION` stays `"1"` until the first release that contains the library; after that any change to a shipped prompt bumps it.
6. **Review.** A reviewer who did not write the batch checks each prompt: numbers present; reduced-motion; states with reasons; no invented facts about a site or library; hard lines present; under the limit; nothing taken from a protected collection. Report the findings, then fix.
7. **Checkpoint:** `pytest tests/test_clone_library.py tests/test_clone_compose.py -q` and `python -X utf8 scripts/check.py`.

## Phase 3: the other 45 prompts

Each task creates the files `prompt_library/<ID>-<slug>.md` for its group (slugs from the titles in the spec's table 4.1, kebab-case) and follows the procedure. Titles are those of the spec.

### Task 15: Batch A, pages and sections (A01-A10)
Rules to add: **A01** when the first section holds an autoplay muted looping video ("the first screen has a looping video"); **A02** when a hero heading is 14 vw or more; **A04** when a sticky header has backdrop blur; **A09** when a footer heading is 12 vw or more. Grounding: taste-skill `directions.json` ("The name as a monument" for A02), `heroes.json` and `sections.json`; the four hero and footer observations of the spec's 3.2.

### Task 16: Batch B, galleries (B03-B07)
Rules: **B03** when `view_transitions` ("the page uses the View Transitions API"); **B05** when a newsletter form exists ("a subscribe form"). Grounding: recent.design dialog and View Transitions, footer.design "Editor's choice", inspora.design subscribe strip.

### Task 17: Batch C, motion and scroll (C02-C12)
Rules: **C04** when the scroll probe found pinned elements; **C06** when three or more elements are parallax-like; **C08** when a keyframes name holds `marquee` or `ticker`; **C12** when `view_transitions`. Grounding: taste-skill `motions.json` (masked headline, shutter image, pinned story, scaling wordmark) and MotionSites `scroll.md`, `type-and-text-motion.md`, `depth-and-interaction.md`.

### Task 18: Batch D, backgrounds and surfaces (D01-D07)
Rules: **D05** a WebGL canvas that covers the viewport; **D07** WebGL canvases inside cards or pictures. Check the maintenance of any library before recommending it (`ogl` has had no push since 2025-04-13: prefer plain WebGL2 or three.js in D05 unless the docs at the pinned version justify OGL). Grounding: MotionSites `surfaces-and-buttons.md`, `3d-and-webgl.md`.

### Task 19: Batch E, 3D and interactive (E01-E03)
Rule: **E01** when three.js is detected and the canvas neither covers the viewport nor sits in a card. Grounding: the `threejs` pack already installed for picture builds (cited by name) and MotionSites `3d-and-webgl.md`.

### Task 20: Batch F, process (F02, F04, F05)
No rules (library-only). F02 turns a brief into the production spec (state list with purpose, assets, hard lines); F04 audits real against placeholder assets; F05 is the accessibility and performance pass (reduced motion, LCP, CLS, INP).

### Task 21: Batch G, after the exact copy (G01-G06)
No rules (library-only: they apply to a clone of the same site, not to a recreation). Each states where the frozen copy lives (`public/clone/site.css`, `fixes.css`, `src/components/sites/<site>/`, the `data-clone-section` attribute of `docs/CLONE.md`) and what behaviour to give back without touching the pixel result; Verify includes re-running the app's Measure and staying above the clone's earlier accuracy.

At the end of Task 21: `len(load().prompts) == 50`, ids exactly A01-A10, B01-B07, C01-C12, D01-D07, E01-E03, F01-F05, G01-G06.

---

## Phase 4: proof, compare, documents

### Task 22: Real builder runs and the proof records

**Files:**
- Modify: `backend/src/agentfit/clone/prompt_library/proofs.json`
- Create (outside the repository, in the scratchpad): one folder for each builder run

- [ ] **Step 1: Compose** for `recent.design` and `inspora.design` (the video gallery and the GSAP and scroll-snap one) with the isolated core; save the two prompts.
- [ ] **Step 2: Build.** Give each prompt, and nothing else of this repository, to a fresh builder agent working in its own scratch folder; tell it the gates may be written and passed. It builds a Next.js site with `[PLACEHOLDER]` assets.
- [ ] **Step 3: Measure** (code only): `npm install` and `npm run build` pass or not; serve the build; screenshots at 390, 768 and 1440 px; run a study on the served address (`http://127.0.0.1:<port>`) and compute `dna_match` against the reference fixture. Record every number, including failures.
- [ ] **Step 4: Record proofs.** For each recipe id used in a passing run, add `{builder, date, build_ok, widths, dna_match, notes}` to `proofs.json`, with `notes` saying it was tested inside a composed prompt and with which other ids. A failing run records nothing as tested; it becomes a note for the owner and, where the cause is a prompt, a fix.
- [ ] **Step 5: Claude Code discovery.** If the `claude` command is on this PC, run it headless in a saved project folder and ask it to list its project skills; if it is not, say discovery was not checked.
- [ ] **Step 6: Run** `pytest tests/test_clone_library.py -q` (the pin ignores proofs). Say what the numbers do not cover: whether the page looks like the reference, fine behaviour, inner pages.

### Task 23: `dna_match` in the interface

**Files:**
- Modify: `backend/src/agentfit/api/prompt_routes.py`, `desktop/src/pages/clone/MasterPrompts.tsx`, `desktop/src/lib/masterPrompts.ts`, `desktop/src/locales/it.txt`
- Test: `backend/tests/test_clone_compose.py`, `desktop/src/lib/masterPrompts.test.ts`

**Interfaces:** `GET /api/clone/{study_id}/dna/match?with=<other_id>` -> `dna_match(a, b)`; 404 when either has no DNA. `matchLines(m: DnaMatch): string[]` in `masterPrompts.ts` (overall and each part as percentages, `not_covered` in words; a `null` part reads "Not measured").

- [ ] **Step 1: Write the failing tests:** `test_the_match_route_compares_two_studies`; `test_the_match_route_404s_when_a_study_has_no_dna`; vitest `matchLines says Not measured for a null part`.
- [ ] **Step 2: Run** both. Expected: FAIL.
- [ ] **Step 3: Implement** the route and a "Compare with another study" line in the panel.
- [ ] **Step 4: Run** the tests and `npm run typecheck; npm run lint; npm test; python -X utf8 scripts/build_locale.py`. Expected: PASS.
- [ ] **Step 5: Real run:** compare the Task 22 result with its reference in the panel; screenshot.

### Task 24: Documents, notices, full checks, frozen build

**Files:**
- Modify: `docs/CLONE.md` (mode table gets Study; new sections *Master prompts* and *Site DNA*; the Skills table gets the three design packs and the `purpose` note), `docs/DECISIONS.md`, `docs/LESSONS.md`, `THIRD_PARTY_NOTICES.md`, `README.md` (the Clone feature line)
- Test: none new

- [ ] **Step 1: DECISIONS** (format `## Title`, `*date. Detail: file.md, "Section".*`, paragraph): the library is curated and the composer is code; a site's DNA is collected by a script of its own and interpreted in Python, in a model-less study; third-party skill scripts are not copied and design packs are separate from picture packs; titles carry their Italian in the file.
- [ ] **Step 2: LESSONS:** a word found in a bundle is not a library (`rapier` in `browserapierrors`); a pack added to `PACKS` changes what every picture build fetches and copies; a name that already exists in a package (`clone/prompts.py`) is found by listing the folder before writing the plan.
- [ ] **Step 3: NOTICES:** check the three packs are listed with commit, licence and the MotionSites split quoted.
- [ ] **Step 4: Full checks.** `python -X utf8 scripts/check.py --all` passes (ruff, `lint-imports`, every pytest file, typecheck, eslint, vitest, the Italian catalogs, the map `--check`). Regenerate the map with `python -X utf8 scripts/build_context.py`.
- [ ] **Step 5: Frozen build.** Run the PyInstaller step as `scripts/build.ps1` lines 76-80 do (same arguments), into a scratch `--distpath`; check the output holds `agentfit/clone/prompt_library/shared.md`, one prompt file, `dna.mjs`, `dna_lib.mjs`, `dna_signatures.json`. If any is missing, fix the packaging (`--collect-data agentfit` should carry them) and run again.
- [ ] **Step 6: Report** what was run and what was not: the real runs of Tasks 10, 11, 14, 22, 23 with their numbers; the frozen-build check; the prompts that have no proof ("Not tested yet"); and the follow-ups of the spec's section 7.
