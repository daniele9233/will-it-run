# Video studio

The Videos page is embedded in Will It Run below Images. Generation, upload of the starting and
ending pictures, playback, settings reuse, MP4 download and gallery all remain inside the app.
The same local ComfyUI engine and authenticated API as Images are reused, with no cloud account.

## Live generation progress (0.4.1)

The generation panel shows the same native progress bar as the 3D studio, with
the current phase, elapsed seconds and actual sampling steps. A WebSocket
observer reads ComfyUI events for this video's prompt ID only. Two-expert Wan
profiles cover both samplers; the percentage never decreases. Decoding,
encoding, saving, Full HD finishing and validation have separate phases.
Only a saved MP4 and its published recipe can complete the bar at 100%.

These phase weights describe workflow completion, not a measured remaining
duration. While ComfyUI has not supplied progress, the bar is indeterminate
and the interface explicitly reports that remaining time is unknown. Losing
the progress socket does not fail or interrupt the HTTP generation job.
Cancellation and errors keep their final state and elapsed time visible.

## Local profiles

| Profile | Diffusion files | Conditioning | Other components | Defaults |
|---|---|---|---|---|
| Wan 2.2 I2V A14B Q6_K | Original HighNoise and LowNoise Q6_K pair | WanImageToVideo with one picture; WanFirstLastFrameToVideo with two | UMT5 XXL FP8, Wan 2.1 VAE | 20 Euler/simple steps, CFG 3.5, 16 fps; shift 5 with one picture, 8 with two |
| Wan 2.1 FLF2V 14B 720P Q4_K_M | Single city96 GGUF | WanFirstLastFrameToVideo; both pictures required | Same UMT5 and Wan 2.1 VAE; CLIP Vision H encodes **both** pictures | 20 uni_pc/simple steps, CFG 3, shift 8, 16 fps |
| MiniMax H3 Ref2VA-VSA INT8 | Pruned Ref2VA INT8 model | MiniMaxH3ReferenceToVideo; one visual reference, not a first/last-frame constraint | Qwen3-VL 32B NVFP4 AWQ; video and audio VAEs; Turbo LoRA and VSA gate | 4 Euler/simple steps, BasicGuider (CFG 1), 24 fps, video/audio shifts 12/3, VSA sparsity 0.75 |

The model selector and selected-model description state which input each model
accepts. I2V animates a starting frame with optional final guidance. FLF2V is the
dedicated first/last-frame option and requires both pictures. Ref2VA uses one
subject reference; it does not promise that image will be the first video frame.
Its ending-image, negative-prompt, CFG and slow-motion controls are unavailable
or fixed to the actual upstream recipe. Unsupported API combinations are rejected.

At the owner's request on 2026-11-08, Google Flow and both Fun-InP profiles were
removed. The three Fun-InP GGUFs and the 5B-only Wan2.2 VAE were deleted from
D:\Models and from their verified NTFS hard links in ComfyUI: 24,951,591,008 model
bytes across four underlying files, plus their local verification markers. Shared
Wan encoders and the two retained Wan profiles were preserved. Saved gallery
videos remain playable; reusing a removed model's recipe explains why another
available model must be selected first. Older dated comparisons below are history.

`imagegen/video_catalog.py` specifies the exact filenames. Profiles never fall back to a smaller
or different checkpoint. `imagegen/data/video_assets.json` records pinned source commits, exact
byte sizes, SHA-256 and source URLs, verified against the publisher's Hugging Face tree and HEAD.
The originals live in D:\Models. VAE and vision dependencies have role-specific subdirectories
`video/vae` and `video/clip_vision`. Existing shared components can be hard-linked there after
verification, without duplicating bytes. GGUF files are not complete until their download hash
matches and the .part file is renamed to the final name.

On engine startup, a separate app-cache YAML adds the D:\Models diffusion directory and only
existing role-specific dependency directories. The owner's extra_model_paths.yaml is untouched.
For the engine already running on this PC, same-volume hard links expose the same files in its
usual model folders without restarting it or copying the weights.

## Workflow and validation

