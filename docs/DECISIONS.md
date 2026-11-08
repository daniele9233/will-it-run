# Decisions

## 2026-11-08 - Remove Voice and Qwen 3D Coder; observe video progress

The owner withdrew Voice and the Qwen 3D Coder panel. Remove their active UI,
routes, services and build wiring, preserving TRELLIS.2, Hi3D, the comparison
lab, the 3D gallery and all other model families. Earlier Voice/Qwen entries
in this document describe superseded decisions. Local model cleanup is limited
to the dedicated Voice folder and must be reported separately from code removal.
The updater must never run prepare-voice or redownload the removed models.

Video progress uses observed ComfyUI sampling events filtered to the submitted
prompt, with monotonic stage weights and the same progress element as 3D.
Socket failure is optional telemetry loss, not generation failure. Display an
unknown remaining duration instead of inventing an ETA; reserve 100% for a
validated, persisted result.

The choices that shaped Will It Run, with the reason for each. One `##` section is one decision. Read this before reopening one of them: the reason is usually the thing that is easy to forget.

Every section is also copied into the project memory (`willitrun memory import-docs`, run by the git hooks), so `willitrun memory recall "<topic>"` finds it by words. The longer arguments stay in the document an entry points to.

To add a decision: a new `##` section at the end, with the date, what was decided, why, and where it shows in the code. To change one: edit its section and say what replaced it and when. Do not delete the old reason.

## Tauri and React for the interface, not a native Windows toolkit
*From the first version. Detail: ARCHITECTURE.md.*
The interface is dense and custom (model rows, a node editor, a live activity feed), which web tooling does well, and Tauri packages it with the system WebView2 in a small installer. The Rust side stays tiny on purpose, about 150 lines that manage a child process (`desktop/src-tauri`). Do not move logic into Rust.

## A Python core, not a Rust one
*From the first version. Detail: ARCHITECTURE.md.*
LangGraph is a Python library and the workflow engine is the centre of the product. Hardware detection and scoring live in the same process, so there is one language, one test suite and no IPC schema between "analysis" and "orchestration". The cost is a bundled interpreter (about 40 MB frozen) and a slower start, accepted. Code: `backend/src/agentfit`.

## HTTP between the interface and the core, not Tauri IPC
*From the first version. Detail: ARCHITECTURE.md.*
The same API serves the desktop shell, a browser during UI development and the test suite (`TestClient`). Live updates use one Server-Sent Events stream. The core listens on 127.0.0.1 with a bearer token chosen at each launch (SECURITY.md, "The local API"). Every new endpoint is written as `@app.get("/api/...")` in `backend/src/agentfit/api/`, because the generated map and a test read routes in that form.

## SQLite, one file, numbered migrations
*From the first version. Detail: ARCHITECTURE.md.*
One file in `%LOCALAPPDATA%\Will It Run`, no server. Schema changes are numbered SQL files in `backend/src/agentfit/migrations/` (`0001_initial.sql` ... `0006_image_components.sql`), recorded in `schema_migrations`. A migration that was committed is never edited: add the next number. LangGraph checkpoints use a second SQLite file.

## llmfit is adapted and reused as data, never wrapped
*Commit studied: 1a4e9a0. Detail: ARCHITECTURE.md, table "llmfit: reused, adapted, wrapped or reimplemented".*
The quantization tables, the memory and KV-cache formulas, the bandwidth model of speed and the GPU tables were adapted to Python (`fit/`, `hardware/gpu_tables.py`). The model database is reused as an offline seed (`scripts/build_seed_catalog.py`). Hardware detection was rewritten for Windows only. The `llmfit-core` crate is not linked: it would add a second engine and an IPC schema for a few hundred lines of arithmetic.

## LM Studio is the model runtime and is not bundled
*From the first version. Detail: LM_STUDIO.md.*
Will It Run talks to LM Studio's REST API and its `lms` CLI. The model list is merged from both because neither knows everything: the CLI knows each model's file path (which maps it back to its Hugging Face repository), the REST API knows capabilities and what is loaded. Either one alone still works. Use only documented LM Studio and LangGraph behaviour, and list new usage in LM_STUDIO.md or LANGGRAPH.md.

## The Project Brain is built into the app
*2026-10-13. Detail: MEMORY.md, "Why it is built in, and what it borrows".*
The permanent memory of a project is tables in the SQLite file the app already has, an FTS5 index, and vectors from the runtime the app already uses. No memory library was added: mem0 calls a language model twice per write and needs a graph database for its graph, claude-mem is a plugin for other tools, LightRAG and GraphRAG index documents rather than what agents do, Graphiti needs a graph database. Adopted ideas: typed entries captured from tool calls (claude-mem), one store searched by vectors and graph together with a new fact replacing the old (mem0), what was replaced is kept and marked (Graphiti). Code: `backend/src/agentfit/brain`.

