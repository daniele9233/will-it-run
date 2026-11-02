# Lessons

Things that went wrong once and cost time, with the cause and the rule that prevents them. One `##` section is one lesson. Read the ones that touch the area you are about to change.

Every section is also copied into the project memory (`willitrun memory import-docs`), so `willitrun memory recall "<error or topic>"` finds it by words. Add a lesson when a fix was not obvious or a mistake is easy to repeat: a new `##` section at the end, with what happened, why, and the rule. Decisions (why something is the way it is) go in DECISIONS.md.

## Civitai answers 401 without a personal token
*Found 2026-10-27, in `backend/src/agentfit/imagegen`.* Models listed from Civitai could not be downloaded: its API refuses a download that carries no token. The catalog keeps those entries flagged `needs_token`, the page says "Needs your Civitai token" and refuses before queueing, instead of failing halfway. The owner does not want to use Civitai at all: add models from Hugging Face files that need no token, verify the URL, size and checksum, and never ask for or look for a token.

## Qwen-Image 2.1 needs the leejet branch of ComfyUI-GGUF
*Found 2026-10-27.* With city96's ComfyUI-GGUF the Qwen-Image 2.1 files fail to load with "Unknown model architecture". The component in `imagegen/data/components.json` installs `https://github.com/leejet/ComfyUI-GGUF` and `tests/test_imagegen.py` asserts it. Restart ComfyUI after installing a custom node. The flow the owner confirmed in ComfyUI is `imagegen/data/workflows/qwen_image_txt2img.json`: keep its defaults.

## Pony V6 draws an illustration from a sentence
*Found 2026-10-27.* Asked with a descriptive sentence it drew an illustration; asked with photographic tags (`photo (medium), realistic, real life, 1girl, solo, ...`) it made a photograph. Its entry carries a photographic negative prompt and its own workflow, `pony_txt2img`. Test a new model by looking at its picture, not by reading its model card.

## A model-written `<script>` must never reach an exported page
*Arena exports, `arena/export.py` and `arena/answers.py`.* The HTML reports contain no `<script` and no external address, and an answer that contains `<script>` is shown escaped (`&lt;script&gt;`). `tests/test_arena_api.py` checks both. A report is opened by a person with a browser; the text in it was written by a model.

## The Windows console is cp1252
A Python script that prints a character outside that code page (a star, an arrow, a dash) dies with `UnicodeEncodeError` when its output goes to a pipe or a console. Run scripts with `python -X utf8` (or `PYTHONUTF8=1`); the hook scripts switch their own output to UTF-8 and CI runs the scripts with `-X utf8`. The same applies to reading: open text files with an explicit `encoding`. The `willitrun memory` commands switch their own output to UTF-8 (`memory_output` in `cli.py`); before that, `memory recall` crashed on a cp1252 pipe when an entry held an arrow or a star.

## The code graph does not see calls made through an attribute
`codegraph callers BrainService.add` lists one caller; a text search finds ten, most of them written `memory.service.add(...)` or `self.service.add(...)`. Use the graph to find where to look, then confirm "these are all the callers" with `grep -rn "\.add(" backend/src` before changing a signature. Found by a fresh agent in the 2026-10-27 test (AGENT_CONTEXT.md, "Measured").

## A link with a `download` attribute does nothing inside the desktop window
The Images page saved pictures with `<a href="..." download>`. In the Tauri window (WebView2) the picture lives on another origin (the core on 127.0.0.1) and the attribute is ignored, so the button looked dead. Save through the window's Save dialog and a route that writes the file (`lib/imageSave.ts`, `POST /api/images/gallery/{id}/save`), the way the Arena saves its reports, and fall back to a fetched blob only in a plain browser.

## A text file that does not end with a newline glues the next appended line to it
`desktop/src/locales/it.txt` has Windows line endings and no newline after its last line; `cat >> it.txt` joined the first new pair to the last old one and the catalog check reported one string missing. Look at the end of a file (`tail -c 100 file | od -c`) before appending, and keep one kind of line ending in it.

