# Images: ComfyUI as the engine

Will It Run does not run a diffusion model itself and does not download one. It queues a
workflow to **ComfyUI** on the same PC, waits, and keeps its own copy of the picture and of
the settings that made it. One page, `Images`, and one package, `agentfit/imagegen`.

This document is in three parts: how the integration works, which models are worth running on
a 16 GB card, and what the licences actually say. The model survey is dated **26 October 2026**
and says where each claim comes from; a figure without a source is marked *unverified*.

## Why ComfyUI and not something built in

- It is the runtime the models are published for. A checkpoint that exists is almost always a
  ComfyUI workflow away from running, and the nodes that matter (GGUF loaders, upscalers,
  detailers) are written for it.
- It has an HTTP API small enough to drive honestly: `POST /prompt`, `GET /history/<id>`,
  `GET /view`, `POST /upload/image`, `POST /interrupt`, `POST /free`, `GET /system_stats`,
  `GET /object_info`, `GET /models/<folder>`. Nothing in this app guesses what the engine can
  do: the node classes and the model files are read from the engine itself.
- It is **GPL-3.0** and it is *not* bundled with Will It Run. You install and update it; Will It
  Run only talks to it over `127.0.0.1`. See [ComfyUI's repository](https://github.com/Comfy-Org/ComfyUI).

The alternative — embedding a diffusion runtime — would mean shipping tens of gigabytes of
Python wheels, CUDA kernels and model licences inside an app whose job is to *measure* what a
PC can run. Hence the split: ComfyUI draws, Will It Run asks and records.

## How it is wired

| Piece | File | What it does |
|---|---|---|
| Engine | `backend/src/agentfit/imagegen/engine.py` | Finds ComfyUI on this PC, starts it, stops it, and says what it found. |
| Engine client | `backend/src/agentfit/imagegen/comfy.py` | The HTTP surface above. Turns ComfyUI's nested error JSON into one readable line; never assumes a folder exists. |
| Components | `backend/src/agentfit/imagegen/components.py` + `data/components.json` | Every file a model needs, where it comes from, what is on disk, and the downloads in flight. |
| Workflows | `backend/src/agentfit/imagegen/templates.py` + `data/workflows/*.json` | A workflow is a ComfyUI graph with `{{placeholders}}`. Adding a model family is adding a JSON file. |
| Models | `backend/src/agentfit/imagegen/catalog.py` + `data/models.json` | A model names its components. Anything else in `checkpoints/` is discovered and treated as SDXL with settings guessed from its name. |
| Memory | `backend/src/agentfit/imagegen/plan.py` | What a model needs, what this card can give, and what to change when it does not fit. |
| Jobs | `backend/src/agentfit/imagegen/service.py` | One thread per picture, progress on the event bus (`image.queued`, `image.started`, `image.progress`, `image.done`, `image.failed`), one row per picture in SQLite (`image_generation`, migration `0005_images.sql`). |
| Routes | `backend/src/agentfit/api/image_routes.py` | `/api/images/*`: engine, models, plan, components, downloads, check, generate, jobs, cancel, gallery, upload, free. |
| Page | `desktop/src/pages/Images.tsx` + `components/images/` | The engine card, the composer, the model manager, the preview and the gallery. |

A generation is a job, not a request: `POST /api/images/generate` returns at once and the
picture arrives through the gallery and the event stream. **Nothing is claimed as installed
unless the file is on disk where ComfyUI looks for it**, and **nothing is queued unless every
node class the graph uses exists** — the page shows what is missing by name before you press
Generate.

## Starting the engine

The Images page has one button, and it does the honest thing in each of the three cases:

| State | What the card shows |
|---|---|
| **Running** | Green dot, ComfyUI version, free VRAM, and *Stop engine* — which is only enabled when Will It Run started that process itself. |
| **Installed, stopped** | The folder that holds `main.py`, and *Start engine*: `python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --preview-method none`, with the output in `%LOCALAPPDATA%\Will It Run\Data\logs\comfyui.log`. The first start loads the model list and takes up to a minute. |
| **Not installed** | "ComfyUI is not on this PC yet" and two links: the **official installer** (`https://comfy.org/download`, which redirects to `download.comfy.org/windows/nsis/x64`) and the **portable build** (`ComfyUI_windows_portable_nvidia.7z` from the GitHub release). Also *Look again*, which rescans. |

Will It Run does not install ComfyUI for you. It is GPL-3.0, it is not part of this app, and
installing someone else's application silently is not a thing a model manager should do.

**Where it looks:** the folder in Settings → Images first, then
`…\Comfy-Desktop\ComfyUI-Installs\*\ComfyUI`, `D:\ComfyUI`, `C:\ComfyUI`,
`…\ComfyUI_windows_portable\ComfyUI`, ComfyUI Desktop's own install, `%APPDATA%\ComfyUI`,
`%USERPROFILE%\Documents\ComfyUI`, and any `ComfyUI*` folder at the top of drives C–G. A folder
counts only if it has `main.py`; an interpreter is looked for in `.venv\Scripts\python.exe` or
`python_embeded\python.exe` next to it.

## A model is a list of parts

This is the rule the whole feature exists for: **not every model is three files**. An SDXL
checkpoint carries its text encoders and its VAE inside the checkpoint; a Krea 2 or Qwen-Image
build needs them separate. So `models.json` names **components**, and `components.json`
describes each component once.

```
kreanoct-v4          → krea2-kreanoct     + krea2-te + krea2-vae
realdream-krea2-v3   → krea2-realdream-v3 + krea2-te + krea2-vae   ← encoder and VAE shared
qwen-image-2.1-uc    → qwen21uc-unet      + qwen21-te-int8 + qwen21-vae   (+ the ComfyUI-GGUF node)
noctq-v4             → qwen21-noctq-v4    + qwen21-te-int8 + qwen21-vae   ← encoder and VAE shared
lustify-v9           → sdxl-lustify-v9                                ← one file, all-in-one
```

Downloading KreaNoct and then Real Dream fetches **only** the second diffusion model:
the encoder and the VAE are already there and are the same component. That works for every
model in the catalog, because it is a graph, not a special case.

What the registry records per component (`image_component`, migration `0006`): file name,
folder, absolute path, size, SHA-256, source, licence, when it was installed, when it was last
verified, and the last error. What it does **not** do:

- It does not trust a database row over the disk. A component counts as present only if the
  file is in `models/<folder>/` under the name the catalog (or the download) gave it.
- It does not invent a URL. A component either has a **verified source** — Hugging Face
  (`repo` + filename, resolved through the Hub's tree API for the exact path, size and LFS
  checksum) or Civitai (`model_id`/`version_id`/`file_id`, resolved through its API: a version
  with several files — fp8, int8, nvfp4 — downloads the one the catalog names, not the first) —
  or it is marked **to place by hand**, and the interface says which folder the file goes in.
- It does not pretend Civitai is open. **Since the 2026-10-27 check Civitai answers 401 to a
  download without a token**, so a Civitai component is flagged `needs_token` until Settings →
  Images has one, shows *Needs your Civitai token* and refuses before queueing instead of
  failing halfway. Hugging Face files in this catalog need no token.
- It does not delete a shared part. `DELETE /api/images/components/{id}` refuses when more
  than one model names that component, and says which ones. The page disables the bin icon
  for shared files.
- It does not start a download that would eat the disk reserve: the size is known from the
  publisher before the first byte, and `storage.plan_download` gates it exactly as it does for
  text models. The `.part` file is removed on cancel and on a failed checksum.

A download is a job too: `image.component` and `image.component.progress` on the bus,
`GET /api/images/downloads` to read the state, `POST /api/images/downloads/{id}/cancel` to
stop it. Custom nodes are installed with `git clone --depth 1` into `custom_nodes/` and need a
ComfyUI restart — the app says so instead of pretending it took effect.

### What the plan says before anything is downloaded

`GET /api/images/models/{key}/plan` returns, per model: the components and their state, what
is missing and how many bytes, the disk verdict, and a memory estimate:

- the weight footprint is the catalog figure when it is known, otherwise measured file sizes;
- **0.5 GB** is added for latents, attention workspace and the VAE decode at one megapixel;
- the budget is this PC's *safely usable* VRAM (hardware profile minus the OS and the 5%
  margin), the free VRAM comes from ComfyUI itself, and whether LM Studio holds a model is
  read live;
