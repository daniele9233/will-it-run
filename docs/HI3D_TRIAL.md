# Hi3D geometry comparison — 2026-11-03

The owner authorized a real local TRELLIS.2 versus TRELLIS.2 + Hi3D comparison,
then selected `Altalena sospesa tra cielo e mare.png` in the repository root.
Its SHA-256 is `e644c7f201ddefc28a4176ab4678bf69863f3140edcd744e309afb8bae3c52e3`.
The photo and all generated media remain private, uncommitted files. This trial
does not add Hi3D to the product's 3D page or replace the installed TRELLIS engine.

## Reproducible inputs and runtime

- Windows RTX 4080 SUPER, 16,376 MiB VRAM; WSL distribution `WillItRun`.
- Hi3D source: `13657ad09afaa92e350b461a1cd66399e44a59c4`.
- Hi3D weights: `d502e938cbedc6e87aba8884614c5ca6d177a4b6`.
- DINOv2-large: `47b73eefe95e8d44ec3623f8890bd894b6ea2d6c`.
- CLIP ViT-L/14: `32bd64288804d66eefd0ccbe215aa642df71cc41`.
- Public assets: 10,656,141,286 bytes; byte sizes and SHA-256/Git blob checks
  verified against the publishers' metadata before offline inference.
- Separate `~/.local/share/willitrun-hi3d` source/model directory and venv overlay.
  It shares existing PyTorch 2.6.0+cu124 and the TRELLIS compiler read-only;
  FlashAttention 2.7.4.post1, spconv-cu120 2.3.6 and Transformers 4.46.3 are in
  the overlay. This is not the exact upstream PyTorch 2.5 measured stack.
- Telemetry disabled; no hosted inference, user account, or API key.

The exact manifest, scripts, logs, intermediate meshes and receipts are under
ignored `.shots/hi3d/`. Setup/download scripts and model hashes are retained there.
The application's original 3D gallery and installed worker were not modified.

## Baseline and comparison protocol

The normal U2Net foreground extraction keeps cloud, ropes, person and seat,
excluding sky and sea. Both models receive that same foreground; their own
documented image-conditioning framing is retained.

TRELLIS.2 at 1024, seed 1 and 50 steps exhausted GPU memory during geometry
decoding. Row-chunked pointwise MLPs and eviction of recomputable neighbor caches
were tried in a private worker; 1024 still failed. Saved latents allowed the
last decoding attempt without resampling. These failures are not successful
gallery assets and do not establish that every input fails on 16 GB.

The explicitly reduced baseline uses the installed TRELLIS.2 shape pipeline at
512, seed 1 and 50 steps, without a texture pass. The geometry-only worker takes
90.88 seconds including loading. A prior direct-call prototype accidentally
retained autograd graphs; that failed attempt is an implementation error, not
evidence about hardware fit.

The raw decoder produced 31,939,956 triangles. Export preparation uses the same
CuMesh/O-Voxel narrow-band DC remesh as the app's export: resolution 512,
band 1, project-back 0, followed by simplification. Raw Z-up coordinates become
glTF/Hi3D Y-up through `(x, y, z) -> (x, z, -y)`.

The detailed control contains 974,786 triangles; the roughly 60k input for Hi3D
contains 56,302. The latter is a decimation of the completed baseline, **not** an
independent fast coarse generation; no end-to-end speedup is claimed.

Hi3D is requested at 1024, seed 1, 25 Heun scheduler updates per pass, with the
upstream sequential CPU offload. Each completed extraction is retained before
the normal component filtering and final export.

Clay renders remove every original material and use identical lighting,
camera, shading and orthographic frame. For presentation each mesh is centered
on its bounding box and uniformly scaled to a shared maximum extent; no rotation,
nonuniform scaling or shape fitting is applied. The offline Three.js comparison
uses the same normalization and one synchronized camera for all panels.

Triangle counts, file sizes, connectivity and finite coordinates describe the
assets; none is a score of reconstruction fidelity. Views of unseen surfaces
cannot establish correctness against this single photograph.

## Observations before refinement

The real baseline clay render already shows a rectangular backing slab and
relief-like geometry. The slim ropes and person are imperfect. This complex
surreal scene is not a clean single-object photograph. Keep these errors in the
control rather than removing them manually and attributing the correction to Hi3D.