## Some source files begin with a byte order mark
`backend/src/agentfit/api/server.py` is one. Text read as plain UTF-8 starts with U+FEFF and `ast.parse` fails with "invalid non-printable character U+FEFF". Read source with `encoding="utf-8-sig"` (the map generator and the hooks do).

## Do not write code through a shell heredoc full of escapes
*This session's mistake, twice.* A `python - <<'EOF'` that patches a file with `\n` escapes turned them into real line breaks and corrupted `brain/service.py`; the fix was `git checkout -- <file>` and writing the change with the editor tools. Create and edit files with the file tools (Write, Edit), not with a command that rewrites text inside a quoted string. Heredocs are fine for short read-only probes.

## Run ruff from `backend/`
Ruff's `src = ["src", "tests"]` is relative to where it is started. From the repository root it does not find `agentfit`, treats it as a third-party package and reports I001 (import order) on every file. CI and `scripts/check.py` run it from `backend/` (`ruff check src tests ../scripts`). Run `lint-imports` there as well.

## A `# noqa` for a rule that is not enabled is itself an error
With `RUF` selected in `backend/pyproject.toml`, `# noqa: A002` (or ANN201, E402 after a `sys.path` change) is reported as RUF100 because those rules are not on. Add a `noqa` only for a rule that is selected. The Arena's exceptions are already in `[tool.ruff.lint.per-file-ignores]` (RUF001 in the question bank and the export, which must say × and − as a reader sees them).

## The map lists what git has: stage a new file before expecting it in `docs/map/`
`scripts/build_context.py` reads the files that git tracks or has staged, so a scratch file nobody added cannot make the map differ from CI's. A brand new module is missing from the map until `git add <file>` (or `git add -N <file>`), then `python scripts/build_context.py`. The pre-commit hook does this for a commit.

## Routes must be written where the map and its test can read them
`docs/map/api.md` and `tests/test_context_map.py` read `@app.get("/api/...")` decorators in `backend/src/agentfit/api/`. A route added through a router, a computed path or `add_api_route` is served but absent from the map, and the test names the difference. Write the decorator directly, or teach `load_routes` in `scripts/build_context.py` the new form.

## The REST call that loads a model has no GPU offload and no idle timeout
*LM_STUDIO.md.* To load with a GPU ratio the app runs `lms load --gpu <ratio>`; workflow loads that need an idle timeout go through `lms load <key> -c <context> --ttl <seconds> -y` when the CLI is installed, and through REST without a timeout when it is not. Check for the CLI before assuming either.

## The MCP SDK renamed its server class
`mcp` 2.x moved `FastMCP` to `mcp.server.mcpserver.MCPServer`, and a tool object has `input_schema` instead of `inputSchema`. `brain/mcp_server.py` imports whichever exists and the tests only use `list_tools()` and `call_tool()`. When the package is upgraded, run `tests/test_project_memory.py` first.

## A fix made by a shell command is invisible to the session ledger
The ledger reads the file edits a session made through Edit and Write. A fix applied with `sed` or `python -c` leaves a failed check with no edit between the failure and the pass, so the error is recorded without a solution. Prefer the editor tools for changes to the project.

## Development screenshots are never committed
`.shots/` holds screenshots taken while testing, and they can show generated pictures. The GitHub repository is public. It is in `.gitignore`: keep it there, and do not add generated pictures to the repository.

## Two import cycles exist; do not add a third
`teams` and `workflows` import each other (a team is built from workflow definitions, a workflow store seeds the team templates), and so do `lmstudio` and `runtime`. `backend/.importlinter` leaves them alone on purpose. A new cycle between packages means the new code belongs somewhere else, usually lower: pass the thing in as an argument or put the shared part in a module both can import.

## A shell can have no `cargo` on PATH, and the release found out last
*Found 2026-10-27, publishing 0.2.0.* `scripts/release.ps1` passed lint, the tests and the frozen core, then stopped in the Tauri step with "failed to run 'cargo metadata' ... program not found". The Terminal panel's shell had no Rust folder on PATH: on this PC `~\.cargo\bin` does not exist, the compiler lives in `~\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin`, and the user PATH holds a broken entry made of PowerShell text. The release had already written the version into six files, so the tree stayed modified and the next run refused to start. `build.ps1` now looks for cargo at its start (PATH, `CARGO_HOME`, `~\.cargo\bin`, the rustup toolchains with stable first), for that run only, and stops there with a clear message when it is nowhere. If a release stops after the bump, restore the version files with `git checkout --` before running it again (`docs/RELEASING.md`).