- a model with a separate text encoder is counted in two stages — the encoder reads the prompt and
  leaves the card before the sampler runs — so the weight figure is the larger of *(diffusion
  model + VAE)* and *(encoder)*, not their sum. If any size is unknown it falls back to the
  catalog figure instead of guessing;
- a model that overshoots the budget by up to 0.5 GB is reported as *tight*, not as impossible:
  ComfyUI streams weights through the card, and KreaNoct (14.5 GB estimated against 14.2 GB
  usable) made a 1024×1536 picture in 14 s on this PC. Past 0.5 GB the plan says it does not fit;
- the cost of a megapixel is the one modelled number (1.3 GB for SDXL, 2.1 for Krea 2, 2.3 for
  Qwen-Image). It is used to *rank* resolutions, and every place it appears says **estimate**.

The result is `fits`, `max_megapixels`, `warnings` and `suggestions` as **codes** (`does_not_fit`,
`tight`, `free_vram_low`, `lmstudio_loaded`, `lower_resolution`, `close_lmstudio`,
`enable_offload`, `lighter_model`…), so the interface writes them in its own language instead
of showing English from the core. The page shows the sentence with the numbers and, when a run
dies of OOM, the job comes back with `hints` built from the engine's own error text.

## Settings

`%APPDATA%\Will It Run\settings.yaml`, or Settings → Images:

```yaml
images:
  comfy_url: http://127.0.0.1:8188
  comfy_dir: ""               # the folder with main.py; empty: look for it
  timeout_seconds: 900        # one picture; the first one loads the model
  request_timeout_seconds: 30
  gallery_dir: ""             # empty: <local app data>\images
  free_vram_on_leave: false   # true: unload ComfyUI's models when the Images page is left
  civitai_token: ""           # only for the models Civitai gates
  hf_token: ""                # only for gated Hugging Face repositories
```

### Starting ComfyUI for this

```bat
python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch
```

- `--cache-ram` is the default and keeps models loaded between runs: that is what makes the
  second picture much faster than the first.
- `--cache-none` does the opposite of what its name suggests (it re-executes every node on
  every run). Do not use it for this.
- `--reserve-vram 1.5` leaves the desktop compositor its memory. On current builds
  `--lowvram` has no effect while dynamic VRAM is on; use `--disable-dynamic-vram` only if you
  want the old estimate-based loading back.
- `--use-sage-attention` is usually a small win. `--fast` changes numerics (fp16 accumulation,
  fp8 matrix multiply) and may change quality: try it and compare.

Model folders come from `extra_model_paths.yaml` next to `main.py`, so a shared folder outside
the install works:

```yaml
shared_models:
  base_path: 'D:\Comfy-Desktop\ComfyUI-Shared\models'
  diffusion_models: diffusion_models/
  checkpoints: checkpoints/
  text_encoders: text_encoders/
  vae: vae/
  loras: loras/
```

A `.gguf` unet needs [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF); the node it adds is
`UnetLoaderGGUF`. Without it, a GGUF model is reported as missing its node, not as broken.

## The models

### What the app knows by name

This catalog is **photographic only**. Anime, cartoon and illustration models are not offered
(NoobAI, Illustrious and the Kroma builds, which drew an animated look, were removed on
2026-10-27). `data/models.json` and `data/components.json` hold models whose files, loaders and
sampling were checked, with sizes and download sources read from the publishers' APIs.

| Model | Family | Source | On 16 GB | Licence |
|---|---|---|---|---|
| **Qwen-Image 2.1 UC (Q6_K, GGUF)** | `qwen_image` | Hugging Face `abenzerps/Qwen-Image-2.1-Uncensored-Quantized` | 5.9 GB diffusion model; the 9.4 GB encoder leaves the card first | Qwen Research: **non-commercial** |
| **KreaNoct V4 INT8** | `krea2` | Hugging Face `Noctaluna/Krea2-Noct-Uncensored` | 13.5 GB, tight; 14 s for 1024×1536 | Krea 2 Community |
| **Noct Q V4 (Qwen 2.1 UC, INT8)** | `qwen_image` | Hugging Face `Noctaluna/Noct-Q-Uncensored-Qwen-Image-2.1` | 7.3 GB | Qwen Research: non-commercial |
| Qwen-Image 2.1 UC (INT8, no GGUF) | `qwen_image` | same repository as the Q6_K | 7.3 GB | Qwen Research: non-commercial |
| Big Love KreaEdit3 (INT8) | `krea2` | Civitai 897413, **needs a token** | 13.5 GB | Civitai: images, rent |
| Real Dream Krea 2 V3 (INT8) | `krea2` | Civitai 153568, **needs a token** | 13.5 GB | Civitai: images, rent, sell |
| Moody Krea 2 Mix V9 (INT8) | `krea2` | Civitai 2731187, **needs a token** | 13.8 GB | Civitai: images, rent |
| LUSTIFY! ZENITH V9, bigASP v2.5 | `sdxl` | Civitai, **needs a token** | 7 GB, comfortable | Civitai: images |
| CyberRealistic XL v10, Juggernaut XL Ragnarok, RealVisXL V5 | `sdxl` | Civitai / Hugging Face | 7 GB | OpenRAIL / Civitai |
| Qwen-Image 2.1 Turbo and base (official) | `qwen_image` | Hugging Face `Comfy-Org` | 7.3 GB | Qwen Research: non-commercial |
| MOSS Image SFT | `moss_image` | by hand | — (blocked: ComfyUI 0.38.0 has no loader) | see model card |