## Image generation is not a ModelRuntime, and ComfyUI is the engine
*2026-10. Detail: IMAGE_STUDIO.md, ARCHITECTURE.md ("Runtime abstraction").*
A diffusion model is not loaded, given a conversation and unloaded: it is a graph of nodes that runs once and writes a file, and its "model" is several files whose names must match. So `agentfit/imagegen` is a job runner of its own against ComfyUI's HTTP API. ComfyUI is GPL-3.0 and is not bundled or installed by the app: embedding a diffusion runtime would mean shipping gigabytes of wheels, CUDA kernels and model licences in an app whose job is to measure what a PC can run. Rule for the whole feature: nothing is claimed as installed unless the file is on disk where ComfyUI looks for it, and nothing is queued unless every node class of the graph exists.

## The Images catalog is photographic, with one exception
*2026-10-27. Detail: IMAGE_STUDIO.md.*
The person who owns the project wants photoreal pictures (including uncensored ones of adults) and said cartoon models do not interest them. NoobAI, Illustrious and the Kroma builds were removed. The one exception, asked for by name, is Pony Diffusion V6 XL, which draws anime by default: its entry starts from a photographic negative prompt (`source_anime, source_cartoon, 3d, drawing`), and on this PC it only made a photograph when prompted with photographic tags, not with a descriptive sentence. Do not add anime or illustration models back without being asked.

## Every model comes from a free source, Hugging Face first
*2026-10-27. Detail: IMAGE_STUDIO.md.*
The owner wants everything free and does not want to use Civitai. Civitai answers 401 to a download without a personal API token, so its entries stay in the catalog flagged `needs_token`, are refused before queueing, and are never pushed on the person. New models are added from Hugging Face files that need no token. A source is verified (the URL answers, the size and checksum match) before it is written into `data/components.json`.

## The Arena has no judge model: code checks the answers
*Detail: ARENA.md.*
Each question has a verifier written as code, a reference answer and wrong answers that must fail. What no code can judge is never counted as right or wrong: it is shown as **not evaluated**, and a person's mark on it never enters a score. Every model gets the same messages byte for byte and the same sampling. Every score has a 95% interval from a bootstrap with a fixed seed.

## The Arena suite is versioned and pinned by hash
*Detail: ARENA.md, "Reproducible" and "In the code" (the last lines say how to add a question).*
`SUITE_VERSION` (`backend/src/agentfit/arena/__init__.py`) changes whenever a question, a verifier or the way an answer is read changes, because results of two versions are not comparable. The canonical seed (42) is pinned by hash in `tests/test_arena_bank.py`: change a question and the test stops you until you also bump the version and pin the new hash it prints. A run stores its own questions, so it can be read after the bank changes.

## Interface text: English is the key, Italian lives in a catalog
*Detail: LOCALIZATION.md.*
Both sides use the English text as the key, gettext style: `t("Explore Models")` in the interface and `tr("...")` in the core. The Italian is in `desktop/src/locales/it.txt` (which generates `it.ts`, never edited by hand) and `backend/src/agentfit/locales/it.py`. A string without an Italian translation, or a translation that drops a placeholder, fails the suite. Text sent to models and logs are never translated. The Arena stores the reason of a verdict as an English template and translates it when it is read.

## No fabrication, and every automatic choice explains itself
*Detail: DEVELOPMENT.md, "Conventions".*
A value that is not known is `None` in Python and "Unknown" or "Not measured" in the interface; no default may look like data. Anything Will It Run decides for the person returns its reasons (`reasons`, `why`, `quant_reasoning`, `explanations`).

## The internal name stays agentfit
*Detail: DEVELOPMENT.md, "Names that did not change".*
The project started as AgentFit. The Python package `agentfit`, the environment variables (`AGENTFIT_HOME`, `AGENTFIT_TOKEN`, `AGENTFIT_PARENT_PID`), the database file `agentfit.sqlite3` and the Cargo package keep that name on purpose; nothing a user sees depends on them. The `agentfit` command is an alias of `willitrun`.