## A cloud key can be saved with no model list, and nothing says so
*Found 2026-10-27, in `backend/src/agentfit/cloud/service.py`.* The owner saved a DeepSeek key on 14 October and DeepSeek never appeared in the Chat picker: the list of models is asked for once, right after the key is saved, and kept only on success. When that one call failed, the error went to the Settings row (gone when the page was left), nothing was logged, and nobody asked again, so the key stayed "present" with no models. `CloudService.load_missing()` now asks for the list of every provider that has a key and none (at most once a minute each), `models()` remembers and logs why it failed, and `status()["error"]` carries it to Settings and to the greyed entry of every model picker. The rule: a step that fills a cache must say why it could not, and something must try again.

## Removing a node that React owns makes the page throw
*Found 2026-10-27, capturing brioni.com (`clone/capture.mjs`).* Cookie banners and the newsletter dialog were removed with `element.remove()`; a few seconds later the site showed "Qualcosa è andato storto" and the capture was of its error page (362 elements instead of 1,300). The dialog belonged to React, which found its node gone. Pop-ups are hidden with an attribute and a rule (`[data-wir-hidden] { display: none !important }`, in a constructed style sheet so the page's head is not touched) and skipped when the page is frozen. They also come on timers: brioni.com's dialog appears about four seconds after the first scroll, so the sweep runs three times.

## A web component's shadow DOM is not in `outerHTML`
*Found 2026-10-27, brioni.com's carousels.* A frozen page showed its Swiper sections as empty boxes (sections 5, 7 and 9, a quarter of the page's height, scored 0 to 20%). `swiper-container` and `swiper-slide` keep their layout in shadow roots. The capture flattens them: the shadow styles are rewritten (`:host` to a class on the host, `::slotted` to a descendant) and each `<slot>` is replaced, where it stands in the shadow tree, by what the page put in the host. A `<slot>` first dropped as an unknown tag, found by the section's score, not by reading code.

## A site sends a phone other markup, not only other CSS
*Found 2026-10-27, brioni.com on a phone.* One copy at desktop width scored 99.9% at 1440 and 48% at 390: the header has other markup (a menu button, no map pin) and the hero another picture, chosen by script. The sections are frozen at both widths; a section whose phone markup differs has both, shown by a media query. After that: 99.8% at 390.

## The kit's skill does not fit a local model's room
*Found 2026-10-25, brioni.com, a 27B Cloner.* 776 tool calls in 2 h 39 min, 39 files written, no component, 310 trims of the conversation, 194 swaps between the model and its vision model. The room left for one answer is what the context has left after the prompt (`output_budget`, at least 1,024 tokens, thinking included): with the skill's 12K tokens and a full history, a `write_file` was cut off in the middle ("this call was cut off before its arguments were complete") and a thinking model used its turn thinking. A larger prompt cannot be made to fit a smaller model by adding guards: the work was redesigned (CLONE.md, "Why the exact copy exists"). Read a skill when it is needed, never all at the start.

## TypeScript rejects a faithful copy
*Found 2026-10-27, building the first generated project.* A copy keeps `tabindex="0"`, which React's types want as a number, `part="wrapper"` on a div, custom properties in a style, web components with no type. Converting each case is a list that never ends and changes nothing in the browser. The generated files start with `// @ts-nocheck` (said in their first line and in the README), and `npm run build` still checks everything else.

## A rewrite by a model is kept only if a number says it is better
*Rule, 2026-10-27 (`clone/picture.py`).* A region rewritten from its score can score lower. `refine` saves the code it replaces; the next `measure` compares the score over the regions the model wrote, and puts the old code back (and builds again) when it fell. The same for motion: it is measured when its intro is over, and removed if the page at rest lost more than 3 points. Check what a model did with a measure; do not ask the model whether it is better.

