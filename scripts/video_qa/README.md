# Video quality pipeline

Local QA tooling, separate from the product UI. Outputs, prompts, job IDs, PNGs,
MP4s, browser sequences and JSON reports stay in ignored `.shots/video-qa/`.
No generated assets belong in Git. No downloads of models or quantization fallbacks.

## Coverage

1. Scroll: Qwen BF16 origin, edit of that origin, First/Last video, 300 WebP samples.
2. Spin: identical First/Last images, 360° prompt, 120 WebP samples, seam and
   neutral-background foreground centroid/area/clipping measurements.
3. Geometry: single-image orbit, forward/backward Lucas–Kanade tracking of the
   **initial** features through every decoded frame, plus fundamental-matrix RANSAC.
   FLF-only models are explicitly unsupported here; an ending image is never silently added.
4. Stress: two First/Last generations per model with different reproducible seeds.
   Failure, timeout cancellation and backend-restart handling also have fake-client tests.

The default four-model matrix has 20 entries: 19 executable videos and one
unsupported I2V entry. Jobs run sequentially to avoid GPU contention. Each successful
video retains its actual backend recipe, timings and native/output settings.

## Quality contract and limits

- Images: installed `qwen-image-2.1-uc-bf16` diffusor, INT8 text encoder, PNG originals,
  requested 2048 resolution, 2048×1152, 50 steps, CFG 2.5. This is the locally installed
  BF16 profile, **not an entirely unquantized pipeline**. More steps are not proof of
  greater image quality. No lossy image re-encoding precedes upload.
- Videos: existing four catalog profiles, native 1280×704 (1280×720 for the 720p FLF model), 60 sampling steps,
  native family fps, 8 seconds; existing finishing yields 1920×1080, 30 fps,
  240 frames. The 720p FLF case explicitly uses 33 native frames slowed to the requested
  eight seconds; other profiles keep continuous native generation. This is **upscaled/interpolated Full HD**, not native 1080p or lossless MP4.
  The backend accepts native dimensions only up to 1280. It deletes the native temporary
  clip after finishing, so this suite diagnoses the published video, not both stages.
- Strict FFmpeg full decode, FFprobe frame timestamps, decoded frame count, duration,
  dimensions, fps. Uneven timestamps are a drop/discontinuity signal, not proof of
  how many latent frames the generator produced.
- Endpoint MSE, local-window SSIM, exact-pixel identity (after explicitly recorded
  resizing when needed). SSIM ≥0.95 is a configurable-in-code screening threshold;
  it does not mean exact identity. Lossy MP4 generally cannot preserve identical pixels.
- Black frames: >98% pixels below luma 8. Adjacent duplicates: exact equality at
  640×360. Flicker candidates: adjacent mean-luma change >20. These are heuristics;
  lighting changes can trigger false positives and localized artifacts can escape.
- Spin centroid drift >0.1 of image dimensions, foreground area ratio >1.5 or clipping
  fails screening. Neutral-background segmentation is approximate. These metrics
  cannot establish that the object completed a true rigid 360° rotation.
- Geometry: initial-track survival <15% fails screening. Occlusion/reflections and
  legitimate viewpoint changes can also lose tracks. Good tracking alone cannot certify
  3D rigidity, calibrated poses or reconstructability. COLMAP and Gaussian training
  are **not evaluated** by this pipeline; no `.splat` or trained `.ply` is fabricated.
- Browser samples are resampled, never counted as extra generated motion: a 300-frame
  sequence from a 240-frame video necessarily repeats some source samples.

## Execution

Dependencies are listed in `requirements.txt`; FFmpeg/FFprobe must be on PATH.
The current installation was prepared with `uv pip install --python
backend/.venv/Scripts/python.exe -r scripts/video_qa/requirements.txt`.

From the repository root:

```powershell
# Use a dedicated backend when testing; do not point AGENTFIT_HOME at owner data.
$env:AGENTFIT_HOME = "$PWD\.shots\video-qa\app"
backend/.venv/Scripts/willitrun.exe serve --port 8765
# In a separate process (set AGENTFIT_TOKEN only for an authenticated backend):
backend/.venv/Scripts/python.exe -X utf8 scripts/video_qa/run.py
```

The live scratch backend was configured with `images.timeout_seconds=10800` before
the restarted edit. The default 900-second image deadline can be insufficient for
2048-resolution BF16 editing; changing it after a job starts does not extend that
job. The cancelled first edit is preserved in `timeout-restart.json` and
`ending-cancelled.json`.

`--preflight` records installed components without generating. `--timeout` defaults
to three hours per job; the video backend itself limits generation to two hours.
On client timeout only its own job is cancelled and the suite stops, avoiding a
second GPU job while cancellation is pending. Backend terminal failures are recorded
and the remaining model cases continue. Image generation failure blocks the video
matrix because the required reference assets do not exist.

Rerunning with the same output directory reuses reference assets, saved job IDs and
terminal cases; it never resubmits them silently. If a backend restart lost an active
job, use a **new** output directory for an explicit fresh run. Completed cases do not
run again automatically. After a partially completed frame export, analyze into a new
directory: stale frames are deliberately refused. A process crash between submission
and saving its returned job ID cannot be resumed safely without inspecting the engine.

Offline analysis of one existing MP4:

```powershell
backend/.venv/Scripts/python.exe scripts/video_qa/diagnose.py video.mp4 .shots/video-qa/manual --scenario scroll --first origin.png --last ending.png
backend/.venv/Scripts/python.exe -m unittest discover -s scripts/video_qa -p 'test_*.py'
python -X utf8 scripts/check.py
```

Nonzero exit means a failure or coverage gap, including unsupported cases. JSON reports
distinguish `failed`, `blocked`, `unsupported`, and `passed_screening`.

## Browser fixtures

The viewer uses locally installed GSAP/ScrollTrigger and
[@mkkellogg/gaussian-splats-3d](https://github.com/mkkellogg/GaussianSplats3D)
(API verified against its README and installed 0.4.7 source). It is an inspection
boilerplate; the Gaussian viewer still needs a real trained scene.

```powershell
cd scripts/video_qa/viewer
npm ci
npm run dev -- --port 5177
```

The dev-only `/runs/` route points to `.shots/video-qa/run/`. Load, for example,
`/runs/scroll-wan22-i2v-q6-0/manifest.json`. Spin supports horizontal pointer drag,
touch, wraparound and keyboard arrows. ScrollTrigger pins the canvas, scrubs through
the sequence and updates story overlays. The frame slider remains available with
reduced motion. Images are fetched with six workers and only eight decoded images
are retained. Failed loads are visible. Gaussian loading has a 200 MB fixture budget,
explicit format, failure display and viewer/object-URL cleanup.

The production build does not include private run assets or the dev-only run route.
Keep the preview bound to loopback. `npm run build` validates the viewer bundle.
