# Will It Run

**What AI team can this PC run?**

Tell Will It Run what kind of AI you want.

Will It Run analyzes your Windows PC.
It finds the strongest models your hardware can run.
It supports both standard and uncensored open models.
It chooses the right quantization.
It makes sure you have enough disk space.
It downloads the models through LM Studio.
It benchmarks them.
It creates specialized AI agents.
It connects them with LangGraph.
Then it runs your entire AI team locally.

Will It Run is a Windows 11 desktop application. Everything runs on your machine: no cloud
account, no API key, no telemetry. If you want, it can also use Claude, ChatGPT, DeepSeek or
Gemini with your own API key, wherever a local model can go.

## What it does

| | |
|---|---|
| **Hardware** | Detects CPU, RAM, every GPU (NVIDIA, AMD, Intel), VRAM, drivers, CUDA and storage, then works out how much memory is *safely* usable after Windows and a safety margin. |
| **Model fit** | For any model file: VRAM, RAM, KV cache and overhead, estimated tokens/sec, and one of eight verdicts from `PERFECT` to `IMPOSSIBLE`, with the reasons. |
| **Model explorer** | Live Hugging Face search plus a built-in catalog and your LM Studio models. Filters for task, tool use, vision, installed, and Standard / Uncensored alignment. |
| **Model files** | Every file of a repository with its own Will It Run score and the quality its quantization keeps (published llama.cpp measurements), so any of them can be chosen knowingly. |
| **Downloads** | The models already on this PC, with size, speed and fit. Exact file, size and hash before anything starts. A download that would breach the disk reserve is refused, with alternatives. Pause, resume, stop, retry, and a folder of your choice per download. |
| **Guide** | A built-in guide for people new to local AI: how it works, what the verdicts and words mean, and a "?" next to the terms that need it. Interface in Italian and English, with zoom. |
| **Benchmarks** | Measures load time, time to first token, tokens/sec, VRAM, RAM and GPU utilization on your PC. Measured numbers replace estimates everywhere. |
| **Arena** | Puts models, one at a time, through the same questions, each with an answer that **code** can check: facts, math, logic, code that is run, SQL, Terraform, Ansible, documents, PDFs, pictures, JSON, tool calls, questions that cannot be answered, orders hidden in a text. Nothing is judged by another model; what no code can judge is shown as *not evaluated*, never as wrong. Leaderboard with confidence intervals, a matrix of every question, a duel between two models, a report of every question with what each model answered, side by side, models tested in separate challenges put side by side when they asked the same questions, reports in HTML, Markdown, CSV and JSON, and every run records the suite version, the seed and the sampling so it can be repeated. See [docs/ARENA.md](docs/ARENA.md). |
| **AI teams** | Pick specializations or describe a team. Will It Run generates the agents, assigns the *fewest* models that cover them, and plans loading so the peak fits your VRAM. The plan says which model does which job, role by role, and any role can be given a different one. |
| **Workflows** | YAML definition, validator, LangGraph compiler. A visual node editor. Human approval gates, conditional edges, bounded loops, structured outputs, tools. |
| **Verification workflows** | Four templates where no agent is trusted: Adversarial Blackboard, Commit DAG, Fail-Closed Swarm, Deferred-Consensus Delphi. Agents see only what they are meant to, code checks every quote against the evidence, counts the votes and compares the hashes, and a run that cannot verify its answer refuses instead of guessing. |
| **Several models** | Each agent can have its own model. A gateway swaps them one at a time, measures free VRAM before every load, fits the context to it, checks again afterwards, and learns what each model really costs. On *Automatic* the context of a model is chosen for each request from what the request needs and what this PC holds, and a conversation that outgrows it is given more or shortened instead of failing. |
| **Sessions** | Watch a team work on its own graph: nodes light up as they finish, the arrow into the working agent flows at the speed of its tokens, and its reasoning streams next to it. Each tool call, each model switch and its latency, live GPU and RAM. Approve or deny risky actions. The prompt sits at the bottom, as in a terminal assistant: say what to do and where (a folder on the PC, or one of your GitHub repositories, picked from a list after signing in on github.com), then ask for the next thing under the result. |