`imagegen/video.py` binds the profile to a ComfyUI API graph. There are one or two samplers
according to the actual model. In two-expert profiles the first stage preserves leftover noise
and the second continues without adding fresh noise. The ending image is uploaded independently
and passes through its own LoadImage node. Wan 2.1 also uses a separate CLIP vision encoding of
each endpoint. The 5B VAE cannot be replaced with the 14B model's VAE.

Wan native frames must be 4n+1, up to 361. MiniMax H3 uses 17n+5 at 24 fps,
including 124 frames for the approximately five-second upstream example and 362
for a 15-second request. Sizes are multiples of 16 for Wan and 32 for Ref2VA;
the UI adapts its choices and displays the actual aligned frame count.
The seed is resolved before queuing. Every gallery recipe preserves model key, both input names,
resolved seed, resolution, fps, steps and CFG. Old gallery entries remain readable.
The profile defaults are suggestions, not measured hardware-fit or speed guarantees. Begin with
small generation sizes. Native 720P model naming does not promise 720P will fit a 16 GB card.

MP4 files and JSON recipes live in the app's videos data directory; AGENTFIT_HOME is honored.
Recipes are atomically published. Jobs run in background threads and do not resume after an app
restart; completed gallery files survive. The two-hour timeout and Cancel delete only this job
from the engine queue, interrupting the engine only if that prompt is running. Restarting the
shared engine affects image generation too, and the button only stops a process owned by the app.

## Official workflow references

- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_flf2v.json
- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_5B_fun_inpaint.json
- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_fun_inpaint.json
- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/wan2.1_flf2v_720_f16.json

Optional acceleration LoRAs are not loaded; their four-step recipes do not apply to these base models.

## Earlier verification on this PC, 2026-10-30

The original Q6_K I2V pair produced an MP4 at 256 square, 5 frames and 4 steps in 46.4 s.
A separate first/last-frame graph using those same files produced 17 frames at 256 square,
8 steps, CFG 4 and shift 8 in 60.9 s. Decoded endpoint comparisons were closer to their intended
inputs than to the opposite input. These were synthetic clothed-portrait pipeline checks, not
quality benchmarks. The earlier MP4 was served through the API and decoded in the browser player.

## Full HD desktop hero output

The current profiles have the same final format in the UI: 1920 x 1080, 16:9, 30 fps,
6 through 15 whole seconds for Wan (180 through 450 output frames), or 5 through
15 for Ref2VA (150 through 450). Output is H.264/yuv420p MP4 with faststart; Wan
is silent, while Ref2VA preserves its generated audio as AAC. I2V defaults to
832 x 480, FLF2V to 1280 x 720, and Ref2VA to the upstream 1344 x 768 canvas.
Portrait inputs rotate the canvas orientation. The output is explicitly described
as upscaled. Lanczos scaling and centered padding preserve the whole image and
aspect ratio; this is not native 1080p or learned super-resolution. Higher native
resolutions consume more memory.

The optional `duration_seconds` request field activates this pipeline. The service
uses each profile's native rate and temporal alignment: 97-241 source frames at
16 fps for continuous Wan generation; 124-362 at 24 fps, aligned to 17n+5, for
Ref2VA. Native versus requested export timing remains explicit. Wan endpoint
images condition the first/last frames; Ref2VA images condition the referenced
subject. Continuous generation uses one latent, without repeated clips or loops.
The separately selected Wan Slow motion mode is described below. Longer
generations cost more memory and may lose coherence; accepting a tensor length
does not establish a quality guarantee.
Legacy requests without duration_seconds and existing gallery files remain readable.

`imagegen/video_finish.py` runs the installed local FFmpeg and FFprobe (PATH or
its standard per-user Windows location), without a shell or network.
Motion-compensated interpolation (`minterpolate`, mci/aobmc/bidir, scd=none) computes
intermediate images at 30 fps before enlargement. Timestamps map both original
endpoints to output frames 0 and N-1. Two padded lookahead frames only feed the
filter's buffer; export stops before them and uses passthrough timestamps rather
than an encoder frame-duplication setting.