## Context for coding agents: a map made from the code, a code graph, a memory made from facts
*2026-10-27. Detail: AGENT_CONTEXT.md.*
The project is large (about 40,000 lines of Python, 20,000 of TypeScript, 133 routes, 23 tables) and models change it in sessions that start empty. Chosen: `AGENTS.md` (rules, short) plus `docs/map/` generated from the code by `scripts/build_context.py` (never written by a model, so never wrong in its own way); CodeGraph (MIT, local, one MCP tool, updates itself) for symbols, callers and blast radius; and the Project Brain as the memory, fed without a model by a transcript ledger, by git hooks and by `docs/DECISIONS.md` and `docs/LESSONS.md`, and served over MCP and the command line. Everything is free, local and runs by itself. Turned down, with the reasons: GitNexus (non-commercial licence, 19 tools, too heavy for a local model with an 8k window), Serena (about 29 tools), codebase-memory-mcp (17 tools, a binary with documented Defender false positives), claude-mem (hosted observer), Graphiti (a graph database), Graphify and CodeGraph's hosted platforms (accounts, paid tiers). Evidence is mixed: vendors report large savings, an independent benchmark found none below about 25,000 nodes, and this repo has about 3,000 symbols. CodeGraph therefore stays only while it helps (`codegraph uninstall` removes it); the map and the memory do not depend on it. First measurement, the same day: on four read-only tasks fresh Haiku agents with and without all of this were equally right, equally fast and equally cheap, and the graph's caller list was incomplete (AGENT_CONTEXT.md, "Measured"). So the benefit is unproven here; it was not tested on edits, on a small local model or across sessions.

## Architecture contracts record today's layering
*2026-10-27. Detail: `backend/.importlinter`.*
`lint-imports` (import-linter) checks eight contracts computed from the real import graph: the entry stack is cli over api over doctor over app; engines and libraries never import the entry points; paths, config, database, events, translations and branding import no feature; hardware and fit know nothing of models or workflows; the Brain depends on the database and the log only; the image studio is independent of the Arena, workflows, teams, cloning and the LLM runtime; the Arena, benchmark and chat do not import one another; the Arena imports no other feature directly. Two cycles exist and are accepted: `teams` and `workflows`, `lmstudio` and `runtime`. Do not add a third. To loosen a contract, change the file and add the reason here.

## Hooks help, CI decides, generated files are never edited
*2026-10-27. Detail: AGENT_CONTEXT.md.*
The git hooks (`.githooks/`, turned on by `core.hooksPath`) and the Claude Code hooks (`.claude/settings.json`) save work and never block it: a failure inside them is skipped. The gate is CI (`.github/workflows/ci.yml`: ruff, import contracts, pytest, the Italian catalog, the map with `--check`, then tsc, eslint and vitest). `docs/map/` and `desktop/src/locales/it.ts` are generated; edit their sources and run the generator (a Claude Code hook refuses to edit them by hand).

## Commit and push only when asked, and "main" means master
*2026-10-27. Standing instruction from the owner.*
Nothing is committed or pushed unless the person asks. The repository's default branch is `master`; "push to main" means push `master`. `.shots/` (development screenshots, which can show generated pictures) is never committed: the GitHub repository is public.

## A clone is the page as the browser rendered it, not a model's description of it
*2026-10-27. Detail: CLONE.md.*
The first version had a model follow the skill of ai-site-cloner. On a local 27B model (about 27K tokens of context, 12K of them the skill) a run of 2 h 39 min on brioni.com made 776 tool calls and built no component; guards around the loop did not change that, because the skill assumes a model that keeps a whole site in its head. The default is now an exact copy: Chrome renders the page, `capture.mjs` freezes what it rendered (markup, style rules, pictures, fonts, shadow DOM flattened, a phone variant of each section), `replica.py` writes a Next.js project and `measure.mjs` compares it with the original's screenshots. Accuracy on brioni.com, measured on the built project: 99.9%, in 65 seconds, with no model for the copy. The agent mode stays for the models it works with. Code: `backend/src/agentfit/clone/pipeline.py`.

## A local model does small jobs that a number can check
*2026-10-27. Detail: CLONE.md, "From a picture".*
A model of 27B on a 16 GB card has about 27K tokens, and a long tool loop spends them on re-reading. So a model is used as a function: one bounded question (name these sections, write this region from its crop, rewrite it from its score), an answer that code validates, and a measure that decides whether the answer stays. Places and sizes come from data, not from the model; a rewrite that does not raise the score is undone; motion that moves the page at rest is removed, and the motion is a fixed GSAP component that only takes numbers (the model chose the GSAP code once and the measure threw it out). The photographs of a picture are its own crops, and the report says which share of the page that is, so the accuracy cannot be raised by copying. A step that is code (capture, build, measure) never asks a model. Code: `clone/picture.py`.