| **Clone a site** | Give the address of a website and get a Next.js project, with the **accuracy** of the copy measured against the original. The default is an **exact copy**: Chrome opens the site like a visitor, hides cookie banners and pop-ups, and Will It Run freezes what it rendered (markup, style rules, pictures, fonts, web components, a phone version of each section) into a project with a component for each section, builds it, and compares it with the original pixel by pixel at desktop and phone width: 99.9% on brioni.com, in about a minute, with no model for the copy. A model on this PC names the components and, only if a section is weak, fixes it. The first way, an agent that follows the skill of [ai-site-cloner](https://github.com/Mahanaicoach/ai-site-cloner) (MIT), is still there for models that can use it. **From a picture**: give a reference image and a brief; a model that sees writes the page region by region, the page is measured against the picture, and GSAP motion (numbers the model chooses for a fixed component, given sentences of the design and GSAP skills) is kept only if the page at rest still matches. See [docs/CLONE.md](docs/CLONE.md). |
| **Cloud models** | Claude, ChatGPT, DeepSeek and Gemini with your own API keys, encrypted with Windows for your account. A cloud model can be chosen in Clone a site, in Chat and in any slot of a workflow; nothing is loaded on the PC for it. See [docs/CLOUD.md](docs/CLOUD.md). |
| **Project Brain** | Every folder or GitHub repository a team or a chat works in keeps a permanent memory: how it is built, what was decided, which files were changed and why, what went wrong and what fixed it. It is recorded from what the agents actually did, not from a summary a model wrote. The next session is handed only the entries its task needs, found by meaning (an embedding model in LM Studio), by words and through a knowledge graph of entries, files and imports. |
| **Images** | ComfyUI is the engine, and the page starts it for you: if it is not on the PC the page says so and links to the official installer instead of installing it silently. A model is a list of parts — diffusion model, text encoder, VAE, required custom node — and an encoder shared by two models is downloaded once. Every part is verified against the publisher's size and checksum, and nothing counts as installed unless the file is in ComfyUI's folder. The page plans the memory before you press Generate: what it needs, what this card can give, and what to change if it does not fit. Create, edit a photo, upscale, one to four pictures at a time. The catalog is photographic only (Qwen-Image 2.1 UC, KreaNoct, Noct Q, Z-Image Turbo, Krea 2 and SDXL realism checkpoints, plus Pony Diffusion V6 XL; no anime-only models), with verified Hugging Face sources. See [docs/IMAGE_STUDIO.md](docs/IMAGE_STUDIO.md). |

A beginner can do: *"I want a coding AI team" → Automatic → Install → Run.*
An expert can inspect the exact GGUF, quantization, context, KV cache, GPU offload,
loading strategy, system prompts, LangGraph state, conditional edges and tools.

## Requirements

- Windows 11 x64 (Windows 10 x64 may work; it is not tested)
- [LM Studio](https://lmstudio.ai/download) for running models. Will It Run never installs it for you.
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI) only if you want the Images page. It is GPL-3.0 and
  is not bundled or installed by Will It Run: you install it, and Will It Run talks to it over
  `127.0.0.1`.
- A GPU is recommended. Without one, Will It Run plans for CPU inference with smaller models.

## Install

Download the latest `WillItRun-<version>-Setup-x64.exe` from the
[Releases](https://github.com/daniele9233/will-it-run/releases/latest) page and run it. It installs for the current
user and adds a Start Menu entry. Nothing else is installed: the Python runtime Will It Run needs is bundled inside
the app. The installer is not code-signed, so Windows SmartScreen warns on first run; each release carries a
`.sha256` file to check the download. How a version is published: [docs/RELEASING.md](docs/RELEASING.md).

## Command line

The installer includes the same engine as a CLI (`willitrun-core.exe` in the install
folder; `willitrun` in a development checkout).

```
willitrun hardware                      # what this PC has and what is safely usable
willitrun models                        # installed LM Studio models with their fit
willitrun recommend -s coding           # best models for a job on this PC
willitrun recommend --uncensored
willitrun model search "Qwen uncensored 27B"
willitrun model info <repo>             # every file's fit, size and metadata
willitrun model install <repo> -q Q4_K_M
willitrun benchmark <model-key>
willitrun team create -s coding -s devops -s enterprise_docs
willitrun workflow list
willitrun workflow run <id> --task "..." --model <model-key>
willitrun workflow run <id> --task "..." --folder D:\code\shop   # works in a project: it has a memory
willitrun memory list                   # the projects that have a memory
willitrun memory search shop "why is the session in SQLite"
willitrun doctor                        # PASS / WARNING / FAIL environment checks
```

Most commands accept `--json`.

## Building from source

```powershell
uv venv backend\.venv --python 3.13
uv pip install --python backend\.venv\Scripts\python.exe -e "backend[dev]"
cd desktop; npm install; cd ..

# develop
backend\.venv\Scripts\willitrun.exe serve --port 8765     # core
cd desktop; npm run dev                                   # UI at http://127.0.0.1:1420
cd desktop; npx tauri dev                                 # or: the real desktop shell

# test (only what your change needs; --all is what CI runs)
python scripts/check.py
cd backend; .venv\Scripts\python.exe -m pytest
cd desktop; npm run typecheck; npm run lint; npm test

# installer -> dist\WillItRun-Setup-x64.exe
.\scripts\build.ps1
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Documentation

- [Architecture](docs/ARCHITECTURE.md): components, and what was reused from llmfit
- [Windows](docs/WINDOWS.md): paths, packaging, notifications
- [Hardware detection](docs/HARDWARE_DETECTION.md)
- [Model scoring](docs/MODEL_SCORING.md): fit categories, memory, speed, score, accuracy by quantization
- [Localization](docs/LOCALIZATION.md): Italian and English, how to add a string or a language
- [LM Studio](docs/LM_STUDIO.md): exactly which commands and endpoints are used
- [Uncensored models](docs/UNCENSORED_MODELS.md)
- [Images](docs/IMAGE_STUDIO.md): ComfyUI as the engine, the models worth running at 16 GB, the
  quality settings, the licences, and how to add a model family
- [Workflows](docs/WORKFLOWS.md): the YAML DSL, templates, tools
- [Project Brain](docs/MEMORY.md): what each project remembers, how it is searched, what was borrowed from other memory projects
- [Clone a site](docs/CLONE.md): the exact copy and its measure, building from a picture, the agent that follows the kit, the skills
- [Cloud models](docs/CLOUD.md): keys, how each provider is called, what leaves the PC
- [LangGraph](docs/LANGGRAPH.md): how definitions compile and run
- [Security](docs/SECURITY.md)
- [Development](docs/DEVELOPMENT.md)
- [Working with an AI model on this code](docs/AGENT_CONTEXT.md): [AGENTS.md](AGENTS.md), the generated map in `docs/map/`, the code graph, the project memory, [decisions](docs/DECISIONS.md) and [lessons](docs/LESSONS.md)

## What version 1 does not do

Stated plainly, so nothing is assumed:

- **Video generation.** Wan 2.2 runs in ComfyUI on this PC, but no video workflow is included.
  Images are covered; moving pictures are not.
- **Masks, inpainting a region, and face detail.** Editing is instruction-based or whole-image,
  and `FaceDetailer` needs a custom node the app does not install. See
  [docs/IMAGE_STUDIO.md](docs/IMAGE_STUDIO.md).
- **Seeing the screen.** Computer-use agents can list windows, read the clipboard, take
  screenshots to disk and, with approval, type and click. They do not yet interpret
  screenshots.
- **Pause/stop through LM Studio's downloader.** Its documented API has no such call.
  Will It Run's own downloader (the default) does all three; LM Studio's is used only if
  you select it.
- **A full Italian translation of everything.** The interface and the explanations the
  core writes about models, disks and downloads are translated, as are the names and
  descriptions of workflow templates and checks. Team plans, the content of workflows
  (roles, prompts, edge labels, check verdicts), validation messages, the Doctor report,
  logs and the command line are in English, as is everything the models themselves write.
- **Reading PDF or Word files as evidence.** Verification workflows read plain-text
  files only and list anything else as skipped.
- **Calibrated consensus thresholds.** The Delphi tally uses fixed settings. Statistical
  calibration needs questions with known answers, which Will It Run does not have.
- **Signatures.** The ledger of a run holds SHA-256 hashes, not signatures.
- **A memory that judges what is true.** The Project Brain records what runs did and what
  agents wrote; a wrong note stays wrong until it is edited. Its search by meaning was
  tried on LM Studio's own embedding model once and not tuned on a real project. See
  [docs/MEMORY.md](docs/MEMORY.md).
- **Web access for agents.** Fact-checking and research agents work from model knowledge
  and files you provide, and say so in their output.
- **Reverse engineering of programs.** Not offered. Earlier versions had REA for websites and for
  apps and firmware; Clone a site replaced it.
- **Cost tracking for cloud models.** Tokens read and written are shown per question; prices
  change too often to be computed here.

## Acknowledgements

Memory and speed estimation, quantization tables, the GPU bandwidth table and the seed
model catalog are adapted from [llmfit](https://github.com/AlexsJones/llmfit) (MIT).
Cloning uses [ai-site-cloner](https://github.com/Mahanaicoach/ai-site-cloner) (MIT, Mahanaicoach), fetched from GitHub
on the user's PC and not bundled.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

MIT. See [LICENSE](LICENSE).
