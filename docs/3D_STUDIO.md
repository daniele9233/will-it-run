# 3D: Microsoft TRELLIS.2

The 3D page uploads a photo (or a frame at 0.3 seconds of a video), generates a
static 3D asset locally, previews it and exports a self-contained GLB. Existing
TRELLIS v1 gallery files remain viewable and exportable.

## Generation settings

The active engine is Microsoft TRELLIS.2-4B, pinned to model revision
`af44b45f2e35a493886929c6d786e563ec68364d` and source commit
`75fbf0183001ed9876c8dbb35de6b68552ee08bd`. Generation selects explicit
`1024_cascade` (default for 16 GB) or `1536_cascade`, with CPU offloading,
24 sampling steps by default, a one-million
triangle export target and 4096-pixel PBR textures (base color, metallic/roughness).
Texture export supports 2048 or 4096 pixels. Unlike upstream's
default token cap, this worker allows the full 1536 grid and checks the resulting
voxel size; memory failure is reported instead of silently reducing resolution.
1536³ describes the sparse spatial grid, not a polygon count or texture resolution.

Microsoft officially requires 24 GB VRAM. This machine has a 16 GB RTX 4080 SUPER;
1536³ with CPU offloading remains experimental on this hardware; the supplied
samurai has completed successfully (see the verified run below).

## Runtime and public auxiliary models

Setup creates `~/.local/share/willitrun-trellis2` inside the selected WSL2
Linux distribution. It uses isolated Python 3.10, PyTorch 2.6/CUDA 12.4 and
xformers. ComfyUI and the core Python environment are independent.
`3d/runtime.json` records the chosen distribution. A runtime is ready only after
compiled dependencies and the verified weight manifest are both present.

`imagegen/data/trellis2_assets.json` pins every inference weight/configuration with
its upstream revision, byte size and SHA-256. Downloads are size/hash checked.
The 1536 cascade does not load the unused 512 texture flow or training encoders.
The sparse-structure decoder is shared with the original Microsoft TRELLIS model;
that required weight is retained independently of the removed v1 runtime.

The official pipeline defaults to gated DINOv3 and BRIA background-removal
repositories. For an account-free local workflow, this integration loads the same
DINOv3 ViT-L/16 LVD-1689M weights from Hugging Face's public timm distribution,
using matching resized RGB input, normalization and final unweighted layer norm.
It uses the already available local U2Net background remover instead of BRIA.
There is no hosted inference, telemetry, user account or API key.

## Persistence, errors and export

Only one setup/generation runs at a time. Validated GLB v2 models, source PNG,
recipe (including actual engine/revision and requested resolution) and logs stay
in the app's 3D data directory. Setup errors leave `setup.log`; generation errors
leave a per-job `.log`. Failed jobs do not enter the gallery.
The viewer rejects external asset URLs and displays texture loading errors.
The Windows save dialog exports the original GLB bytes including embedded PBR
textures. No cloud conversion is involved.