## Skills are fetched at a pinned commit and a step is given only the sentences it needs
*2026-10-27, changed 2026-10-28. Detail: CLONE.md, "Skills".*
GSAP's, Anthropic's frontend-design, Vercel's, Three.js and Playwright's skills are installed the way the clone kit is: `git fetch` of one commit, checked against its hash, never bundled, kept with their licences in `tools/skills/<owner>__<repo>@<commit>`, once per PC. A run copies the ones it wants into its project (`.skills/`). A step of a run is given a few sentences of a skill, picked by a phrase (`SkillStore.excerpt`, `picture.GUIDANCE`), only where they bear on the choice the step asks of the model: today the motion, which gets the design skill's paragraph on motion and the GSAP skill's note on the strength of the eases. Composition and refinement get none, because the design skill tells a model to take aesthetic risks and what they write is a copy. The first version said the model reads the skills with `read_file` and sees a line of each in its prompt; no step has that tool and nothing called the function that wrote the line, so no model was shown a skill. The Playwright CLI is not installed as a global npm package. Code: `clone/skills.py`, `clone/picture.py`.

## Master prompts: the library is curated and the composer is code
*2026-10-28. Detail: CLONE.md, "Master prompts".*
The prompts are Markdown files written by hand and checked at load (`clone/library.py`); which of them a page needs is decided by a table of rules in code (`clone/compose.py`), and every recipe it picks comes with its reason. No model writes or chooses a prompt, so the same DNA gives the same prompt and the reason can be read. The composer states the measured values once, in one "Site DNA" block, and a recipe's placeholder points at its line, because the same nine numbers twice would spend the 12,000-token budget on repetition. The titles and summaries carry their Italian in the file (`title_it`, `summary_it`) so the interface needs no second catalog for the library. Five pilot prompts ship; forty-five are planned, and a prompt is "Not tested yet" until a real builder has used it (`proofs.json`).

## A site's DNA is collected by a script of its own and read in Python, in a study that uses no model
*2026-10-28. Detail: CLONE.md, "Site DNA".*
`dna.mjs` only collects: counts, histograms and short lists from a live page, `null` plus a reason where it could not measure. Every interpretation (roles, palette, which library, what is recommended) is Python (`dna.py`) that tests can feed with a saved JSON, with no browser. `dna.mjs` repeats the launch code of `capture.mjs` on purpose: two scripts that share nothing but habits cannot break each other. A study is a model-less run of the workflows engine (the validator accepts a workflow with checks and no agent). An unknown value is never replaced by one that looks like data.

## Third-party skill scripts are never copied, and design packs are separate from picture packs
*2026-10-28. Detail: CLONE.md, "Skills".*
The three design packs (taste-skill, MotionSites, impeccable) keep text and data only: `scripts/`, `assets/`, `examples/` and `media/` are dropped by the copy (`SkillPack.exclude`), because a script from the internet should not land in a person's project to be run by an agent, and MotionSites' examples and media are not under its licence. The packs have a `purpose`: `picture` for what a build from a picture fetches, `design` for what the master prompts cite and are asked for by id, so that adding a pack to `PACKS` does not change what every picture build downloads. `git fetch` takes the whole commit, so the notices say "not kept", not "not fetched" (impeccable: 86 MB downloaded, 0.46 MB kept). The texts of the skills still mention the scripts; the prompts never tell a builder to run them. Impeccable's `reference/ios.md` and `android.md` come from ehmo/platform-design-skills (MIT) and are credited in the notices and in the marker.

