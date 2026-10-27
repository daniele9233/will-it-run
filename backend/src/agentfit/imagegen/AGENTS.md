# Images (`backend/src/agentfit/imagegen`)

Image generation and editing through ComfyUI, which runs on the same PC and is not bundled. Read `docs/IMAGE_STUDIO.md` (how it is wired, the models, the licences) and `docs/map/imagegen.md`. The page is `desktop/src/pages/Images.tsx` and `desktop/src/components/images/`; the routes are `api/image_routes.py` (`/api/images/*`).

## How it is built

- `engine.py` finds, starts and stops ComfyUI; `comfy.py` is its HTTP client; `components.py` + `data/components.json` list every file a model needs and where it comes from; `catalog.py` + `data/models.json` name the models; `templates.py` + `data/workflows/*.json` are ComfyUI graphs with `{{placeholders}}`; `plan.py` is the memory plan; `service.py` runs one thread per picture and writes one row per picture (`image_generation`, migration `0005_images.sql`).
- **Adding a model is data, not code**: a components entry per file (with its source, size and SHA-256), a models entry that names them and its defaults, and a workflow JSON only for a new family. Anything else found in `checkpoints/` is treated as SDXL with settings guessed from its name.
- A model is a list of parts, not one file: an SDXL checkpoint carries its encoders and VAE, a Krea 2, Qwen-Image or Z-Image model needs separate text encoder, VAE and sometimes a custom node.

## Rules

- **Nothing is claimed as installed unless the file is on disk** where ComfyUI looks for it, and **nothing is queued unless every node class the graph uses exists** (asked from `/object_info`). Do not assume a folder exists.
- **Photographic catalog.** No anime, cartoon or illustration models; the one exception, by name, is Pony Diffusion V6 XL (prompt it with photographic tags and keep its photographic negative prompt).
- **Free sources only**, Hugging Face first. Civitai entries stay flagged `needs_token` and are refused before queueing; do not push them on the owner and never ask for a token. Before writing a source into `components.json`, check that the URL answers and that the size and SHA-256 match what the publisher's API says (Hugging Face: `https://huggingface.co/api/models/<repo>/tree/main` gives size and `lfs.oid`).
- **GGUF** unets need ComfyUI-GGUF in the **leejet** branch for Qwen-Image 2.1 (city96's fails with "Unknown model architecture"); restart ComfyUI after installing a node.
- Pictures made to test the pipeline use a plain clothed-portrait prompt. Never commit pictures or `.shots/`; ComfyUI's output folder is not the record, the app keeps its own copy and its own row.
- ComfyUI is GPL-3.0: the app talks to it over `127.0.0.1` and never installs it silently.

## Tests

`tests/test_imagegen.py` (components, plans, templates, jobs, the catalog data, the leejet branch). It runs against a fake ComfyUI; the real one is only for a manual check on a machine that has it.
