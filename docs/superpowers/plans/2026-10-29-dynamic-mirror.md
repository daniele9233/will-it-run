# Dynamic Mirror Implementation Plan

Historical implementation plan (2026-10-29). Dynamic mirror is implemented in
0.4.0; see [current documentation](../../CLONE.md#dynamic-mirror). Original task
checkboxes are retained as planning history, not the current implementation status.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fourth way to start in Clone a site, "Dynamic mirror": code records the homepage as a browser loads it (scroll and wheel included), a local server replays it, and code measures how the replay compares with the live page.

**Architecture:** A model-less workflow of two checks (`mirror_record`, `mirror_verify`) like Study (`dna.py`). A Node script records responses; a Python standard-library threaded server replays them; Node probes and the existing DNA collector measure live and replay; Python writes a report of numbers. The interface adds a card and a view; nothing existing changes.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, Playwright library (the app's own `clone-browser`), Node (`.mjs`), React/TypeScript.

**Spec:** `docs/superpowers/specs/2026-10-29-dynamic-mirror-design.md` (binding; read it first).

## Global Constraints

- Work uncommitted unless the owner asks (AGENTS.md: commit and push only when asked); register new files with `git add -N` so the code map sees them.
- Interface strings `t("English")` with `English ||| Italiano` in `desktop/src/locales/it.txt` (CRLF kept); core strings `tr("English")` with the pair in `backend/src/agentfit/locales/it.py`. Never edit `desktop/src/locales/it.ts`, `docs/map/`, `.codegraph/`.
- Unknown is `None` / "Not measured", never a default that looks like data. The code never calls a mirror "faithful": it shows numbers.
- Tests never touch the network, LM Studio, a GPU or the owner's data; browser tests are opt-in with `WIR_LIVE_BROWSER` (the path of `tools\clone-browser`), as `test_clone_dna_script.py`.
- Routes are written `@app.get("/api/...")` / `@app.post("/api/...")`, registered before `/api/clone/{study_id}`.
- Imports obey `backend/.importlinter`; `mirror.py` sits in `clone/` like `dna.py` and must not import `api` or `app`.
- `mirror.mjs`, `mirror_probe.mjs`, `mirror_verify.mjs` repeat the launch code of `capture.mjs`/`dna.mjs` on purpose (visitor's Chrome, `serviceWorkers: "block"`, 1440 x 900, banners hidden by attribute, never removed); `capture.mjs` and `dna.mjs` are not edited.
- Limits (spec 3.1): `LIMITS = {"total_mb": 150, "file_mb": 40, "responses": 3000, "seconds": 120}`; only `http`/`https`; the replay server listens on `127.0.0.1` only.
- After each task: `python -X utf8 scripts/check.py`. Windows: `python -X utf8`, explicit encodings, `pathlib`. The editor tool turns a typed unicode escape into the character: use `chr(...)`.
- Files shipped with the package must be found by PyInstaller's `--collect-data agentfit` (they live in `backend/src/agentfit/clone/`).

## Review Focus

- A recorded text file holding a URL of a host that was not recorded (a runtime-built URL): the replay answers 404 with `X-Mirror: not-recorded`, the report counts it and lists it as a leak when the page reached the real host; nothing crashes.
- A page that scrolls vertically versus a wheel-driven page (document height at most one viewport): the right driver is chosen and the report says which was used.
- A first replay load that is blank (visible words 0) is detected, reloaded once, and both loads are in the report.
- A response bigger than the file limit, more responses than the cap, or a main document answered with 400 or more: a limit entry or a `fatal` naming the status, never an empty "success".
- A request path with `..`, an encoded slash or a very long path never reads a file outside `mirror/files/`.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/src/agentfit/clone/mirror.py` (new) | Models, limits, the URL rewrite, paths, the workflow (definition, checks, `register()`) |
| `backend/src/agentfit/clone/mirror_server.py` (new) | The threaded replay server and its request log |
| `backend/src/agentfit/clone/mirror.mjs` (new) | The recorder (Node, Playwright) |
| `backend/src/agentfit/clone/mirror_probe.mjs` (new) | One page, N scroll/wheel steps: numbers of motion, document facts, screenshots |
| `backend/src/agentfit/clone/studio.py` (modify) | `start_mirror`, `mirror`, `mirror_preview`, mode `mirror` in `start`, `list`, `_shots` |
| `backend/src/agentfit/api/clone_routes.py` (modify) | `POST /api/clone/mirror`, `GET /api/clone/{id}/mirror`, `POST /api/clone/{id}/mirror/open` |
| `desktop/src/lib/mirror.ts`, `desktop/src/pages/clone/MirrorView.tsx` (new); `cloneSite.ts`, `CloneSite.tsx`, `it.txt` (modify) | The card, the view, the report lines |
| `backend/tests/test_clone_mirror*.py`, `backend/tests/fixtures/mirror/` (new) | Tests and the local fixture site |

---

### Task 1: Models, limits, rewrite (pure code)

**Files:**
- Create: `backend/src/agentfit/clone/mirror.py`, `backend/tests/test_clone_mirror.py`

**Interfaces:**
- Produces:
```python
LIMITS: dict[str, int]                      # as the Global Constraints
class Entry(BaseModel): url: str; host: str; path: str; status: int; content_type: str | None; file: str | None; bytes: int; order: int; rewritten: bool = False
class Limit(BaseModel): code: str; count: int | None = None      # "too_large", "too_many", "timeout", "wheel_driver", "blank_first_load"
class MirrorIndex(BaseModel): schema_version: Literal[1] = 1; url: str; final_url: str; host: str; captured_at: str; viewport: tuple[int, int]; document: str | None; entries: list[Entry]; limits_hit: list[Limit]; not_recorded: list[str]; driver: Literal["scroll", "wheel"] | None = None
class MirrorReport(BaseModel): ...   # fields in Task 5; defined here with every field Optional so unknown stays None
def mirror_dir(workspace: Path) -> Path                      # workspace / "mirror"
def local_path(entry_host: str, entry_path: str, document_host: str) -> str   # "/_m/<host>/<path>" ; the document's own host keeps its root-relative path
def rewrite_text(text: str, document_host: str, recorded_hosts: set[str]) -> tuple[str, bool]   # absolute and protocol-relative URLs of recorded hosts -> local paths; nothing else edited
def safe_file(root: Path, name: str) -> Path | None          # the hash file under root/"files", or None for anything else
```
- [ ] **Step 1: Write the failing tests:** `test_the_index_leaves_an_unmeasured_field_none`; `test_rewrite_turns_an_absolute_url_of_a_recorded_host_into_a_local_path` (`https://cdn.x.com/a.js?v=2` -> `/_m/cdn.x.com/a.js?v=2`); `test_rewrite_handles_protocol_relative_and_own_host_urls`; `test_rewrite_leaves_a_host_that_was_not_recorded_and_code_outside_urls_alone` (returns changed=False); `test_safe_file_refuses_dotdot_encoded_slashes_and_long_names` (Review Focus 5); `test_limits_are_the_spec_values`.
- [ ] **Step 2: Run** `pytest tests/test_clone_mirror.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the module (models, `rewrite_text` with one compiled regex for `https?://host/` and `//host/`, `safe_file` accepting only `^[0-9a-f]{16,64}$`).
- [ ] **Step 4: Run** the file and ruff. Expected: PASS. **Step 5:** `python -X utf8 scripts/check.py`.

### Task 2: The replay server

**Files:**
- Create: `backend/src/agentfit/clone/mirror_server.py`; extend `backend/tests/test_clone_mirror.py` (or `test_clone_mirror_server.py`)

**Interfaces:**
- Consumes: `MirrorIndex`, `Entry`, `safe_file`, `local_path` (Task 1).
- Produces:
```python
class MirrorServer:
    def __init__(self, root: Path) -> None            # root = mirror_dir(workspace)
    def start(self) -> str                            # "http://127.0.0.1:<port>/" ; second call returns the same address
    def stop(self) -> None
    def requests(self) -> list[dict[str, Any]]        # {"path", "status", "bytes", "not_recorded": bool}
```
Behaviour: `ThreadingHTTPServer` on a free port of `127.0.0.1`; `/` is the recorded document; a path is looked up by recorded URL (own host: the root-relative path; other hosts: `/_m/<host>/...`); `Range` gives `206` with `Content-Range` and `Accept-Ranges: bytes`; headers: the recorded `content-type`, `cache-control: no-store`, `access-control-allow-origin: *`; an unknown path is `404` with `X-Mirror: not-recorded`; only `GET`/`HEAD`.
- [ ] **Step 1: Failing tests** (a `tmp_path` mirror with a hand-written `index.json` and two files): `test_a_recorded_file_is_served_with_its_type`; `test_a_range_request_gets_206_and_the_bytes_asked_for`; `test_an_unrecorded_path_is_a_404_marked_not_recorded_and_logged`; `test_a_path_with_dotdot_or_an_encoded_slash_never_reads_a_file`; `test_twenty_connections_at_once_all_get_their_file` (threads; the spike's blank first load); `test_stop_frees_the_port`; `test_only_127_0_0_1_listens`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** with `http.server`; log under a lock. **Step 4: Run** (PASS). **Step 5:** `python -X utf8 scripts/check.py`.

### Task 3: The recorder `mirror.mjs` and the fixture site

**Files:**
- Create: `backend/src/agentfit/clone/mirror.mjs`, `backend/tests/fixtures/mirror/site/` (`index.html`, `app.js`, `late.js`, `strip.css`, `font.woff2` as a few bytes, `clip.mp4` a few KB made by the test with ffmpeg only if present else a stub, `other/` for a second host on another port), `backend/tests/test_clone_mirror_script.py`

**Interfaces:**
- Produces: `node mirror.mjs <config.json>`, config `{"url", "out", "executable", "locale"?: string, "limits": LIMITS}`, env `WIR_BROWSER_DIR`; writes `<out>/index.json` (a `MirrorIndex`), `<out>/files/<hash>` (decoded bodies; rewrite is Task 4's Python step, the recorder stores originals), `<out>/thumb.jpg`; stdout JSON lines `progress` (steps `visit`, `scroll`, `wheel`, `record`, `write`), `done`, `fatal` as `dna.mjs`.
- Behaviour pinned in the spec 3.1: visit like a visitor; wait `load`, network idle (at most 8 s) and 1.5 s; scroll pass in steps of 0.8 viewport with 200 ms pauses; if the document height is at most one viewport, the wheel driver (pointer at the centre, 40 steps of 120 px down then back, 100 ms pauses) and `driver: "wheel"`; every GET response recorded (decoded body, hashed file name, the headers kept per spec); the limits; a video requested by range is fetched once whole with a plain request (up to the file limit); a main document with status 400 or more, a failed navigation or more than 120 s ends with `fatal` holding the status text.
- [ ] **Step 1: Failing tests** (opt-in `WIR_LIVE_BROWSER`, a `http.server` on `127.0.0.1` serving the fixture, and a second one for the other host): `test_the_recorder_records_the_document_the_script_loaded_after_scroll_and_the_other_host` (index holds `late.js` fetched only after a scroll event, a font, the second host's file, `thumb.jpg` exists); `test_a_wheel_driven_page_uses_the_wheel_driver` (the fixture's second page has a body of one viewport and a horizontal strip moved by `wheel`; `driver == "wheel"` and a file only fetched on wheel is recorded); `test_a_page_that_answers_403_ends_with_a_fatal_naming_the_status`; `test_a_file_over_the_limit_is_a_limit_entry_not_a_crash` (a config with `file_mb: 0`).
- [ ] **Step 2: Run** with `WIR_LIVE_BROWSER` set to the engine (PowerShell: `$env:WIR_LIVE_BROWSER = "$env:LOCALAPPDATA\Will It Run\Data\tools\clone-browser"`): FAIL. **Step 3: Implement** the script (copy the launch and consent-banner code of `dna.mjs`). **Step 4: Run** (PASS, none skipped). **Step 5:** `python -X utf8 scripts/check.py`.

### Task 4: The record check and the workflow

**Files:**
- Modify: `backend/src/agentfit/clone/mirror.py`; extend `backend/tests/test_clone_mirror.py`

**Interfaces:**
- Consumes: `pipeline._install`, `pipeline._run_script`, `pipeline._step`, `pipeline._fail`, `pipeline.research`, `host_of` (as `dna._clone_dna` does), Tasks 1-3.
- Produces:
```python
WORKFLOW = "clone-site-mirror"
RECORD_SCRIPT = HERE / "mirror.mjs"
def _record_script(ctx: CheckContext, config: dict[str, Any]) -> tuple[bool, str]      # the seam tests replace, as dna._script
def _clone_mirror_record(check: CheckDef, ctx: CheckContext) -> dict[str, Any]
    # runs the script, then rewrites the recorded text files in place (rewrite_text, only text/html, css, javascript, json), marks Entry.rewritten, saves index.json
    # returns {"passed": True, "verdict": "RECORDED", "summary": str} or _fail(reason, "MIRROR_FAILED")
def definition(*, url: str) -> WorkflowDef        # checks "record" then "verify", START -> record -> verify -> END, no agent, no model
def register() -> None                            # CheckSpec(kind="clone_mirror_record", listed=False); the verify spec comes in Task 5; called at module level
```
`studio.py` imports `mirror` beside `dna` so `register()` runs.
- [ ] **Step 1: Failing tests:** `test_the_record_step_rewrites_recorded_text_and_keeps_binary_files_untouched` (fake `_record_script` writing a small mirror); `test_a_site_that_refuses_ends_with_what_it_answered` (`(False, "HTTP 403 Forbidden")` -> `verdict == "MIRROR_FAILED"`, summary holds `403`); `test_the_mirror_definition_validates_with_no_agent`; `test_the_record_check_is_registered_and_not_listed`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement.** **Step 4: Run** the file plus `tests/test_workflows*.py`. **Step 5:** `python -X utf8 scripts/check.py`.

### Task 5: The probe, the verification and the report

**Files:**
- Create: `backend/src/agentfit/clone/mirror_probe.mjs`; modify `backend/src/agentfit/clone/mirror.py`; extend tests; `backend/tests/test_clone_mirror_script.py`

**Interfaces:**
- Produces: `node mirror_probe.mjs <config.json>`, config `{"url", "out", "executable", "viewport": [w, h], "steps": 20, "driver": "scroll" | "wheel", "shots": "<dir>"}`; writes `<out>` JSON: `{"height", "sections", "elements", "img", "video", "canvas", "words", "smooth_scroll": "lenis" | "native" | "none" | null, "steps": [{"scroll_y", "items": [{"key", "tx", "ty", "opacity", "left", "top"}]}]}` for the 60 largest visible elements (key = a stable path of tag, index and class), screenshots at 5 positions into `shots`; numbers only, no page text, no addresses.
- In `mirror.py`:
```python
class MirrorReport(BaseModel)        # schema_version 1, live_at, replay_at, dna_match: dict | None, facts: dict[str, dict] (per width: both values and "equal"), motion: {"moving_live", "moving_replay", "matching"(share) } | None, pixels: list[{"at", "within_16"}] | None, requests: {"not_recorded": int, "leaked_hosts": list[str]}, loads: list[{"visible_words": int}], driver, not_covered: list[str], seconds: float
def motion_compare(live: dict, replay: dict) -> dict | None      # None when either has no steps; matching = share of live-moving elements whose sequence stays within 2 px / 0.02 opacity of the replay's
def pixel_share(a: bytes, b: bytes) -> float                       # share of pixels whose channels differ by at most 16/255 (PNG bytes; Pillow is already a dependency, check pyproject first)
def _verify_script(ctx, config) -> tuple[bool, str]                # the seam (runs mirror_probe.mjs and dna.mjs for the two addresses)
def _clone_mirror_verify(check, ctx) -> dict[str, Any]            # starts MirrorServer, studies and probes live and replay at 1440 and 390, reloads once on a blank first load, writes report.json; "MIRRORED" or "MIRROR_FAILED"
```
`not_covered` always lists: hover, click, other pages, sound, touch, login, what loads only after a long wait.
- [ ] **Step 1: Failing tests:** `test_motion_compare_counts_moving_elements_and_matching_ones` (hand-built steps); `test_motion_compare_is_none_without_steps`; `test_pixel_share_is_1_for_equal_images_and_lower_for_a_changed_block`; `test_the_report_says_what_it_does_not_cover`; `test_a_blank_first_load_is_reloaded_once_and_both_loads_are_reported` (fake `_verify_script`, Review Focus 3); `test_a_leak_to_another_host_is_counted_and_listed`; live (opt-in): `test_the_probe_finds_the_wheel_moved_strip_moving_in_live_and_replay`.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** the script and the Python. **Step 4: Run** the files (live ones with `WIR_LIVE_BROWSER`). **Step 5:** `python -X utf8 scripts/check.py`.

### Task 6: Studio entries and routes

**Files:**
- Modify: `backend/src/agentfit/clone/studio.py`, `backend/src/agentfit/api/clone_routes.py`; create `backend/tests/test_clone_mirror_api.py`

**Interfaces:**
- Produces: `CloneStudio.start_mirror(url: str, *, study_id: str | None = None) -> tuple[dict, RunRecord]` (entry: id, url, title, slug, host, created_at, updated_at, `mode "mirror"`, workspace, `source {"kind": "none"}`; deleting old `mirror/` files only after `runs.start` succeeds, as `start_study` does); `start()` sends a `mirror` entry to `start_mirror`; `CloneStudio.mirror(study_id) -> MirrorReport | None` (None until `report.json` exists); `mirror_preview(study_id) -> str` (one `MirrorServer` per study, stopped with the app like `_previews`); `_shots` for `mirror` returns `mirror/thumb.jpg` when it exists; routes `POST /api/clone/mirror` body `{"url": str}` -> `{"study", "run"}` (400 for `CloneError`, 409 as `require_engine()`), `GET /api/clone/{study_id}/mirror` (404 until written), `POST /api/clone/{study_id}/mirror/open` -> `{"url"}` (409 when there is no recording yet). New error and route strings get Italian pairs.
- [ ] **Step 1: Failing tests** (the studio factory as `test_clone_study.py`): `test_a_mirror_entry_is_listed_with_its_mode_and_thumbnail`; `test_a_mirror_runs_again_from_its_entry_and_keeps_its_mode`; `test_a_mirror_entry_can_be_read_and_deleted_like_a_clone`; `test_the_report_is_none_until_written_and_then_valid`; `test_a_run_that_cannot_start_leaves_the_previous_recording`; `test_the_mirror_route_refuses_without_the_engine_with_409`; `test_the_report_route_404s_before_the_run_wrote_it`; `test_open_serves_the_recording_and_answers_with_an_address_the_app_may_open` (`127.0.0.1` with a path).
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** (mirror `start_study` and the study routes). **Step 4: Run** the file plus `tests/test_clone.py tests/test_clone_study.py tests/test_clone_picture.py tests/test_clone_replica.py`. **Step 5:** `python -X utf8 scripts/check.py` (the map's route test needs the new file registered with `git add -N`).

### Task 7: The interface

**Files:**
- Create: `desktop/src/lib/mirror.ts`, `desktop/src/lib/mirror.test.ts`, `desktop/src/pages/clone/MirrorView.tsx`; modify `desktop/src/lib/cloneSite.ts` (`CloneMode`, `stagesOf`, `stageText`; add `MIRROR_STAGES`), `desktop/src/pages/CloneSite.tsx` (the start card and `CloneCard` badge), `desktop/src/locales/it.txt`

**Interfaces:**
- Produces: `CloneMode = ... | "mirror"`; `MIRROR_STAGES: Stage[] = [{ id: "record", check: true }, { id: "verify", check: true }]` with `t()` stage texts; `lib/mirror.ts` exports `MirrorReport` (the JSON, snake_case as in Python), `matchLines(r): string[]`, `motionLines(r): string[]`, `pixelLines(r): string[]`, `gapLines(r): string[]` (counts of 404s and leaked hosts; `not_covered` in words; every `null` reads "Not measured"). `MirrorView` shows stages, thumbnail, the Report card, the warning ("A site's text, logos, pictures, videos and code belong to its owner. The recording stays on this PC; do not publish it."), buttons **Open the mirror** (posts `/mirror/open`, opens the returned address) and **Run again**, and the error text of a failed study (reuse the failed-study helper of `FromSite.tsx`: a failed mirror shows the reason the site gave, not "not finished").
- [ ] **Step 1: Failing vitest tests:** `stagesOf mirror is record then verify`; `matchLines says Not measured for a null part`; `motionLines gives counts and the matching share as a percentage`; `gapLines lists leaked hosts and the 404 count`; `gapLines lists what is not covered`.
- [ ] **Step 2: Run** `npm test -- mirror cloneSite` (FAIL). **Step 3: Implement.** **Step 4: Run** `npm run typecheck; npm run lint; npm test; python -X utf8 scripts/build_locale.py`. **Step 5:** `python -X utf8 scripts/check.py`.

### Task 8: Real runs (the numbers the spec asks for)

**Files:**
- Create (outside the repository, in the scratchpad): the recordings and screenshots of each run; `D:\SotwareKikko\.superpowers\sdd\<this plan>\mirror-runs.md` for the numbers

- [ ] **Step 1: Isolated core and UI** as the memory recipe `isolated-live-test-recipe.md` (scratch `AGENTFIT_HOME`, core on 8766, `willitrun-ui-token` launch entry).
- [ ] **Step 2: Run** the mirror on `https://brandonyasin.com/` from the interface; record the seconds of each step, the size of the recording, the report numbers (pixels per position, `dna_match` and its parts, moving and matching elements, 404s, leaked hosts, driver) and compare with the spike: pixels 96-100 %, `dna_match` 0.95, carousel wrap points near 2021, 1338, 735, 134, 2 missing files in the model's copy.
- [ ] **Step 3: Run** it on one site with vertical scroll and scroll-linked motion (choose with the owner's examples in `docs/`: `https://www.footer.design/` or `https://sceneai.art/`, which have DNA fixtures); same numbers.
- [ ] **Step 4: Fix** what the runs expose (an address the rewrite missed, a blank first load, a limit too small) with a test for each, never by loosening an assertion. Press **Open the mirror** and look at the replay in the browser pane at 1440 and 390 px (one screenshot per state; read text with `read_page`).
- [ ] **Step 5:** `python -X utf8 scripts/check.py`. Report what was not run: hover, click, other pages, a site that needs a login, a site that blocks automated browsers.

### Task 9: Documents and the frozen build

**Files:**
- Modify: `docs/CLONE.md` (mode table gets Dynamic mirror and a section), `docs/DECISIONS.md`, `docs/LESSONS.md`, `README.md` (the Clone line), `AGENTS.md` and `docs/AGENT_CONTEXT.md` (route count, read from the map)

- [ ] **Step 1: DECISIONS** (format `## Title`, `*date. Detail: file.md, "Section".*`, paragraph): the mirror is code, not a model; recorded files are replayed unchanged except URL rewriting; verification is numbers, never a verdict; the replay is local only and the warning is part of the screen; ditto and mimic are a later phase.
- [ ] **Step 2: LESSONS:** a model given a free prompt copies the original's compiled app and never verifies it (the 2026-10-29 spike: 610 s, a blank first load, 2 missing files, 9 pages instead of 1); a single-threaded server leaves the first load blank while a video streams; a wheel-driven page has no vertical scroll, so a scroll-only probe sees nothing.
- [ ] **Step 3: Full checks:** `python -X utf8 scripts/check.py --all`; regenerate the map with `python -X utf8 scripts/build_context.py`.
- [ ] **Step 4: Frozen build:** the PyInstaller step as `scripts/build.ps1` lines 76-80 into a scratch `--distpath`; the output holds `agentfit/clone/mirror.mjs`, `mirror_probe.mjs`, `mirror_server.py`'s compiled module and `willitrun-core.exe version` runs; if a file is missing fix the packaging and run again.
- [ ] **Step 5: Report** what ran and what did not, with the numbers of Task 8 and the follow-ups of the spec's sections 9 and 10.