## A skill folder is ours only if it carries our marker, and its name is the folder's, not the header's
*2026-10-28. Detail: `clone/skills.py`, `install_into`.*
`.claude/skills/<name>` may hold the person's own skill. The folders this program writes carry a marker file (`.willitrun-skill`), written first and removed last; a folder without it is never overwritten or deleted. The name a skill answers to is its folder's (taste-skill's header says `design-taste-frontend`, its folder `taste-skill`), so the copy is placed by folder name and a header that is not a safe folder name is refused. `install_into` does not raise on one bad skill: it returns `written`, `skipped`, and `failed` (the key and the reason), and the others go on.


## 2026-10-30 — Video studio shares the Images engine

Videos lives directly below Images, with its own Wan 2.2 I2V workflow and MP4 gallery.
It reuses the ComfyUI process, client, authenticated image upload and event bus; it never starts
another runtime or substitutes the user's Q6_K experts with the existing Q4 models. A separate
launch-only paths file registers D:\Models for diffusion models, leaving ComfyUI's own YAML intact.
On this PC, two same-volume hard links in ComfyUI's diffusion_models also expose the originals to
the already-running engine, without copying 24 GB or interrupting it.

Requests preserve the seed and all sampling settings. HighNoise hands its unfinished latent to
LowNoise with noise disabled. Tiled VAE decoding limits decode memory. The gallery stores an MP4
and an atomically published JSON recipe per video under app data, avoiding an unnecessary database
schema change. Cancellation deletes only its own queued prompt and interrupts only when that prompt
is running. No timing or hardware-fit guarantee is inferred from file size.


## 2026-10-30 — First/last-frame video has four explicit local profiles

Keep the original Wan 2.2 I2V A14B Q6_K pair and add Fun-InP 5B Q6_K, Fun-InP A14B Q4_K_M,
and Wan 2.1 FLF2V 14B Q4_K_M. The selected quants leave the drive's 30 GiB reserve intact and
avoid assuming both 14B experts can remain in 16 GB VRAM. No profile substitutes another file.
The model name, sampling settings and both input image names are preserved in each recipe.

The catalog describes roles, conditioning and defaults rather than copying one graph to every
family: Fun 5B uses its own Wan 2.2 VAE and one sampler; both A14B profiles use two experts;
Wan 2.1 FLF2V uses a single model plus CLIP Vision H for each endpoint. Only Wan 2.1 requires
the ending picture; the others retain a single-picture mode. Tiled decoding is shared.
Source commits, sizes and SHA-256 are pinned in video_assets.json. Dependencies remain in
role-specific directories, avoiding a flat-folder mapping to every ComfyUI model role.


## 2026-10-30 — Separate native Wan generation from verified hero export

The owner requires the same 1920x1080 / 30 distinct fps / 6-15 seconds from all four
video profiles. Generate the whole selected interval at each model's native rate;
keep native resolution adjustable for memory, then use the already-installed local
FFmpeg for motion-compensated interpolation and Lanczos resizing. No extra model,
cloud, account or neural-upscaling claim is necessary. Describe upscaled output in
the UI and preserve native versus final specifications in separate recipe fields.
Decode and measure the result before atomic gallery publication. Reject repeated
adjacent frames rather than claiming frame-rate metadata proves distinct motion.
Legacy raw recipes retain their meaning; the new UI always requests hero export.


## 2026-10-31 — Save video prompt pairs independently of generated videos

A reusable effect needs its positive and negative texts kept together. Store named pairs
in a dedicated videos/prompts directory under app data, with atomic JSON publication,
independent of gallery recipes and without a new database migration. Gallery deletion
must not remove saved pairs. Reuse fills the two text fields only, preserving current
photos and sampling choices rather than reusing stale uploaded filenames or implying a
prompt alone reproduces an identical video. Both texts stay selectable in the desktop
shell, whose global CSS otherwise disables selection; reuse the existing clipboard helper.


## 2026-10-31 — Diagnose published video separately from native generation quality

The local QA harness in scripts/video_qa uses the existing authenticated studio APIs,
sequential jobs and ignored scratch outputs. It records requested native settings and
measured Full HD output separately: interpolation and scaling do not establish native
1080p quality. Endpoint SSIM, temporal screening and optical-flow track survival are
measurements with explicit heuristic thresholds; they never certify rigid 3D geometry
or fabricate a Gaussian reconstruction. Models requiring two images remain unsupported
in a true single-image case. Qwen BF16 refers to the diffusor; its installed encoder
remains INT8. A repeated First/Last case supplies the requested fourth stress scenario.


## 2026-10-31 — Preserve entire video references and enforce 720p FLF resolution

Video inputs use core ComfyUI resize-and-pad before conditioning; portrait generation
follows source orientation and Full HD export adds borders rather than cropping.
The 720p-only FLF profile cannot silently run a tiny latent for a Full HD request.
Legacy raw recipes remain valid, while new Full HD requests enforce the native
minimum. Decode with the official VAEDecode path instead of forcing very short
temporal tiles. These are pipeline correctness fixes, not a promise of AI-generated
rigid 3D motion or a change to the explicitly selected model quantization.


## 2026-10-31 — Expose short native transitions as explicit slow motion

Keep export duration separate from native motion length. A visible Slow motion
control supplies optional motion_frames; do not silently claim 33 frames at 16 fps
were generated as eight seconds. Store native interval, requested export duration
and timing mode. Existing API requests without motion_frames keep continuous
generation. The live 720p FLF comparison used the official 33-frame recipe, with a
verified eight-second/240-frame export, and separated the object throughout the shot
instead of postponing almost all separation to the end of a long native clip.


## 2026-10-31 — TRELLIS lives in its own WSL2 runtime

The 3D page uses Microsoft TRELLIS-image-large through an isolated Python 3.10,
PyTorch 2.4 / CUDA 12.1 environment in a user-selected existing WSL distribution.
The core and ComfyUI never import its native CUDA extensions. Setup is explicit,
free and local; the official TRELLIS revision is pinned in trellis_setup.sh.
Images travel as bounded PNG payloads over stdin; the worker returns a GLB which
is validated before the core saves its recipe and exposes it in the gallery.
Video references supply one frame, not an animated reconstruction. Model weights,
DINOv2 and background removal assets may download on first use. The upstream
minimum is an NVIDIA GPU with 16 GB VRAM; this is not a guarantee that other loaded
models leave sufficient free memory. Tests use fake subprocess results.


## 2026-10-31 — Explicit 3D photo-to-export flow and high detail

The 3D page exposes Upload photo, Generate 3D model and Export GLB as three visible
steps. High detail requests 24 sampler steps, a 2048-pixel texture and 30 percent
triangle removal; Standard keeps the original 12-step, 1024-pixel, 95-percent-removal
preview recipe. Legacy API requests default to Standard. Store quality in each
recipe and release the generator weights before textured-mesh export to leave GPU
space for larger outputs. The real high-detail samurai run on the 16 GB RTX 4080
SUPER produced 132,344 triangles and 81,376 exported vertices in a 6,477,680-byte
GLB in 78.5 seconds with weights cached. This improves the local output and does
not establish equivalence with the separate proprietary Tripo Studio engine.

## 2026-11-01 — Replace TRELLIS v1 with TRELLIS.2 at explicit 1536³

The owner requested removing the old engine and installing TRELLIS.2 at 1536³.
Keep the gallery and authenticated upload/export API, replace the isolated runtime
with `willitrun-trellis2` and use only the 1536 cascade/4K PBR export profile.
CPU offloading changes model placement, not the requested spatial resolution.
Upstream can silently reduce resolution when its token limit is reached; permit
the full 1536 sparse grid and verify voxel size before accepting output instead.
On the owner's 16 GB GPU, show the official 24 GB requirement and report failure
honestly if insufficient memory prevents generation.
Pin all inference assets with size and SHA-256. Use public timm DINOv3 ViT-L/16
weights and local U2Net preprocessing to avoid requiring accounts for gated
auxiliary models. Do not delete pre-existing generated models during migration.

## 2026-11-01 — Explicit memory profiles and separate Web assets

The new action-pose samurai fails at 1536³ on 16 GB but succeeds at 1024³.
Default to explicit 1024³ while retaining the experimental 1536³ option; never
silently reduce a selected resolution. Keep original GLB bytes and build Web
copies separately with a 25–30k triangle budget, real high-to-low Normal/PBR
bake and Draco/WebP. Deletions are recoverable in a local trash directory.
Share one 3D task slot across generation, Web bake and Qwen code generation.
Qwen is a procedural scene coder: do not present it as image reconstruction.
Render its generated HTML in an opaque sandbox on a separate narrow server,
without application credentials or network access; bundle Three.js offline.
The user expressly excluded running the actual Qwen model during integration.

## 2026-11-01 — Confronto Web and explicit quality maxima

The owner chose the comparison layout and prioritizes detail over generation cost.
Give the viewer the full page width, collapse generation controls and display the
original beside its true Web copy. Use one renderer with scissored views, a shared
camera, identical lighting and the original bounds; independent auto-framing can
hide geometric differences. Resizing and expanded mode preserve the current view.
Provide drag, slider and Escape/focus behavior for resizing/expanded viewing.
Default UI controls to explicit supported maxima without changing API defaults or
claiming that a high seed/step count proves quality. Preserve the original export.
Research the author's coder constraints and adapt prompts to the existing offline
global THREE runtime; never run the owner's Qwen model to test these prompts.
# Studio and recoverable local scene generation

The 3D page uses a single Studio viewport with original/Web selection instead of
side-by-side comparison. Both exports remain available. Qwen continuation and
recovery share the existing 3D job slot and model gateway; recovered scenes get a
new identifier with a parent link. Preview errors are sent from the opaque iframe
using a narrowly checked source window; its network restrictions remain intact.

## 2026-11-02 — Keep the 3D gallery beside the Studio at desktop zoom

Use left gallery/export controls, center viewport and right generation controls,
starting three columns at lg rather than xl. The installed WebView's interface
zoom reduced its CSS viewport enough to stack settings at xl despite a 1440 px
physical window. Preserve adjustable height, with a 400 px initial value. Render
visible model thumbnails sequentially, cache their images and release temporary
WebGL contexts. Keep original GLB and separate Web export behavior unchanged.

## 2026-11-02 — Repair Qwen errors into new copies

Document termination alone is insufficient. Use local Node's parser when available,
never execution, before accepting newly generated inline JavaScript. Feed syntax
errors into bounded full rewrites; expose preview runtime errors as Repair scene.
Preserve original scenes and parent links. Scene deletion is explicit and scoped
to owned files, leaving external exports intact. The shared task slot, MTP-off
policy and opaque offline preview remain the boundaries for generated code.

## 2026-11-02 — Model research is separate from installed capabilities

The owner canceled Hunyuan3D 2.1 integration. No Hunyuan3D engine or alternative
Three.js coder was added, downloaded or enabled. Atelier, OBSIDIAN, larger Qwen
models and alternative video generators were research candidates, not measured
improvements or shipped features. The existing 4B coder remains integrated.
Do not turn recommendations into inventory.

## 2026-11-02 — Consolidate local studios and Dynamic mirror in 0.4.0

Release the accumulated local work together: TRELLIS.2 and Web exports, the Qwen
scene coder and Studio layout, four Wan video profiles with saved prompts and
explicit finishing/slow motion, and Dynamic mirror's browser recording/replay and
measured reports. Preserve separate original/Web assets, model-specific workflows
and honest quality/coverage limits. Video QA tools operate on synthetic fixtures
or explicitly requested local runs; their media and reports remain ignored.
The detailed product changes are in RELEASE_NOTES_0.4.0.md, 3D_STUDIO.md,
VIDEO_STUDIO.md and CLONE.md. Browser recordings under .playwright-cli are private
development outputs and are excluded alongside .shots. The owner authorized
committing all source changes, publishing master and creating the new version tag.

## 2026-11-03 — Optional Hi3D tests in the 3D studio

The owner explicitly requested Hi3D inside the installed application to run their
own tests, without introducing scene decomposition. Keep TRELLIS.2 generation as
it is and add an optional selected-model refinement action. Reuse the isolated
Hi3D runtime and verified pinned public assets, the existing shared 3D task slot,
authenticated routes and gallery. Each refinement stores a new GLB and recipe
with source/reference hashes, requested settings and actual geometry counts.
The original and its reference remain intact. Hi3D is geometry only; do not claim
textures, UVs or a PBR Web export. Compare the source and result in neutral gray
with a shared camera and documented uniform size normalization. Let the owner
select 512, 1024 or 1536 explicitly, never silently reduce resolution, and retain
the experimental-memory note for 1536 on 16 GB. No cloud, account or paid service.

## 2026-11-03 — Google Flow with the owner's existing Pro credits

The owner explicitly selected Google Flow as the only external video generator.
Keep Google authentication and credit spending in its normal browser interface.
Will It Run prepares first/last reference copies and the prompt, opens Flow, then
imports a downloaded MP4 and extracts 150 WebP frames locally with FFmpeg. This
is a browser-assisted handoff, not a Google API or an autonomous MCP connection.
Never store Google passwords, cookies or access tokens, infer a credit balance,
or claim an imported video's model has been independently authenticated.
Validate actual streams, duration and frame count; publish the sequence ZIP only
after complete extraction. Keep local Wan generation available separately.

The three hero experiences combine real interactive GLBs with scroll-scrubbed
Flow footage, with provenance kept alongside the project. A video adds character
motion but does not add a skeleton or animation clips to the original models.

## 2026-11-03 — Preserve source appearance on Hi3D geometry

Hi3D itself supplies geometry only. When the source contains color, transfer
approximate per-vertex RGBA from nearest source vertices after bounding-box
centering and uniform scaling. This adds appearance without UV unwrapping on the
refined million-face output. Keep the source intact; record source_vertex_colors
in the recipe and label the result as approximate, without PBR textures or UVs.
Do not offer texture-based Web baking for this representation. A source without
appearance remains explicitly geometry-only. Existing geometry-only results are
preserved when creating colored recovery copies.


## 2026-11-07 - Local 3D comparison lab and Voice

Keep TRELLIS.2, Hi3D and 3D Coder unchanged. Add isolated pinned runtimes for
Pixal3D, SUPRA-Tex and Gaussian generators; keep GLB and Gaussian PLY results
separate. Idol uses public repacked weights and compatible TripoSplat code,
explicitly experimental. Companion applications keep their own interfaces and
export/import handoff. Scenario instructions are not a generative model.

Voice uses VoxCPM2 and dots.tts SOAR for Italian, Apache-2.0 licensing and a
16 GB GPU. This is a hardware/license choice, not a measured quality ranking.
No ML dependencies enter the core. Voice is an independent engine in import
contracts; the API injects the shared 3D task slot without cross-engine imports.
Published model manifests and actual files gate readiness. No successful setup
or generation is fabricated when downloads, WSL or CUDA cannot be verified.
See VOICE_STUDIO.md and 3D_STUDIO.md for implementation and verification limits.

## 2026-11-07 - Prepare both voices and organize 3D by the task

The owner requested both voices preinstalled and a simpler 3D workflow. The
local updater now gates application replacement on `prepare-voice` verifying
both engines. Ready engines are kept; a failed download is resumable and does
not become an Installed label. Voice is wired once through AppServices for CLI
and API. The UI keeps one setup repair action and hides optional parameters.

3D starts with photo-to-object and separates text-to-HTML scenes from optional
refiners, Gaussian generators, companion studios and expert instructions. A
purpose/output/status table replaces the undifferentiated engine cards. WSL
access failures report an unknown state instead of an invented missing install.

## 2026-11-07 - Voice follows the existing model folder and verifies audio

The owner explicitly keeps models under D:\Models. Voice now reads the existing
storage.custom_model_dir setting through AppServices rather than silently using
the application-data drive. Only model files and receipts follow that folder;
recordings and history keep their existing private location.

Windows voice setup uses the already selected WSL distribution, isolating both
TTS stacks from the core and the TRELLIS environment. The pinned dots.tts code
imports WeTextProcessing/Pynini during runtime import and targets Linux/macOS;
an untested native Windows pip install is insufficient proof of compatibility.
The updater now requests actual short TTS and reference-voice jobs before
reporting completion. File/import readiness and audio verification stay distinct.
Existing verified model files do not consume a second download reservation;
the disk reserve for new downloads is unchanged.

## 2026-11-07 - Embedded dots.tts audio excludes the upstream demo UI

The pinned dots.tts package requires Gradio >=6.17,<7, pinned to 6.17.0 in its
recommended constraints. An owner's setup log proves that requirement cannot
resolve in their installation. Our worker uses DotsTtsRuntime directly and the
React app provides its interface. Install the unchanged source without resolving
its dependencies, then install its declared requirements except the exact Gradio
package, retaining all other requirements and upstream constraints. Record that
intentional profile explicitly. Do not edit the pinned checkout or downgrade
runtime dependencies opportunistically. Setup still requires imports and CUDA;
the updater still requires generated audio before replacing the app.

## 2026-11-07 - Import WhatsApp voice recordings without manual conversion

Voice accepts WAV and Ogg/Opus references in the same upload flow. Reuse the local
FFmpeg discovery used by video finishing through the shared media helper, keeping
the Voice and Images engines independent. Detect Ogg by content, force its demuxer
and local-only protocols, bound conversion time and decoded duration, then validate
the PCM WAV before retaining it. References still require 3-60 seconds and at most
20 MB compressed input. Long audio is rejected instead of quietly cropped. The
models keep their existing WAV-only private input contract; no new ML dependency,
network service or permanent copy of the source recording is needed.

## 2026-11-07 - Restore the earlier 3D layout at the owner's request

Undo the task-tab presentation and restore the existing three-column studio:
gallery/export on the left, preview in the center, generation controls/Hi3D on
the right. Show generation and export parameters directly and restore comparison
engine cards. Keep 3D Coder and the comparison lab below the main studio. This is
a presentation rollback, preserving all current engines, gallery/source comparison,
unknown WSL probe handling and Voice's new Ogg/Opus import.

## 2026-11-08 - Retire Voice and Qwen 3D Coder; report video progress

The owner superseded the earlier Voice and scene-coder decisions: remove the
Voice interface, engine integration and automatic setup, and remove only Qwen
3D Coder from the 3D studio. Keep TRELLIS.2, Hi3D, the comparison lab, galleries,
original GLBs and Web export. Do not delete a general-purpose Qwen model merely
because the removed scene-coder used it.

Observe ComfyUI progress for the submitted video prompt only. Keep percentages
monotonic across both Wan samplers and show decoding, encoding, saving and
finishing separately. Socket loss must not fail generation. A percentage is
workflow progress, not a measured ETA; an unknown remaining duration stays
unknown. Only the saved MP4 and recipe permit 100 percent. Release as 0.4.1.

Voice model cleanup is limited to the dedicated VoxCPM2 and dots.tts SOAR model
directories after checking their resolved paths. Do not treat code removal as
proof that files on the owner's drive were deleted; record the actual result.
