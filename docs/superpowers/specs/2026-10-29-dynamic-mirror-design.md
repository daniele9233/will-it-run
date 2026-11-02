# Dynamic mirror: design

Status: historical design (2026-10-29); Dynamic mirror is implemented in the 0.4.0 release. See [current documentation](../../CLONE.md#dynamic-mirror). The proposal below records the original intent, not a claim that every proposed detail was shipped.

## 1. Why

"Exact copy" freezes a page: scripts are dropped, so scroll-linked motion, carousels and canvas scenes do not survive. "Agent rebuilds it" and "From a picture" need a strong model and still lose motion. On 2026-10-29 the owner typed one free prompt (five repositories as skills, then an address) into the Chat page with the local model `qwen3.8-27b-gsq-rco-mtp`, on `https://brandonyasin.com/`. In 610 s the model downloaded the site (9 pages, 154 files, 67.5 MB) and served the original's compiled application unchanged. The result matched the original on 96-100 % of the pixels, had the same 4 sections, 785 elements and 34 images, and wrapped its wheel-driven carousel at the same positions (`dna_match` 0.95). It did not use ditto's or mimic's engines, started no server of its own, took no screenshot, left one blank first load and 2 missing files, and downloaded 9 pages when the owner wanted one. One run, one site, no verification by code.

The motion was kept because the original's runtime was kept. That is the useful thing; the unreliable parts were the model's choices. This design moves every choice into code and adds a verification that is measured, not claimed.

## 2. What it is, and what it is not

A fourth way to start in Clone a site, next to Exact copy, From a picture, Study and the agent kit (none of them changes): **Dynamic mirror**.

- An address in; a **recording** of that page as a real browser loaded and scrolled it (every response, with its headers), a local server that replays it, and a **report** of numbers that compare the replay with the live page.
- It needs **no model**.
- It is an archive to open and study, on this PC. It is **not** a Next.js project and not an editable source: the page's own compiled code, fonts, pictures and videos are replayed as recorded. Turning motion into code of the owner's own is a later phase (section 9).
- Its use is the one of Exact copy: a site you own, or learning how a site is built. The screen repeats the warning that a site's text, logos, pictures, videos and code belong to its owner, and says that the recording stays on this PC and is not for publishing.

## 3. Pipeline

A workflow like Study (no agent, no model), built by `mirror.definition(url=...)` with two checks, registered like `dna.register()`:

```
mirror_record  ->  mirror_verify
```

Entry in the clone store: `mode "mirror"`, `source {"kind": "none"}`, the workspace holds `mirror/`. A re-run goes on from the entry like Study does (`start()` sends a `mirror` entry to `start_mirror`).

### 3.1 mirror_record (code, `mirror.mjs`)

Repeats the launch code of `capture.mjs` and `dna.mjs` on purpose (the spec of 2026-10-28 section 6 chose this so the proven copy is not touched): the visitor's Chrome with the user-agent fix, `serviceWorkers: "block"`, 1440 x 900, consent banners hidden with an attribute and never removed.

1. Open the address; wait for `load`, for network idle (at most 8 s) and 1.5 s.
2. **Record every response** the page receives for the whole visit (Playwright `response` events, body from `response.body()`): URL (with query), method GET only, status, content type, the response headers that matter for replay (`content-type`, `content-encoding` is dropped because the body is stored decoded, `cache-control` is dropped, `access-control-*` and `timing-allow-origin` are set to allow, `content-security-policy*` and `x-frame-options` are dropped, `content-range` handled below), the body in a file named by the hash of the body, and the order of arrival.
3. **Drive the page** so what loads on interaction is recorded: scroll down and back in steps of 0.8 viewport with 200 ms pauses; if the document does not grow (height at most one viewport), the page is wheel-driven: move the pointer over the page centre and send 40 wheel steps of 120 px down, then back, with 100 ms pauses (the homepage of the spike). Videos that start by themselves are left playing; a video's later byte ranges are recorded in whole by fetching the file once with a plain request when the page requested a range.
4. Hover and click are **not** driven (they would change the page). What the visit did not load is not in the recording; the report says so.
5. Limits, all fixed in the code and shown in the report: only the one document (the address given; links to other pages are not followed), at most 150 MB in all and 40 MB for a file (a larger response is recorded as "too large": the replay answers 404 for it and the report counts it), at most 3,000 responses, 120 s for the whole step, only `http` and `https` responses, bodies of at most the limit. Failed navigation, or the main document answered with status 400 or more, ends with a `fatal` event holding the status text (the rule of the DNA collector).
6. Writes `<workspace>/mirror/index.json` (schema in section 5) and `mirror/files/<hash>` and `mirror/thumb.jpg` (first screen). The step reports through the same JSON lines as `capture.mjs` (`progress`, `done`, `fatal`; steps `visit`, `scroll`, `wheel`, `record`, `write`).

### 3.2 The replay server (`mirror_server.py`, no Node)

A threaded HTTP server of the Python standard library on `127.0.0.1:<free port>`, started on demand and stopped when the app stops (like the clone preview), many connections at once (the spike's single-threaded server left the first load blank while it streamed videos).

- The recorded document is served at `/`; every recorded URL on any host is served at `/_m/<host>/<path-and-query-hash>` (the path of the original on the original host is kept as it was when the host is the document's own, so root-relative URLs of the page keep working at their original paths: `/_nuxt/x.js` is answered because the document's host is the default).
- **Rewrite at record time**, once, in the stored copy (never in the original files): in the text of HTML, CSS, JS and JSON responses, absolute URLs of a recorded host (`https://cdn.example.com/a.js`) become `/_m/cdn.example.com/a.js`, and protocol-relative ones too; an absolute URL of the document's own host becomes a root-relative one. Nothing else in the code is edited. A request the replay cannot answer is a **404 with a header `X-Mirror: not-recorded`**, never an empty 200, and is counted.
- HTTP Range (`206`, `Content-Range`, `Accept-Ranges: bytes`) is supported for video and audio.
- Headers: the recorded `content-type`; `cache-control: no-store`; CORS open.
- A request log in memory (path, status, bytes) is what the verification reads.

### 3.3 mirror_verify (code)

Runs after the record, against the **live address** and the **replay**, in one browser each (same launch code), at 1440 x 900 and 390 x 844. It never asks a model.

1. **Study both** with `dna.mjs` (reused as it is, `WIR_BROWSER_DIR` and the same config) and compute `dna.dna_match(live, replay)`: overall and each part, with `None` parts left as they are.
2. **Document facts**: document height, count of `section`, elements, `img`, `video`, `canvas`, visible words, font families, palette roles (the DNA already holds them) for both widths; each is compared and the report says equal, or the difference.
3. **Motion probe** (`mirror_probe.mjs`, new): for 20 steps (a wheel step with the pointer over the page when the document does not scroll, otherwise a scroll step of 0.5 viewport), after 150 ms, record for the 60 largest visible elements the 2D `transform`, `opacity` and the bounding-box left and top; and `window.scrollY` and `Lenis`/`ScrollTrigger` presence. Output per step is numbers only. Compare live and replay: the number of elements whose transform changes at least once, and for each the sequence correlation; the report holds the counts and the share of elements whose sequence matches within 2 px or 0.02 opacity. No judgement beyond the numbers.
4. **Pixels**: screenshots of both at scroll positions 0, 25, 50, 75 and 100 % (or after 0, 5, 10, 15 and 20 wheel steps when the page does not scroll), animations as they are; the share of pixels whose channels differ by at most 16/255 (what the spike measured), per position. No threshold decides "pass".
5. **Leaks and gaps**: the replay's request log: count of requests answered 404 (`X-Mirror: not-recorded`), and the count of requests the page made to a host other than `127.0.0.1` (a leak to the original; it is not blocked, it is shown). A replay whose first load is blank is detected (visible words 0) and the study reloads once; both loads are in the report.
6. Writes `mirror/report.json` and `mirror/shots/`. The step ends with verdict `MIRRORED` when the recording was written and the replay served the document; the numbers say how good it is. It ends `MIRROR_FAILED` with the reason when the record or the serving failed. A copy is never called "faithful" by the code: the interface shows the numbers.

Everything the replay did not load while verifying is unknown, not fine: the report lists what was **not covered** (hover, click, other pages, sound, touch, login, what only loads after a long wait).

## 4. Interfaces

```python
# backend/src/agentfit/clone/mirror.py
WORKFLOW = "clone-site-mirror"
RECORD_SCRIPT = HERE / "mirror.mjs"; VERIFY_SCRIPT = HERE / "mirror_verify.mjs"; PROBE_SCRIPT = HERE / "mirror_probe.mjs"
LIMITS = {"total_mb": 150, "file_mb": 40, "responses": 3000, "seconds": 120}
class MirrorIndex(BaseModel)  # schema_version 1, url, final_url, host, captured_at, viewport, document: str (hash), entries: list[Entry], limits_hit: list[Limit], not_recorded: list[str]
class Entry(BaseModel)        # url, host, path, status, content_type, file (hash), bytes, order, rewritten: bool
class MirrorReport(BaseModel) # schema_version 1, dna_match, facts (both widths), motion, pixels, requests {not_recorded, leaked_hosts}, loads (list), not_covered: list[str], seconds
def definition(*, url: str) -> WorkflowDef; def register() -> None   # CheckSpec(kind="clone_mirror_record", listed=False) and "clone_mirror_verify"
def mirror_dir(workspace: Path) -> Path
class MirrorServer: start(workspace) -> str; stop(); requests() -> list[dict]
```

Studio (`CloneStudio`): `start_mirror(url, *, study_id=None)`, `mirror(study_id) -> MirrorReport | None` (None until the run wrote `report.json`), `mirror_preview(study_id) -> str` (starts the server, returns `http://127.0.0.1:<port>/`), listing and `_shots` for `mirror` entries (thumb), `delete` as for a clone.

Routes (written as `@app.get/@app.post("/api/...")`, registered before `/api/clone/{study_id}`): `POST /api/clone/mirror` body `{"url": str}` -> `{"study", "run"}` (400 for `CloneError`, 409 when the engine is missing, like `require_engine()`); `GET /api/clone/{id}/mirror` -> the report (404 until written); `POST /api/clone/{id}/mirror/open` -> `{"url"}` (starts the replay server).

## 5. Interface (desktop)

- `CloneSite.tsx`: the start screen gets a fourth card **Dynamic mirror** ("Records the page as a browser loads it and plays it back on this PC, with its motion; no model needed"). `CloneMode` gains `"mirror"`; `stagesOf` gets `MIRROR_STAGES = [{id: "record", check: true}, {id: "verify", check: true}]`. Nothing existing is removed or reworded.
- A mirror clone's view: the two stages, the thumbnail, a **Report** card (lib `mirror.ts`: `matchLines`, `motionLines`, `pixelLines`, `gapLines`, each returning `t()` strings; a `null` part reads "Not measured"), the list "Not covered", the warning text, and the buttons **Open the mirror** (`/mirror/open`, opened with the app's link opener for `127.0.0.1` addresses) and **Run again**.
- Strings through `t()` with Italian pairs in `it.txt`; core strings through `tr()` with pairs in `locales/it.py`.

## 6. Data and safety

- Everything under `<workspace>/mirror/`; deleting the clone deletes it. Nothing is sent anywhere. The replay server listens on `127.0.0.1` only.
- The address must be `http` or `https`; a host that resolves to a private address is refused unless it is `localhost` (the tests and a site the owner runs).
- The server answers only recorded URLs; it never fetches. A file name comes from a hash; no request path is used as a file path.
- No script of the recorded page is run by the app's Python: only the browser runs the page, in the cloner's own profile, as Exact copy does.

## 7. Tests

- `test_clone_mirror.py` (no browser, no network): the index and report models (unknown stays `None`), the rewrite function on strings (absolute, protocol-relative, own-host, query kept, no change to code outside URLs), the limits (`LIMITS` hit makes a limit entry, never a crash), the server (a recorded file is served with its type, Range returns 206, an unrecorded URL is a 404 with `X-Mirror`, a path with `..` is not a file read, many connections at once), the workflow definition validates, `register()` is listed false, `start_mirror` creates a `mirror` entry and a re-run keeps the mode, the routes (409 without the engine, 404 before the report).
- `test_clone_mirror_script.py` (opt-in, `WIR_LIVE_BROWSER`, a local static fixture page served on `127.0.0.1` with a wheel-driven horizontal strip, a video, a font, a script that loads another file after scroll and a second host on another port): the recording holds those files; the replay serves the page; the motion probe finds the strip moving in both; a 403 page ends with a `fatal` naming the status.
- Vitest for `lib/mirror.ts` and the stage list.
- Real runs, with numbers in the report of the task: `https://brandonyasin.com/` (to compare with the spike: pixels 96-100 %, `dna_match` 0.95, carousel wrap points) and one site with vertical scroll and scroll-linked motion. The seconds of each step are reported; no target is claimed before they are measured.

## 8. Not in this design

Following links or more than one page; hover, click and form driving; login, cookies of the person, API sites that need a live server; making the recording editable or exporting it as a project; publishing; audio; service workers; WebSocket traffic (counted as not recorded); anything paid or hosted.

## 9. Later (separate designs, only after this one is measured)

- Use the sampled motion (the probe's numbers) to compose a prompt for the owner's own agent, as Study does for the DNA; the composer would take a `motion` block.
- Look at ditto (`luigiluft/ditto`, MIT, created July 2026, 3 stars) and mimic (`sumanrox/mimic`, MIT, September 2026, 7 stars): their measuring and sampling scripts as pinned, optional steps. They are young; their value on a real site is untested and the spike did not run them. Ditto's alternative 3D asset generation needs a paid service and is out.

## 10. Open points for the owner

1. Verification compares with the **live** site at the time of the check; if the site changes between the record and the check, the numbers move. The report shows both capture times. Accepted for the first version.
2. A response over 40 MB is not recorded (a large video becomes a 404 in the replay, counted). Raise the limit if the owner's sites need it.
3. A site that serves different markup by cookie, country or time (the spike's clock and theme) differs by a few pixels; the report shows the pixel shares, it does not hide them.