## A page with no doctype is drawn in quirks mode, and Next.js always writes one
*Found 2026-10-27, copying news.ycombinator.com.* The copy measured 78.7%: the phone width was fine in the markup (identical to the byte, apart from the addresses) and in the style rules, yet a row of the list was 60 px tall instead of 56, and everything after it 4 px lower, which the block-by-block structural comparison punishes hard. Hacker News has no `<!DOCTYPE>`, so the browser draws it in quirks mode, and Next.js writes `<!DOCTYPE html>` on every page. The capture records `document.compatMode`; a page in `BackCompat` is served from a document without a doctype (`public/clone/page.html`, with a `beforeFiles` rewrite of `/`). 100.0% after. When two pages with the same markup and rules measure differently, compare the rendering mode before the markup.

## A new check's label and description must be in the core's Italian catalog, and `check.py` does not run that test
*Found 2026-10-27, adding the steps of the exact copy.* `tests/test_i18n.py::test_text_translated_through_a_variable_is_in_the_catalog` fails when a registered `CheckSpec` (even one with `listed=False`) has a label or description that is not in `backend/src/agentfit/locales/it.py`, and the plan of `scripts/check.py` ("the tests that import what you changed") did not include it: it passed while the whole suite failed. After adding a check, a template or a workflow, run `python -m pytest -q` in `backend/` once, not only the selected files.

## A feature described in the UI, the docs and the release notes had no caller
*Found 2026-10-28, in `backend/src/agentfit/clone/picture.py`.* The skills card, `docs/CLONE.md`, `docs/DECISIONS.md` and the notes of v0.3.0 said that the model reads the skills with `read_file` and sees a line of each in its prompt. `prompt_catalog` and the `skills_prompt` that used it were written and tested, but nothing called them, and the steps of a build from a picture are single model calls with no `read_file`: the skills were copied into the project and no model ever saw one. A test of the function passes whatever its callers do. Before writing that a thing is used by a run, ask who calls it (`codegraph callers <name>`, then a text search for the attribute calls the graph misses); a function with no caller outside tests is not part of the feature. The rule for what a skill may be asked to do is in DECISIONS.md, "Skills are fetched at a pinned commit and a step is given only the sentences it needs".

## A test that failed only in CI: an unpinned plugin changed what a subprocess prints
*Found 2026-10-28, in the CI run of v0.3.1.* `test_a_test_report_says_what_the_suite_said_not_what_the_agent_concluded` passed here and failed on GitHub. CI installs the newest pytest-asyncio (1.4.1 that day, 1.4.0 here), which warns on stderr when a project sets no `asyncio_default_fixture_loop_scope`. The `run_tests` tool puts stderr after stdout, and the evidence of a test report quoted the last line of the whole output, the warning, instead of the suite's "1 passed". Running the test with `PYTHONWARNINGS=default` in `backend/` reproduces it here (the warning stays hidden otherwise; why CI shows it by default was not found). The evidence now comes from the suite's own lines (`checks.suite_closing_line`) and `pyproject.toml` sets the option. When a test fails only in CI, compare the versions of its "Install" step with `importlib.metadata` here before reading the test.


## A word found in a bundle is not a library
*Found 2026-10-28, reading the scripts of live sites (`clone/dna_signatures.json`).* The first signature for Rapier matched the text `rapier` and fired on `browserapierrors`; a bare word such as `paper`, `gsap` or `bodymovin` appears in minified code, in an id and in a string. A signature is a plain regular expression that needs a shape only the library has (a global it defines, a unique call, an import path), and each hit carries the label of the script that matched. A Webflow signature for Lottie (`Webflow.require("lottie")`) fired on the runtime's hook and not on any Lottie on the page: it bent the DNA to the hand measurement, so it was removed. The scroll-snap regex also matched `scroll-snap-type: none`, which is the opposite of snap. Check a signature against a page that does not use the library, not only against one that does.

## Adding a pack to `PACKS` changes what every picture build fetches and copies
*Found 2026-10-28, adding the design packs.* `install()` and `copy_into()` work on "the packs" when no id is given. Three more packs, 86 MB of download among them, would have been fetched by every build from a picture. The packs have a `purpose` and the old calls take only `picture`; the design ones are asked for by id. Before adding to a list that code walks, find what walks it.