Before publication, FFprobe checks resolution, actual decoded frame count, fps and
duration. Decoded frame hashes must contain no adjacent repetitions. A stationary
or otherwise repeated result is rejected with an actionable movement prompt message,
not silently accepted as distinct frames. These checks measure format and frame
identity, not perceptual motion quality; interpolation can produce artifacts.
The recipe records both native settings and measured final output separately.
Finishing and verification are cancellable, kill only their own child process,
and have a two-hour timeout each. Temporary files are cleaned on cancellation or
failure; no partial video is added to the gallery. All processing stays on this PC.

## Full HD live verification, 2026-10-30

Each of the four profiles generated a 15-second continuous clip from both endpoint
photos at 512 x 288, seed 42, four steps (pipeline smoke test, not quality benchmark).
The final decoded MP4s all measured 1920 x 1080, 30 fps, exactly 450 frames, 15 seconds,
450 distinct frame hashes and zero adjacent repetitions. Total generation plus
finishing times: original I2V Q6 261.8 s; Fun 5B Q6 78.0 s; Fun A14B Q4 242.8 s;
Wan 2.1 FLF Q4 233.8 s. Synthetic moving fixtures also verified 6 seconds / 180 distinct
frames and 15 seconds / 450 distinct frames. Higher native resolutions and normal
20-step image quality have not been benchmarked by these checks.


## Photo selection and MP4 saving

Photo inputs are hidden behind large, keyboard-accessible buttons with ImagePlus icons,
separate starting/ending labels, uploaded filenames and previews. Clearing an input after
selection lets the same file be picked again; cancelling a picker preserves the current photo.

The desktop Save video button uses the existing native Save dialog and an authenticated
POST /api/videos/gallery/{video_id}/save endpoint, following the Images saving flow.
The backend copies the existing MP4; saving does not regenerate, transcode, move or remove it.
Destinations must be absolute, in an existing directory and have .mp4 extension (added when
absent). Errors are shown and success includes the saved path; cancelling performs no copy.
The browser keeps its own authenticated download flow. WebView2's blob download link alone
is not used as a desktop save implementation.


## Saved video prompts

Each gallery video displays its positive and negative prompts in separate selectable,
read-only text areas, with individual Copy buttons. Empty negatives are explicitly shown
as not saved. Existing gallery recipes already contain this text; no migration is needed.
The existing clipboard helper is reused. If automatic copying is refused, the text is
selected and the UI tells the person to press Ctrl+C. The generation fields accept paste.

Save prompts is available below the generation text fields and on the selected gallery
video. A name is required, and both exact texts (including whitespace and Unicode) are
saved together. The Saved video prompts section is on the same Videos page, with a header
shortcut, search, inspection, copying, reuse and deletion. Use these prompts fills only
the positive/negative fields and focuses the generation form; photos, model, seed and
sampling settings stay under the person's control. Loading does not generate a video.