The three Civitai Krea 2 checkpoints are the most-downloaded photoreal NSFW builds of the
month on Civitai (211 k, 190 k and 121 k downloads on 2026-10-27) and are the same architecture
and INT8 format as KreaNoct, so they run on the same template — but **they have not been run on
this PC**: Civitai would not give them without a token. The page says so in the model's note.

The Krea 2 encoder and VAE, KreaNoct, the Qwen UC diffusion model, Noct Q and the encoder/VAE of
Qwen-Image 2.1 all have a **verified Hugging Face source** (path, size and SHA-256 come from the
Hub's tree API at download time). Only the MOSS parts are still *to place by hand*.

### What it discovers

Anything in ComfyUI's `checkpoints/` folder becomes a model too. The family is guessed from the
name — photoreal or plain SDXL — and used only to pick starting steps, CFG, sampler and a
negative prompt that asks for a photograph, not a drawing. The page says it is a guess. That is how a checkpoint that is not in
the catalog still runs: they are all SDXL, and SDXL is one graph.

### Recommended stacks, 27 October 2026

**(A) Photoreal uncensored — what this PC is best at.** Two families, both verified here.

```
# Qwen-Image 2.1 UC: the ComfyUI flow that works, now inside the app
models/diffusion_models/qwen-image-2.1-UC-Q6_K.gguf      # abenzerps, 5.9 GB, Unet Loader (GGUF)
models/text_encoders/qwen3vl_8b_int8_convrot.safetensors  # CLIPLoader, type qwen_image
models/vae/qwen_image_2.1_vae_bf16.safetensors
# custom node: leejet/ComfyUI-GGUF (the city96 branch fails with "Unknown model architecture")

# KreaNoct V4: Krea 2 Turbo, uncensored
models/diffusion_models/KreaNoct_V4_int8_convrot.safetensors
models/text_encoders/qwen3vl_4b_fp8_scaled.safetensors    # CLIPLoader, type krea2
models/vae/qwen_image_vae.safetensors
```

*Qwen UC*: `TextEncodeQwenImage21` → `KSampler`, **25 steps, CFG 1, euler / simple**, 1024×1024
(up to 2048×2048 in multiples of 32). At CFG 1 the negative prompt is ignored; raise CFG to 3–4
only when you want it, at about twice the time. 40–50 steps is the quality setting (the page has
a *Preset: Qualità massima* button). *KreaNoct*: **8 steps, CFG 1, euler / simple**, 1024×1536,
no negative prompt, prompts written as the caption of a photo that already exists. Noct Q is
the same Qwen graph with its author's recipe: 25 steps, CFG 3 and his negative prompt.

The on-policy distillation of Krea 2 and the quality of the uncensored merges are the
publishers' claims, not independent measurements. KreaNoct's page requires every character to
be an adult and Noct Q's asks for an adult age in the prompt; the app does not police what you
prompt, and real people's likeness stays outside what any of these licences or laws allow.

**(C) Editing — the gap in the current setup.** Qwen-Image 2.1 writes and edits, but its
licence is non-commercial. The clean-licence upgrade is **Qwen-Image-Edit-2511**
(Apache-2.0, better identity consistency, LoRAs baked in), and the fast Apache-2.0 lane is
**FLUX.2 klein 4B** (8.4 GB VRAM distilled, editing and generation in one model).

```
# Qwen-Image-Edit-2511 through ComfyUI-GGUF
models/unet/qwen-image-edit-2511-Q4_K_M.gguf
models/text_encoders/Qwen2.5-VL-7B-Instruct*.safetensors + its mmproj
models/vae/qwen_image_vae.safetensors

# or FLUX.2 klein 4B (Apache-2.0)
models/diffusion_models/flux-2-klein-4b-fp8.safetensors
models/text_encoders/qwen_3_4b.safetensors
models/vae/flux2-vae.safetensors
```

Neither is in `data/models.json`, because no graph has been checked for them here: the Images
page offers only what it can actually run, and says which file is missing when one is.
Adding one is a JSON file, described below. [FLUX.1 Kontext is legacy](https://bfl.ai/licensing)
and non-commercial.

**Download order, smallest first:** `flux2-vae` + `qwen_3_4b` + `flux-2-klein-4b-fp8`
(Apache-2.0, generation and editing both), then a Qwen-Image-Edit GGUF, then a Krea 2-class
photoreal checkpoint, which is the biggest file and the biggest licence question.

### What was checked and what could not be

| Claim | Status |
|---|---|
| FLUX.2 [klein] 4B, created 2026-01-14, Apache-2.0 | verified on Hugging Face (`black-forest-labs/FLUX.2-klein-4B`) |
| Qwen-Image 2.1, released 2026-09-20, Qwen Research Licence | verified (`Qwen/Qwen-Image-2.1`, `Comfy-Org/Qwen-Image-2.1`) |
| Qwen-Image-Edit-2511, Apache-2.0 | verified (`Qwen/Qwen-Image-Edit-2511`) |
| Z-Image-Turbo, 6B, Apache-2.0, 9 steps, CFG 0 | verified (`Tongyi-MAI/Z-Image-Turbo`); **Z-Image-Edit is still "to be released"** on its card |
| Qwen UC Q6_K, KreaNoct V4, Noct Q V4: file names, sizes and SHA-256 | read from the Hugging Face tree API on 2026-10-27 |
| KreaNoct V4, Qwen UC Q6_K and Noct Q V4 generate through the app | **verified** on this PC (RTX 4080 SUPER), one clothed-portrait prompt each, through `ImageStudio` against the real ComfyUI 0.38.0: KreaNoct 1024×1536 in 14 s (8 steps); Qwen UC Q6_K 1024×1024 in 36 s (25 steps, CFG 1); Noct Q V4 1024×1536 in 34 s (25 steps, CFG 3). The first run of each includes loading the model |
| Civitai downloads without a token | **no**: HTTP 401 on 2026-10-27 for every version tried |
| Pony Diffusion V7 | **not verified — treat as rumour** |
| HiDream, Chroma, ACE++, CatVTON, PuLID, SUPIR, CCSR current status | **not verified in this survey**; not used by any template here |
| A 4080 SUPER timing for any of these | **not published anywhere reached.** The only 16 GB-class figures found are for an RTX 5060 Ti: Qwen-Image 2.1 INT8 at 21.4 s (1024²) and 124.1 s (2048²). Do not read them as this card's numbers. |

## Quality: the knobs that matter

| Family | Steps | CFG | Sampler / scheduler | Notes |
|---|---|---|---|---|
| Krea 2 / KreaNoct | 8–12 | 1.0 | `euler` / `simple` | These are the distillation targets. More steps do not help. |
| Qwen-Image 2.1 UC | 25 (40–50 for the best quality) | 1.0 (3 with a negative prompt) | `euler` / `simple` | At CFG 1 the negative prompt is ignored. Sizes in multiples of 32. |
| Qwen-Image upscale pass | 16 | 1.0 | `euler` / `simple` | Denoise 0.35: repaint the blur, keep the person. |
| SDXL photoreal | 25–35 | 4.5–5.5 | `dpmpp_2m` / `karras` | Hires fix at ×1.5, denoise 0.4–0.45. |
| FLUX.2 klein distilled | 4-step schedule | — | ComfyUI's distilled edit template | 8.4 GB VRAM documented by ComfyUI. |

Three things change perceived quality more than a different checkpoint: a **second pass at low
denoise** (`sdxl_hires`, or `*_upscale` on an existing picture), a **detail pass on the face**
(ComfyUI's Impact Pack `FaceDetailer`, not installed here yet — it needs the custom node), and
**fewer, better LoRAs**.

LoRA rules that hold across families: match the base architecture exactly (an SDXL LoRA on
Krea 2 is noise), use the trigger words from the model's own page, test each one alone at 0.5
before stacking, and keep the stack at three or fewer with a summed weight under 1.5. Detail
LoRAs first, style LoRAs last, in the chain the page builds for you.

## 16 GB in practice

- KreaNoct holds ~13.5 GB. Close LM Studio's model first: the budget is per-card, not per-app, and
  when the engine spills to system memory a picture goes from seconds to minutes. Qwen UC is the
  light one: 5.9 GB of diffusion model, with the encoder swapped out after the prompt.
- Qwen-Image 2.1 is tight because the text encoder is large; a Q6_K unet plus an 8.7 GB encoder
  leaves little room at 2 megapixels.
- 2048×2048 is the point where a 16 GB card starts swapping. Generate at 1 MP and upscale.
- Press **Free VRAM** before going back to LM Studio: it calls ComfyUI's `/free`, which unloads
  everything the engine holds.

## Legal and licences

Not legal advice. What the texts say, as of 26 October 2026:

- **EU AI Act, Article 50(2)** — outputs of generative systems must be marked in a
  machine-readable format and be detectable as artificially generated. Per
  [Article 113](https://artificialintelligenceact.eu/article/50/), this applies from
  **2 August 2026**: it is live now. Publishing also triggers the deployer's disclosure duty.
- **Italy, L. 132/2025** (in force 10 October 2025) amends the penal code for offences committed
  with AI, explicitly including the unlawful diffusion of deepfakes; implemented by
  D.Lgs 160/2026 and D.Lgs 179/2026. Non-consensual likeness is a criminal matter independent of
  any model licence.
- **FLUX [dev]** — the [FLUX [dev] Non-Commercial Licence](https://bfl.ai/legal/non-commercial-license-terms)
  (rev. 2025-11-25) covers FLUX.1 and FLUX.2 `[dev]`: non-commercial only, and §2(e) *requires*
  content filtering or pre-review of every output plus disclosure that it is AI-generated.
  FLUX.2 `[klein] 4B` is Apache-2.0 instead, which is why it is the recommendation above.
- **Krea 2** — [Community Licence](https://www.krea.ai/krea-2-licensing): commercial use only
  under $1M trailing revenue, with content filtering and no removal of provenance.
- **Qwen-Image 2.1** — Qwen Research Licence: "FOR NON-COMMERCIAL PURPOSES ONLY". Qwen-Image
  1.0, Edit and Z-Image are Apache-2.0.
- **Civitai checkpoints and LoRAs** carry their own licence per model page; many are
  `cc-by-nc-nd-4.0`, which forbids derivatives and commercial use.

Net: fictional adults, generated locally, are not what these texts target. **Real people's
likeness, minors, and stripping provenance are.** Will It Run does not police what you prompt:
it shows the licence next to each model and leaves the decision to you, as with text models
([UNCENSORED_MODELS.md](UNCENSORED_MODELS.md)).

## Importing the models you already have

Most people download a dozen `.safetensors` into one folder and then find out that ComfyUI
wants them in four different folders. **Importa modelli locali** (Images → Models) reads the
header of every file in the folder you choose — never the weights — and shows what each one
turned out to be before anything moves.

How a file is recognised, in order:

1. **Exact file name** against `components.json`. That is how the eleven files of this PC were
   recognised with no guessing at all.
2. **Its tensors**, for anything new: low-rank pairs (`lora_A`/`lora_B`, `lora_down`/`lora_up`)
   → a LoRA; `encoder` + `decoder` → a VAE; `blocks` + `txtfusion` or `krea2_svdquant_*` → Krea 2;
   `transformer_blocks` + `img_in` → Qwen-Image; `layers` + `text_embedder` → MOSS;
   `double_blocks`/`single_blocks` → FLUX; a language model with an `lm_head` → a text encoder.
3. Anything left over is listed under **Non riconosciuto** with the reason. It is never
   assigned a role to make the list look complete.

The summary also says, per model, which components are in the folder, which are already
visible to ComfyUI, and which are still missing — so shared parts are obvious: pointing at
`D:\Models` after the first import shows *Text encoder Krea 2* and *VAE Krea 2* as
**Già presente**, because KreaNoct already uses them.

**Three ways to place a file.** *Copia* leaves the originals; *Sposta* moves them (a rename on
the same volume, a copy-then-unlink across volumes, so a failure leaves the original in place);
*Usa la posizione esistente* only registers a file ComfyUI already reads — with a flat folder it
is refused, because mapping one folder to every role would put text encoders among the diffusion
models. `extra_model_paths.yaml` is never edited: doing it for a flat folder would be wrong, and
doing it silently would be worse.

Nothing is overwritten without checking: a same-named file of a different size is a **conflict**,
listed before anything happens, and the import button stays off until it is resolved. Importing
the same folder twice produces the second time an empty list, because every component is already
where it belongs.

### The LoRA files are checked, not trusted

A LoRA is only worth something against the model it was trained for. The scan pairs every LoRA
tensor with a tensor of that family's diffusion model (`…lora_A.weight` → `….weight`) and reports
**how many matched**. On this PC that is the difference between a file that looks fine and a file
that works:

| LoRA | Matched | Why not all |
|---|---|---|
| `chatrlabs-omni-lora-v0.1-bf16` (Qwen-Image 2.1) | 320 / 448 | the INT8 build fuses `img_mlp` into `gate_up`/`out`; the LoRA targets the separate `gate_layer`/`proj` |
| `MOSS-Image-NSFW-Uncensored-LoRA-ComfyUI` | 252 / 504 | the INT8 build fuses q/k/v into `proj.qkv`; the LoRA has `proj.q`, `proj.k`, `proj.v` |

The Omni LoRA also ships in the `transformer.` namespace, which `LoraLoader` cannot read. The
conversion offered by the import rewrites **only the tensor names** (`transformer.` →
`diffusion_model.`), copying every weight byte for byte and never touching the original file —
the same namespace-only change the MOSS LoRA's own metadata documents. After converting, the
verification runs again against the base model.

A run with the converted LoRA and the same seed produces a different picture, so the adapter is
applied — partially: the tensors that the INT8 build does not have are simply not loaded. ComfyUI
does not report that in `/history`; it logs it, which is why this page shows the count instead of
claiming success.

## Adding a model family

A template is a ComfyUI graph in JSON, and `{{name}}` is filled from the request plus the
model's profile. Put a file in `backend/src/agentfit/imagegen/data/workflows/`:

```json
{
  "key": "mymodel_txt2img",
  "name": "Text to image — my model",
  "family": "mymodel",
  "kind": "txt2img",
  "default": true,
  "description": "One sentence on what this model is for.",
  "roles": ["unet", "clip", "vae"],
  "lora_mode": "model",
  "lora_source": "1",
  "needs": ["SomeCustomNode"],
  "defaults": {"negative": "", "steps": 20, "cfg": 3.5, "sampler": "euler", "scheduler": "simple"},
  "graph": {
    "1": {"class_type": "{{unet_loader}}", "inputs": {"unet_name": "{{unet}}", "weight_dtype": "{{weight_dtype}}"}},
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "{{clip}}", "type": "{{clip_type}}"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "{{vae}}"}}
  }
}
```

Rules the binder enforces:

- a value that is exactly `{{x}}` keeps the type of `x`, so `"{{seed}}"` stays an integer;
- an input that resolves to `null` or `""` is removed, which is how one template carries
  `weight_dtype` for `UNETLoader` and not for `UnetLoaderGGUF`;
- a node whose inputs all vanish is removed, and its readers lose that input — that is how the
  optional reference pictures of an edit work;
- a link to a node the template never had is an error, not a silently ignored input.

Then add the model to `data/models.json` with the exact file names under `files`, the loader
settings under `loader` (`unet_loader`, `weight_dtype`, `clip_type`) and its sampling under
`sampling`. `needs` is documentation for the reader: the real check is done against the
engine's `/object_info`. The sampler, decode and save nodes follow the same pattern — copy them
from a shipped template.

## What this does not do

- **Install ComfyUI.** It finds it, starts it, stops it, and links to the official installer.
- **Install custom nodes on its own initiative.** A model that needs one is refused until you
  press the button; the node is cloned with `git` and ComfyUI has to be restarted.
- **Video.** Wan 2.2 is on this PC and in ComfyUI's paths, but no video template exists here.
- **Masks and inpainting a region.** Editing is instruction-based (`qwen_image_edit`) or
  whole-image (`sdxl_img2img`). A mask brush and `VAEEncodeForInpaint` are the next step, and
  the better tool once you know which region you want changed.
- **Face detail.** The `FaceDetailer` node needs the Impact Pack, which is not installed. A
  second low-denoise pass on the whole picture is what the upscale templates do instead.
- **Benchmarks.** No timing on this card is claimed anywhere: the app reports the elapsed time
  of the pictures *it* made, which is a measurement, and nothing else.