## A name that already exists in a package is found by listing the folder before the plan is written
*Found 2026-10-28, planning the master prompts.* `clone/prompts.py` already held the prompt of the kit agent, and the plan had named its new module the same. The library is `clone/library.py`. `ls` the package first.

## A path past 260 characters fails on Windows, and the copy of a skill reaches it
*Found 2026-10-28 (`clone/skills.py`).* A skill folder copied to `<project>\.claude\skills\<name>\reference\...` can pass 260 characters in a deep project. The copy of that skill fails with an OSError; what was written of it is removed, the others go on, and the failure comes back in `failed`. Only simulated in a test, not reproduced on a real long path.

## The editor tool turns a typed unicode escape into the character
*Found 2026-10-28, writing the library loader.* A backslash-u escape for the byte order mark, typed in a source file through the editor tools, was written as the literal character, so the file stopped saying what it meant. Write `chr(0xFEFF)` in source and name the constant (`BYTE_ORDER_MARK`). The same hazard exists for any invisible character.

## A signature in the stage that reads scripts needs a time budget of its own
*Found 2026-10-28, `dna.mjs`.* A page with hundreds of scripts, or a hover probe that never settles, held the whole run until the 300 s kill and wrote nothing. Each stage has its own budget (scroll 25 s, scripts 60 s, a probe 10 s) and what it did not finish is a `limits` entry, not silence. A probe that failed is `null` plus `hover_probe_skipped`, never `{sampled: 0}`, which would read as "nothing changes on hover".


## A ComfyUI image input is a tensor link, not an uploaded filename
*Found 2026-10-30, verifying Wan first/last-frame video.* The upload API returns a filename,
but start_image/end_image on the conditioning node expect IMAGE tensors. Each filename must
pass through its own LoadImage node. Passing an uploaded name directly raises `'str' object
has no attribute 'movedim'`. A second filename on the request is not sufficient to implement
first/last-frame generation; check that both graph links reach actual LoadImage outputs.


## Two endpoint photos can have the same basename
*Found 2026-10-30, video uploads.* ComfyUI uploads use overwrite=true. A starting image and an
ending image from different folders can both be named image.png; reusing their basename makes
both workflow inputs point at the last upload. Video uploads get a fresh UUID plus a bounded
filename suffix, and removing the end picture clears the file input so the same file can be
selected again. Test with two different images carrying the same filename.


## 2026-10-30 — Interpolation needs endpoint lookahead and decoded verification

FFmpeg's minterpolate buffers future frames. Merely requesting 30 fps can shorten
an export or tempt padding the result with duplicated frames. Map the source's
first/last frames to the target's first/last timestamps, supply two guard frames
for lookahead, cap the emitted frame count, and measure the decoded MP4. A synthetic
moving source verified both 6 seconds / 180 distinct frames and 15 seconds / 450
distinct frames at actual 1920x1080 and 30 fps. Do not call this native Full HD or
infer distinct images from container metadata alone.


## 2026-10-30 — A long page must own scrolling inside the desktop shell

Shell's main pane uses overflow-hidden. Videos initially had an intrinsic-height
grid without its own scroll container, so the duration and Generate controls could
be clipped below the window; a wheel action had no effect. Give the page min-h-0,
flex-1 and overflow-y-auto, and check the real installed app at the owner's zoom.
A browser canvas large enough to show the whole form does not expose this problem.


## 2026-10-31 — Video saving needs the same native path as image saving

The Videos page had repeated the blob-download-link approach already documented as broken
in imageSave.ts for the desktop WebView. Reuse pickSavePath and an authenticated backend
copy route, and verify the resulting file on disk. A working browser download is insufficient.
Native file inputs also lose their obvious button appearance under the app's CSS reset;
use explicitly styled buttons and icons to open hidden inputs, with filenames and previews.


## 2026-10-31 — Browser frame counts are not generated frame counts