Upstream: [Microsoft TRELLIS.2](https://github.com/microsoft/TRELLIS.2).

## Verified run — 2026-11-01

The installed Windows application uploaded the same samurai PNG extracted from
its supplied MP4 and generated model `46437208b3d141b5acee51d7f1e016b2` through
its real Generate button: seed 1, 24 steps, 1536³, CPU offloading, 4096 textures.
The worker checks its actual voxel size before export, so a lower-resolution
result cannot be accepted as 1536³. This first run took 359.0 seconds on the RTX
4080 SUPER 16 GB. It produced 982,163 triangles and an 87,383,812-byte GLB with
embedded 4096×4096 PNG base-color and metallic/roughness maps. No external model
resources were present. Native front/back previews displayed color without
texture errors; the authenticated export endpoint produced byte-identical output.
This is evidence for this image/settings, not a guarantee that every image fits.

WSL `agentops` uses a 20 GB RAM cap and 12 GB swap on D:, replacing its former
8 GB cap; Windows has 32 GB RAM. The isolated runtime uses NumPy 1.26.4 and
plyfile 1.1.2. DINOv3 strict state loading and both feature shapes were verified
on the real GPU before the complete pipeline. All four compiled support libraries
are pinned to the commits used for this run. The old v1 runtime and its separate
Hugging Face model cache were removed after the v2 result was verified; the v2
copy of the required shared sparse-structure decoder and all gallery files remain.
Generated models, screenshots and detailed logs are under ignored `.shots/trellis2`
or the application's private 3D data folder; none are committed.

## Progress, deletion and Web copies — 2026-11-01

Sampling emits live step counts; weighted stage progress reaches 100 only after
GLB validation/persistence. Decoding/export stages may hold their percentage
while working. Finished/failed jobs include elapsed time and publish `3d.job`.
Uploading a new reference or starting generation clears the previous preview;
only the matching successful job can select its new model. Failures retain the
reference and display a short actionable message, with traceback in the log.

Delete preview moves the original, Web copy and their metadata/logs to `.trash`
inside the 3D data folder. Undo restores them. Shared reference PNGs are retained.
This removes gallery entries without silently destroying previously exported files.

Original detailed exports the exact generated GLB bytes. Export for Web creates
a separate `.web.glb`, using a verified portable Blender 4.5.15 in WSL. It welds
seams, voxel-remeshes the reconstruction, decimates to a 25–30k triangle budget,
creates render UVs and bakes tangent normals, base color, roughness and metallic
from the original. Maps are 1K/2K and the GLB uses Draco and WebP. Original data
is never overwritten. The viewer bundles its Draco decoder and loads no CDN.

The supplied `samurai04.png` still exhausted 16 GB at 1536³ during shape decoding,
despite bounded FP32 normalization scratch buffers. At explicit 1024³, seed 1,
24 steps and 4K export it completed in 268.3s: 992,703 triangles, 92,459,260 bytes.
The 2K Web copy took 59.1s and produced 28,000 triangles in 3,944,024 bytes.
Embedded Normal Map/PBR maps, Draco/WebP and rendered previews were verified.
This graphic input includes strokes/effects that remain part of the reconstructed
shape; successful generation does not imply clean character-only topology.

## Studio

Studio replaces the Original/Web comparison at the owner's request. It uses one
large viewport and a selector for the original or Web copy. Generation settings
sit alongside it on wide screens and stack on smaller windows. Actual triangle
counts are computed from loaded geometry, alongside actual file sizes.

The preview height is adjustable with a drag handle or keyboard-accessible slider
and is saved locally. Expand fills the app window, keeps the same renderer/camera,
traps keyboard focus and exits with Escape. Photo/generation settings are collapsible
to leave room for the viewer. At the owner's request, initial UI
values prioritize maximum available detail: 1536³, 4096 textures, 50 steps and
30,000 Web triangles/2048 Web textures. API defaults remain independent. These
are available maxima, not a guarantee of quality or successful allocation on 16 GB.

The original detailed export remains the highest-fidelity generated geometry;
the Web copy intentionally simplifies it. Qwen prompt research and two untested
ready-to-copy scene specifications are in [3D_MASTER_PROMPTS.md](3D_MASTER_PROMPTS.md).

## Local Qwen 3D Coder runtime

The 3D page discovers the already-installed Qwen3.5 3D Coder from LM Studio's
inventory. It generates procedural Three.js HTML/JavaScript scenes from text,
not photo-to-GLB reconstructions. Generate scene alone starts inference through
the shared model gateway. It reloads only this model and explicitly passes
`--no-speculative-draft-mtp`; no tools or shell execution are provided to the LLM.
Three.js is bundled offline at build time. Generated HTML previews run in an
opaque sandbox iframe on a narrow separate loopback server with no API token,
no network permission, no navigation/forms/popups. HTML export embeds Three.js
and a restrictive CSP. Scene files persist independently of GLB gallery items.
The code generator reserves a 64K context and up to 32K output tokens, with one
parallel slot. Truncated HTML is continued up to three times and must close its
scripts/document before being marked completed. Exhausted attempts are saved as
partial recovery files rather than gallery successes. Existing incomplete scenes
can be completed into a new copy; their source remains unchanged. This verifies
document completeness; local Node, when available, also checks inline JavaScript
syntax without executing it. Neither check proves visual correctness. A sandboxed preview
reports script, resource-policy and rejected-promise errors to the UI. New completed
scenes open automatically. The scene preview has an adjustable height and expanded
mode that preserves the iframe.

On 2026-11-01 the owner explicitly authorized a real Qwen trial. Recovery of the
Cloud Sanctuary completed in 284 seconds (26,024 output tokens, including 7,871
reasoning tokens). Syntax checking passed, then the sandbox exposed a separate
initialization-order bug in the existing prefix: materials referenced procedural
textures before initialization, and those textures referenced an uninitialized
random source. The recovered copy was reordered manually; the original was kept.
The corrected 48 KB scene rendered in the installed Windows app. Light switching,
camera reset and preview resizing/expansion were exercised. This confirms a working
preview, not geometric fidelity to every requirement in the master prompt.

## 2026-11-02 — Gallery, thumbnails and Qwen repairs

The desktop layout places Preview and export on the left, the single model Studio
in the center, and Photo and generation settings on the right. Three columns start
at lg so the owner's interface zoom does not push settings below the viewer.
Narrow windows stack the panels. The gallery scrolls independently. The initial
Studio height is 400 px; a saved height preference takes precedence.

Gallery buttons include a rendered thumbnail and the short model identifier.
Visible thumbnails are generated sequentially from the actual original GLB using
the same viewer, cached as WebP images for this app session, then their geometry,
textures and WebGL context are released. Generated models and thumbnails remain
private application data, never public repository assets.

Qwen's prompt and scene list sit to the left of its preview. Selection is highlighted;
switching scenes reloads the iframe, and deleting the selected scene clears it.
Authenticated DELETE /api/3d/coder/{scene_id} removes only that scene's owned HTML,
metadata and partial file. Unlike TRELLIS deletion, scene deletion has no undo;
separately exported HTML files are retained.

When Node is available, generation checks inline script syntax with node --check.
A syntax error requests a full rewrite within the existing three-attempt budget.
Repair scene sends a sandbox-reported runtime error to the same local coder,
creating a new scene with parent_id while preserving its source. Redundant external
Three.js core script tags are removed from standalone previews/exports because
the app embeds its local runtime; the network policy stays restrictive.

The saved anime scene failed with a duplicate const localX declaration. Browser
and Node checks reproduced it. A manually corrected copy rendered without
JavaScript errors and was saved privately with its parent link; the original was
retained. No new real Qwen inference was run for this fix. Browser checks covered
the real GLB thumbnail, selection, switching and mocked scene deletion. The rebuilt
installed Windows app's layout was verified at the owner's actual zoom. Complete
project checks and the installer build passed. This is not a quality guarantee
for arbitrary generated scenes.