## Completed Hi3D result and visual assessment

Hi3D completed both passes and returned the requested 1024 resolution:

| Measurement | Actual result |
| --- | --- |
| Wall time, including loading | 430.55 seconds |
| Pipeline inference, including intermediate extraction snapshots | 402.56 seconds |
| Final triangles / vertices | 999,999 / 497,723 |
| Final GLB | 17,973,468 bytes |
| Whole-card peak sampled every second | 9,517 MiB (about 9.29 GiB) |
| PyTorch peak allocated memory | 7,884.18 MiB |
| Peak process RSS sampled every second | 16.19 GiB |
| GLB SHA-256 | `72549de9b47173966b4b767ea9ba3de44aa4e2cd077f4df9e44a9b71192698a0` |

The raw extraction snapshots contain 1,437,920 triangles at the first pass and
7,672,544 at the second, before upstream final postprocessing. Actual files and
receipts are under `.shots/hi3d/`; no CPU or GPU out-of-memory occurred in the
completed Hi3D run. Eight recorded warnings are spconv AMP deprecations.

Front, three-quarter, side, back, cloud-detail and swing-detail clay renders were
generated and inspected. On this particular input, Hi3D **does not demonstrate
a useful quality improvement over the detailed TRELLIS control**:

- It retains the incorrect backing slab and the relief-like global structure.
- It hallucinates repeated vertical ribs, rim details and fence-like features on
  that slab; these are not present in the reference photograph.
- The cloud becomes smoother and more rounded, losing some fine surface detail
  compared with the detailed TRELLIS control.
- The person/seat remain approximate; this is not a reliable reconstruction of
  the reference's suspended swing or its thin ropes.
- Neither final mesh is watertight. Neither is certified for printing, rigging,
  animation, or geometric correctness on unseen sides.

This establishes a working local refiner on the owner's GPU for this case, not
that it repairs incorrect initialization or works better on every image. Do not
enable Hi3D as the product's default enhancement on the strength of this trial.
At this initial assessment, no 1536 Hi3D trial, texture pass, or product UI
integration had been performed. The later integration is recorded below.

Deliverables are `.shots/hi3d/confronto-geometria.png`, `confronto-dettagli.png`,
`confronto-retro.png`, the self-contained offline `confronto-3d.html`, and the
three GLBs `trellis-clay.glb`, `coarse-60k.glb`, `hi3d-1024.glb`. The browser loaded
all three real models with matching triangle counts, no console errors and no
external HTTP resources. A separate Playwright session verified side/reset
buttons, wireframe on every model, keyboard rotation and stacked layout at
720 pixels, then captured `.shots/hi3d/viewer-verified.png`. Static rendering is
the visual evidence for the geometry assessment. `python -X utf8 scripts/check.py`
passed; it selected no source checks because only documentation changed.

## Follow-up: optional Hi3D inside the application

The owner subsequently requested an actual product integration for their own
tests, without scene decomposition. The 3D page now exposes local setup and
selected-model refinement, explicit 512/1024/1536 resolution, steps, seed, input
triangle budget and CPU offloading. Results are separate gallery entries with
the original/reference retained, GLB saving, and a neutral geometry comparison
using a shared camera. See `3D_STUDIO.md` for behavior and provenance.

A real job launched by the UI on this photograph completed at 1024, 25 steps per
pass, seed 1 and 60,000 input triangles. It used a pyfqmr-simplified copy of the
974,786-triangle control, rather than the initial trial's CuMesh coarse mesh.
The output contains 999,999 triangles and 499,650 vertices, with a 17,996,596-byte
GLB. Recorded wall time was 598.5 seconds; the GPU worker's measured processing
time was 521.9 seconds. PyTorch peak allocated memory was 6,951,373,824 bytes;
that figure is not whole-card memory. The source SHA-256 remained unchanged.

The browser opened both real gallery models, reported matching triangle counts,
rotated their shared camera, and exported a byte-identical result through the
gallery button. Comparison lighting was corrected after visual inspection to
avoid clipping the neutral material; the final evidence is
`.shots/hi3d/app-comparison-final.png`. Full repository checks passed. The frozen
installed core separately reported Hi3D ready. Windows UI automation was blocked
by the desktop tool's product policy, so native-window interaction was not
verified. No GPU run at 512 or 1536 was added by this integration check.