An eight-second 30 fps published clip contains 240 frames. Exporting 300 WebP samples
for scroll inspection necessarily repeats samples; diagnose repetitions in the original
decoded MP4 before resampling. Preloading 300 decoded Full HD pictures also costs
roughly 2.5 GB of pixel memory: cache compressed blobs and bound the decoded cache.


## 2026-10-31 — High-resolution BF16 image edits exceed the default image deadline

The live 2048-resolution Qwen 2.1 BF16 edit measured about 17 seconds per step.
Fifty steps plus model loading can exceed the default 900-second image timeout.
ImageStudio captures its deadline once in _wait: changing settings mid-job does not
extend that deadline. Set the longer timeout in the isolated QA backend before
submitting the edit; record any cancelled attempt and preserve the completed origin.
Do not change the owner settings or reduce sampling silently to make a test pass.


## 2026-10-31 — A valid Full HD file can still fail visual acceptance

Two UI-driven video tests produced exactly 240 distinct Full HD frames in eight seconds,
yet failed: portrait center-cropping removed the subject's head/feet, and a 720p FLF
model run at 512x288 deformed geometry with colored light shapes. Native ComfyUI output
confirmed the latter artifacts preceded FFmpeg. Inspect native and finished samples;
format, hashes and mean-luma screening alone do not certify quality. Preserve input
aspect ratio before Wan conditioning as well as during final scaling. Use the model's
intended native resolution and the official decoding path before blaming quantization
or changing guidance values.


## 2026-10-31 — CUDA headers in an isolated TRELLIS Conda environment

The NVIDIA CUDA 12.1 packages put cuda_runtime.h under
`targets/x86_64-linux/include`, while PyTorch's extension builder only added the
environment's top-level include directory. nvdiffrast therefore failed with
`fatal error: cuda_runtime.h: No such file or directory` despite nvcc being installed.
Set CPATH to that target include directory and LIBRARY_PATH to the runtime libraries
and CUDA driver stubs, both at install time and for worker-time compilation.
Keep NumPy below 2 for the chosen Kaolin/PyTorch combination; recent plyfile versions
require NumPy 2, so pin plyfile below 1.1.3 before installing utils3d.


## 2026-10-31 — Send the TRELLIS bootstrap through WSL stdin

A multiline Python program passed as an argument through `wsl -- bash -c` returned
without executing the worker on this Windows machine. Use `wsl --exec bash -s` and
send the fixed bootstrap over stdin. Encode the worker source as base64; pass the
reference and parameters as a JSON heredoc with a fixed delimiter. This avoids
rewriting Python source through the Windows/default-shell quoting layers. Probe
runtime files through stdin as well, and include the raw worker source explicitly
in PyInstaller data: collect-data skips Python source files.


## 2026-10-31 — Test GLB textures under the packaged WebView policy

The browser preview loaded a textured GLB correctly, but the installed WebView
showed a white silhouette. Native WebView2 diagnostics reproduced a connect-src
violation for a blob URL: GLTFLoader creates a local blob for an embedded image
and ImageBitmapLoader fetches it. Allow blob: in connect-src and img-src while
keeping external resource URLs rejected by the viewer. GLTFLoader can swallow
texture errors and return an untextured mesh, so use LoadingManager.onError to
show a failure and avoid displaying that fallback as a completed preview. Verify
the actual installed app, not only a browser development server.

## 2026-11-01 — TRELLIS.2 full resolution and compatible public conditioning

The official 1536 cascade may silently lower its resolution when its default
49,152-token limit is reached. Setting the pipeline name alone does not prove a
1536³ result. The worker allows the full 96³ latent grid and checks the mesh's
actual voxel size before accepting it; memory failure must stay visible.

The stock pipeline points at gated DINOv3 and BRIA auxiliary models. Public timm
DINOv3 ViT-L/16 weights load strictly and produce the expected 1029/4101-token
features at image sizes 512/1024. For matching conditioning, disable timm's learned
final norm and use upstream's unweighted functional layer norm. Local U2Net
preprocessing avoids loading the gated background remover.

Installing utils3d separately can upgrade plyfile and NumPy despite a previous
numpy<2 install. Keep plyfile<1.1.3 in the base dependency request too; verified
runtime versions are NumPy 1.26.4 and plyfile 1.1.2. NVIDIA package downloads can
be slow from one host; identical files from official indexes remain verifiable
by SHA-256, and resuming verified byte ranges sped up this one-time setup.