Authenticated GET/POST /api/videos/prompts and DELETE /api/videos/prompts/{prompt_id}
use imagegen/video_prompts.py. Each pair has a generated UUID, name and timestamp in
app-data videos/prompts/*.json. Writes are atomic and malformed records are skipped.
The library works without ComfyUI and survives process restarts and gallery deletion.
Prompt text and names never become paths. Tests use temporary directories and no GPU.


## 2026-10-31 — Visual-quality corrections after live reference tests

The original two live exports passed format checks but failed visual acceptance.
The portrait reference was center-cropped by Wan conditioning, losing head and feet.
The FLF 720p model was sampled at 512x288; severe RGB shapes, deformation and trails
were already present in the native ComfyUI MP4 before interpolation.

Both endpoints now pass through the core ResizeAndPadImage node with Lanczos and
black padding before conditioning. Portrait uploads choose portrait native dimensions.
Finishing contains the whole generated image within Full HD using padding, never crop.
The default native size is 832x480 (480x832 for portrait), not 512x288. The 720p-only
FLF profile defaults to 1280x720 and Full HD requests below that pixel area are rejected.
The UI removes its low-resolution options for that model and adapts all other presets
to the selected model multiple; reusing a tiny old recipe raises it to the current
minimum instead of quietly repeating that recipe's bad quality settings.

Decoding uses VAEDecode, matching the official ComfyUI templates, rather than forced
16-frame temporal tiles. ComfyUI handles its own memory-aware fallback. These changes
remove concrete pipeline errors; they do not certify rigid geometry or exact endpoint
pixels. Quantizations remain explicit and unchanged. Eight-second native clips still
cover the whole interval, not a shorter animation stretched to eight seconds.

Reference: https://docs.comfy.org/tutorials/video/wan/wan-flf explicitly warns against
small native sizes for the 720p model. The downloaded official workflow confirmed
20 uni_pc/simple steps, CFG 3, shift 8 and VAEDecode, so sampling was not changed
blindly to unrelated settings. Generated results require a fresh visual comparison.


### Slow motion for short endpoint transitions

Long native sequences can hold the initial object until the final frames. A live
comparison with the same references improved the distribution of separation by
using the official FLF 720p recipe with 33 native frames and retiming them to the
requested eight-second export. This is explicitly slow motion, not a claim that
the model generated eight seconds at its native 16 fps.

VideoRequest.motion_frames is optional (4n+1, 5..361). With duration_seconds it
selects the native sequence length; export still uses duration_seconds*30 frames.
Without it, continuous generation retains the previous duration*native_fps+1 rule.
Selecting the 720p FLF profile chooses 33 frames in the visible Slow motion control;
other profiles use 81 for a short transition. Uploading an ending image enables
that visible mode; it can be disabled for continuous motion. Recipes preserve both
native_motion_seconds and export_seconds with an explicit timing mode.

The native workflow for the new duration/motion_frames combination is tested equal
to the successfully rendered raw native workflow; retiming is done by the same
verified finishing function. Endpoint geometry is still judged visually: these
models are not a CAD renderer and fine parts can deform.

## MiniMax H3 Ref2VA-VSA

Source: <https://github.com/Kablex/ComfyUI-Ref2VA-VSA>, pinned to
`323a0a5f7c636cc70f95464145732f81f04a72e9`. The Python core binds the author's
API workflow in `imagegen/ref2va.py`; it never imports torch or the custom node.
The existing ComfyUI runs the model, Turbo LoRA, gate transplant, sigma shift,
SamplerCustomAdvanced, video VAE and audio VAE. The existing gallery, cancellation,
prompt storage and live progress panel also serve Ref2VA. Progress recognizes
the custom sampler and both decode nodes. The generated audio is retained when
finishing the MP4, retimed with the video and checked for the requested duration.

The six sources, immutable revisions, sizes and SHA-256 hashes live in
`imagegen/data/ref2va_assets.json`. The README's original Comfy-Org repository URL
no longer answers; the verified current publisher repository is
<https://huggingface.co/Comfy-Org/MiniMax-H3>. All six pinned download URLs answered
HTTP 200 with the expected byte lengths. Their total size is 48,280,301,087 bytes.
Files live under `D:\Models\video\ref2va`, separated by diffusion_models,
text_encoders, vae and loras. A launch-only extra-path configuration exposes these
folders without changing the owner's ComfyUI YAML.

`POST /api/videos/ref2va/setup` explicitly installs the pinned node in the detected
ComfyUI and downloads the weights. `POST /api/videos/ref2va/setup/cancel` cancels;
verified downloads can resume with HTTP Range. Files are published only after size
and checksum checks; partial files and checksum mismatches never make the model
Ready. Direct-to-disk downloads retain the safety reserve without a duplicate
full-size cache. `GET /api/videos/status` includes setup progress and runtime checks.
Starting or restarting the shared engine loads the node and the new model paths.

Upstream recommends 24 GB VRAM and reports its timings on an RTX 4090. This PC
has an RTX 4080 SUPER with 16 GB. The UI reports the recommendation and actual
detected memory, labels smaller GPUs experimental, and checks CUDA BF16 and the
comfy-kitchen CUDA `sol_attn` capability. These checks establish prerequisites,
not an inference-speed or fit guarantee. The six local downloads and pinned source
were verified successfully; hardware generation results are recorded separately.

### Live verification on this PC, 2026-11-08

The local installer built successfully and was applied to the installed app.
The real installed WebView showed exactly the two retained Wan entries plus
Ref2VA, with their Italian input-mode descriptions and no Flow panel. The shared
engine reported all three profiles ready, with no missing nodes or files. The
FLF form required both images; Ref2VA exposed one reference-image input and fixed
4-step/CFG-1 controls.

One generation was submitted through that WebView using the extension's clothed
portrait example and a plain clothed-portrait movement prompt. RTX 4080 SUPER
16 GB, seed 42, sparsity 0.75, 4 steps; native portrait canvas 768 x 1344,
124 frames at 24 fps. Job `baf87b3f8fd4429d8dbf6445ff9dd9b6` completed in
153.7 seconds including finishing and validation. The persisted result measured
1920 x 1080, 30 fps, 5 seconds, 150 distinct frames, zero adjacent repetitions
and retained generated audio. The player loaded and sought the result; an
inspected frame showed the clothed subject with the intended portrait padding.
Observed progress reached decoding at 82% and completed at 100% only after the
MP4 and recipe were published. This is one end-to-end pipeline measurement,
not a general speed or quality ranking.

The complete project checker passed, including backend tests, all eight import
contracts, Ruff, TypeScript, ESLint, 161 frontend tests and both translation
catalog checks. Development images and model weights remain outside Git.

## Existing-video restoration: RelayVSR

RelayVSR is a separate upscaling panel in Videos. It processes a gallery video or
an explicitly selected local MP4/MOV/MKV/WebM/AVI file, preserving the original
file. It does not replace the three generation profiles. Outputs are new MP4s in
`videos/upscaled`, with recipes, a separate gallery, native Save dialog and
explicit deletion. Generation and upscaling share one task slot, cancellation
and the same progress component. Decode progress reports actual restored frames;
100% requires both a verified MP4 and its published recipe.

### Models and external runtime

The requested files and their matching Flash decoders are pinned in
`imagegen/data/relayvsr_assets.json` to Comfy-Org/RelayVSR revision
`4aa28ada1785a03e9355c28f989b9ed3fdcb9f8e`. All four immutable URLs answered HTTP
200 with their published sizes, and downloaded files passed SHA-256 verification.

| Component | Bytes |
|---|---:|
| relayvsr_x2_lightx2v_int8_convrot.safetensors | 5,163,295,248 |
| relayvsr_x4_int8_convrot.safetensors | 5,164,179,952 |
| relayvsr_flash_vae_x2_fp32.safetensors | 127,759,464 |
| relayvsr_flash_vae_x4_fp32.safetensors | 127,759,784 |
| **Total** | **10,582,994,448** |

They live under `D:\Models\video\relayvsr`, in diffusion_models and vae folders.
The launch-only model-path configuration exposes them to ComfyUI without changing
the owner's YAML. The verified downloader is shared with Ref2VA, including the
disk reserve, HTTP Range resume, cancellation, checksum checks and atomic markers.
No ML dependencies or model weights enter the application core or installer.

The existing clean ComfyUI checkout was updated from `6b747c04` (v0.38.0) to the
official commit `0decb39ee5b5e17c200334589fc317dbbe98310e`, which includes native
RelayVSR support (ComfyUI PR 17511). Its pinned requirements were installed in
ComfyUI's own environment, including comfy-kitchen 0.2.41 and comfy-aimdo 0.6.0.
Torch 2.12.1+cu130 was already present. Runtime checks verify CUDA BF16 and the
CUDA `window_attention` capability. Ref2VA's custom-node checkout is preserved.
Both RelayVSR scales and the three existing generators reported ready.

The workflow follows the official LoadVideo / GetVideoComponents / UNETLoader /
RelayVSRConditioning / one-step Euler KSampler (CFG 1) / matching VSRDecode /
CreateVideo / SaveVideo graph. No text encoder or prompt is required. The x2
model is always paired with the x2 decoder, and x4 with x4. The Detail control
scales the decoder's residual, as implemented by the native node.

### Geometry, timing and resource handling

Native scale doubles or quadruples coded width and height and retains the source
pixel aspect ratio. Explicit 1080p and UHD 4K presets prepare the model's input
at the corresponding lower resolution, using the source **display** aspect ratio
and square-pixel padding. This distinguishes anamorphic pixels from square pixels;
a 720x576 file marked 16:9 must not be squeezed into a 5:4 picture.

The source's frame count and average FPS are measured. Irregular timestamps are
explicitly normalized to that average rate while frame order is retained; this
is not motion interpolation. Final encoding regenerates continuous timestamps
after concatenation so fractional rates are not changed by segment rounding.
Source audio is kept and encoded as AAC over the selected video interval.

An interval that fits the available-RAM budget is processed as a whole. Otherwise
15-frame output windows retain up to 45 preceding frames for the coarse model,
aligned with RelayVSR's 15-frame keyframe cadence. Native TrimVideoLatent and
ImageFromBatch remove that context **before** Flash decoding: at most 61 LQ frames
and 16 newly decoded frames are held per window. Context is not decoded repeatedly,
and every requested frame is published once in order. Recipes record the choice
and its reasons. Native outputs beyond the 4K pixel budget require an explicit
preset; insufficient RAM or disk reserve is reported rather than silently changing
the requested model or output.

The pipeline verifies the actual output dimensions, decoded frame count, rational
average FPS, sample aspect ratio and audio presence. A changed source, failed
checksum, incorrect result or cancellation cannot publish a successful gallery
entry. Temporary input/output files are scoped to the owned task.

Authenticated API: `GET /api/videos/upscale/status`,
`POST /api/videos/upscale/setup`, `POST /api/videos/upscale/setup/cancel`,
`POST /api/videos/upscale`, `GET /api/videos/upscale/gallery`, and file/save/delete
operations under `/api/videos/upscale/gallery/{video_id}`. The existing
`POST /api/videos/jobs/{job_id}/cancel` also cancels these jobs.

### Whole-file verification with the owner's test.mp4, 2026-11-08

The owner supplied `test.mp4` in the repository root and requested using that file.
Measured input: 720x576 coded pixels, SAR 64:45, 16:9 display aspect, 312 frames,
average FPS 11700/487 (approximately 24.025), approximately 12.99 seconds of video,
with audio and irregular original timing. The input's SHA-256 was unchanged after
testing. All four tests processed the **entire** file, not a short excerpt.

| Model | Output | Whole-job time | Measured processing FPS | Frames |
|---|---|---:|---:|---:|
| RelayVSR 2x | 1920x1080 | 55.08 s | 5.66 | 312 |
| RelayVSR 4x | 1920x1080 | 52.27 s | 5.97 | 312 |
| RelayVSR 2x | 3840x2160 | 307.85 s | 1.01 | 312 |
| RelayVSR 4x | 3840x2160 | 258.00 s | 1.21 | 312 |

All outputs retained average FPS 11700/487, 16:9 square-pixel geometry and an
audio track. Both 4K jobs used 21 bounded windows and completed without GPU
out-of-memory errors. Whole-job times include preparation, loading/inference,
encoding and verification. CUDA was used on the RTX 4080 SUPER 16 GB; sampled
whole-GPU usage peaked at 11,762 MiB (about 11.49 GiB, including desktop usage).
The minimum observed available system RAM was about 3.05 GiB. These measured
rates are below this clip's playback rate: real-time operation was not reached.

The same decoded frame index (150) was inspected in the source and both 4K
outputs at a common display size: geometry and colors were intact and edges were
more defined. There is no reference-quality score or claim that lost detail was
recovered exactly. Copies with readable names, comparison frames and the JSON
report are in ignored `.shots/relayvsr-test/`; results also remain in the app's
upscaling gallery. The input and outputs are not committed.

Disk verification began with 43.64 GB free on D. The four weights required 10.58
GB, retaining the 30 GiB model-drive reserve. During the whole-file tests, sampled
free space remained at least 32.77 GB. After exporting the four comparison copies,
the generated Rust release cache was inspected with `cargo clean --dry-run` and
cleared with `cargo clean --release --locked --offline` (2.1 GiB recovered).
The installed app and completed installer in dist were retained; D then had
approximately 33.95 GB free. Ruff, eight import contracts, backend tests,
TypeScript, ESLint, 162 frontend tests and translation checks passed, and the
updated local NSIS installer was successfully applied to the installed app.