## 2026-11-01 — TRELLIS memory and reliable high-to-low baking

CPU offloading alone does not bound decoder activation memory. LayerNorm32
casts the whole tensor to FP32; chunked normalization matches full normalization
on the GPU but does not guarantee a full 1536³ decoder fits. Test the actual
reference and report explicit resolution; samurai04 required 1024³ on 16 GB.

A glTF imported mesh duplicates vertices at seams. Direct decimation and inherited
UVs can leave tiny islands and almost empty baked maps, while welding alone can
leave non-manifold geometry that stops collapse above the requested budget.
A separate voxel-remeshed low mesh, fresh render UVs and selected-to-active
normal/PBR baking produced a valid 28k result. Verify actual GLB index counts,
embedded maps and rendered appearance; file size alone is insufficient.

Disabling a runtime preference does not clear MTP from an externally loaded
Qwen instance or saved preset. The dedicated coder reloads just its own model
on explicit generation and requires the verified --no-speculative-draft-mtp
CLI flag. The integration tests use fakes, never the owner's real model.
# Qwen scene output can end mid-statement

An HTML title, canvas and controls do not prove the generated JavaScript is complete.
The owner's Cloud Sanctuary stopped at `box(0.78` when output was exhausted, while
the app incorrectly reported success. Check document/script termination, budget
for thinking and code, continue interrupted output with a bounded retry count and
surface preview runtime failures. Preserve the source when recovering a scene.

## 2026-11-01 — Keep test temporary files outside generated web projects

Do not point TEMP/TMP at a generated site's `.cache` directory. Node inherits an
ancestor `package.json` with `type: module`, breaking CommonJS test fixtures, and
document evidence readers intentionally skip hidden/cache directories. This caused
nine unrelated suite failures while testing the Qwen hero. Use a short dedicated
normal directory outside web projects on a drive with at least the app's 10 GB
free-space reserve. The system temporary folder on a nearly full C: drive caused
different failures: Windows path-length limits in nested skill fixtures and disk
reserve checks in mocked image/model downloads.

A completed Qwen generation also needs geometry validation: the winter pagoda
returned `finish_reason: stop` but had empty roof buffers, incorrect instance data
and missing window dimensions. Preserve the original output, validate positions
and indices, inspect the rendered model, and record manual repairs separately.


## 2026-11-02 — Closed HTML can still contain broken JavaScript

The saved Qwen scene for “crea un anime 3d” closed every HTML tag but declared
`const localX` twice in the same block. Node `--check` and the browser both
reported the duplicate declaration; no scene code could run. Generation now
checks inline JavaScript with local Node when available, without executing it,
and feeds syntax errors back into a complete rewrite. Browser runtime errors
can request a repaired copy while preserving the original. A redundant Three.js
CDN script is removed from standalone previews/exports because the local core
is already embedded; the restrictive content policy remains in force.

## 2026-11-02 — Full checks need a suitable temporary drive and Git boundary

Mocked image downloads and TRELLIS preparation failed because C: was below the
10 GB free-space reserve. Moving TEMP/TMP into .shots fixed space but broke
document evidence tests, which skip hidden directories. A normal temporary folder
inside this checkout then made a non-repository test discover its parent repository.
Use a visible dedicated temporary directory on a drive with enough space. If it
is inside a checkout, set GIT_CEILING_DIRECTORIES to that temporary root for the
test process. Do not weaken disk checks or evidence filtering. The complete suite
passed with these process-local settings.

## 2026-11-02 — Verify installed layout and the NSIS bundle marker

A browser check at 1440 px missed the installed app's interface zoom. Settings
still stacked until the three-column breakpoint was lowered. Verify the actual
installed WebView at the owner's settings.

Tauri's installed executable can differ from target/release by its bundle marker:
the verified build differed only at NSS versus UNK. Do not mistake that expected
patch for an old installation or accept arbitrary hash mismatches. Compare actual
differences and the separately frozen core, then launch the installed app.
