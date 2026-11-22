# Video studio

The Videos page is embedded in Will It Run below Images. Upload of the keyframe pictures,
generation, playback, settings reuse, MP4 download and gallery all remain inside the app.
The same local ComfyUI engine and authenticated API as Images are reused, with no cloud account.

## Live generation progress (0.4.1)

The generation panel shows the same native progress bar as the 3D studio, with
the current phase, elapsed seconds and actual sampling steps. A WebSocket
observer reads ComfyUI events for this video's prompt ID only. Graphs with two
samplers (LTX-2.5's half-resolution pass and refinement) cover both; the
percentage never decreases. Decoding, encoding, saving, Full HD finishing and
validation have separate phases. Only a saved MP4 and its published recipe can
complete the bar at 100%.

These phase weights describe workflow completion, not a measured remaining
duration. While ComfyUI has not supplied progress, the bar is indeterminate
and the interface explicitly reports that remaining time is unknown. Losing
the progress socket does not fail or interrupt the HTTP generation job.
Cancellation and errors keep their final state and elapsed time visible.

## Text-to-Video

Select **MiniMax H3 FL2VA** or **LTX-2.5** in **Videos**, then choose **Text-to-Video**
under **Generation mode**. Describe the scene, motion and any sound in the prompt.

**MiniMax H3** uses the native [ComfyUI Text-to-Video workflow](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native)
with the existing FL2VA INT8 weights and empty AV latents. A plain prompt gets the
official T2VA fields, including soundscape and music. A structured prompt is passed
through. Its 20-step and 8-step Turbo recipes are both available.

**LTX-2.5** shares its existing distilled INT8 transformer,
Gemma encoder, VAEs and 2× latent upscaler are shared with keyframe generation. This uses
the layout of [Lightricks' two-stage Text-to-Video workflow](https://docs.ltx.io/open-source-model/usage-guides/text-to-video),
with the app's existing weights and sampler settings. The first pass starts from empty
video and audio latents. The second pass refines the upscaled video latent with the generated
audio. The prompt goes directly to the existing encoder.

The generation API accepts `input_mode: "text-to-video"` with `model_key: "minimax-h3-fl2va"`
or `model_key: "ltx25-keyframes"` and an empty or omitted image list. Existing recipes
default to `input_mode: "keyframes"`.
The gallery saves the mode so **Reuse settings** restores it along with the seed and prompt.

On 2026-11-22, a prompt-only API run on the RTX 4080 SUPER and ComfyUI 0.44 generated
five seconds at 1024 × 576. The Full HD export contained 150 distinct frames at 30 fps
and a generated audio track. Generation and export took 122 seconds in that run.

## Local keyframe profiles (since 2026-11-17)

Both profiles take the same input: an ordered list of **keyframe pictures**, each
pinned to a second of the output timeline. One picture is the first frame; two
are the first and last frame; three or more give one continuous shot (piano
sequenza, FPV one-take) that passes through every picture at its time, in one
generation, without cuts or stitched clips. Times default to an even spread from
0 to the end and can be set per picture (`VideoRequest.keyframe_times`).

| Profile | Files (D:\Models\video\…) | Conditioning | Recipe |
|---|---|---|---|
| MiniMax H3 FL2VA · keyframes · INT8 | `minimaxh3`: FL2VA pruned INT8 (20.97 GB), Qwen3-VL 32B NVFP4-AWQ encoder, video VAE fp16, audio VAE, FL2V Turbo 8-step v1.1 LoRA | First and last picture on `MiniMaxH3ImageToVideo` (the model's trained first/last-frame task); every picture in between on a chained `MiniMaxH3AddGuide` at its frame | 20 `res_multistep`/simple steps, BasicGuider (CFG 1), 24 fps, 17n+5 frames, 1344 × 768 native; optional Turbo: the 8-step LoRA and 8 steps |
| LTX-2.5 · keyframes · two-stage · INT8 | `ltx25`: distilled 22B INT8 transformer, Gemma 4 12B projection encoder, video and audio VAEs, x2 latent spatial upscaler | `LTXVAddGuide` per picture (strength 1.0 first, 0.6 inside, 0.9 last), in both stages; `LTXVCropGuides` before upsampling and before decoding | Half resolution with the published 8-step sigmas, `LTXVLatentUpsampler` x2, 3-step refinement (0.85, 0.725, 0.4219, 0), CFG 1, 24 fps, 8n+1 frames, 1280 × 704 |

Both are native ComfyUI 0.44 nodes: no custom node is installed, pinned or updated.
Both keep their generated audio in the MP4.

**MiniMax H3 prompt layout.** H3 was trained on a structured prompt. The core wraps a plain
description in the official layout of `MiniMaxAI/MiniMax-H3/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md`:
the alignment instruction ("Picture 1 (from Shot 1) aligns with the 0.00-second mark …;
Picture 2 … aligns with the S.SS-second mark") for first/last, first-only or last-only,
then `integrated_multimodal_description: [Shot 1] …`, `overall_soundscape:` and
`non_diegetic_music:`. Optional last lines `Sound: …` and `Music: …` fill the two audio
fields; a prompt that already contains `integrated_multimodal_description:` is sent as it is.
The text encoder sees only the first-frame and last-frame pictures; intermediate guides
are latent anchors only (as in the official Multiframe template).

**LTX-2.5 prompt.** The core prefixes the instruction LTX's own keyframe templates use
("Use the provided start image as the first frame, pass through the N provided
intermediate keyframe images in order and end exactly on the provided end image …")
unless the prompt already speaks of the provided start or end image.

**Why the inner LTX guides are 0.6.** On the reference drone sequence, strength 1.0 on the
inner pictures froze the camera on each picture and then jumped (hard-cut-like spikes);
0.6 let the camera fly through them with a maximum frame-to-frame change of about
3.5–4× the median, the same range as the reference video. First and last stay strong.

**Prompting a one-take.** Describe one shot, the camera moves in order (push in, dive,
arc, glide, slow down), say "no cuts", and give two very different pictures enough seconds
between them. Two pictures alone cannot tell a model the geometry in between: the more
spatially distant two keyframes are, the more the model invents.

### What was removed on 2026-11-17

At the owner's request the three previous profiles were deleted from the app and the disk:
LTX-2.5 + MSR + AssetGuide (reference images at negative times, not frame anchors),
Wan 2.1 VACE 14B Multi-Ref and MiniMax H3 Ref2VA-VSA (4-step Turbo plus a sparse-attention
gate). Deleted: `minimax_h3_ref2va_pruned_int8_convrot` (20.97 GB), the Ref2VA Turbo LoRA
(1.96 GB), the VSA gate (3.85 GB), the MSR V2 LoRA (2.22 GB), the partial Wan VACE 14B
download (4.26 GB) and the Wan 2.1 VAE, plus the custom nodes ComfyUI-LTX2.5-MSR,
ComfyUI-LTX-AssetGuide, ComfyUI-Ref2VA-VSA and ComfyUI-Tessiiiz-WanVideoToolkit, their
modules, manifests, routes and pins. Files shared with the new profiles were kept rather
than deleted and downloaded again: the LTX-2.5 transformer, encoder and VAEs (`ltx25`) and the
H3 encoder and VAEs (moved, with their verification markers, from `ref2va` to `minimaxh3`).
Saved gallery videos remain playable; reusing a removed model's recipe says that another
model must be chosen.

Why these two: the owner's work is first/last-frame transitions and FPV one-takes. H3 FL2VA
is the dedicated first/last-frame variant of the H3 family (the Ref2VA variant only uses
pictures as references and does not lock the ends), and native `MiniMaxH3AddGuide` adds
any number of anchors in between. LTX-2.5's native `LTXVAddGuide` does the same at a
fraction of the time and is the iteration model. Wan 2.2 Fun Camera/InP were considered: they
take a camera trajectory or two ends, not several anchors, and are an older generation.

## Workflow and validation

`imagegen/video.py` validates the request and binds the profile's graph from
`imagegen/minimax_h3.py` or `imagegen/ltx25.py`; `imagegen/keyframes.py` maps each time to
a frame (0 s is frame 0, the full duration is exactly the last frame) and rejects times that
are outside the clip, out of order or that land on the same frame. Native frames follow each
model: MiniMax H3 17n+5 and LTX-2.5 8n+1, both at 24 fps (10 s: 243 and 241 frames). Sizes are
multiples of 32 for H3 and of 64 for LTX (its first pass runs at half size). Steps, CFG and FPS
are the recipe's own; other values, a separate ending picture, a negative prompt and slow
motion are rejected rather than ignored. The seed is resolved before queuing. Every gallery
recipe keeps the model key, the ordered pictures, the keyframe times and frames, the seed,
resolution and Turbo flag.

`imagegen/video_catalog.py` names the exact files; profiles never fall back to a smaller or
different checkpoint. `imagegen/data/minimax_h3_assets.json` and `ltx25_assets.json` pin each
file's repository revision, byte size and SHA-256 (checked against the Hugging Face tree API).
`POST /api/videos/models/{model_key}/setup` (and `/cancel`) downloads what is missing with
HTTP Range resume, verifies size and SHA-256 before publishing and checks CUDA BF16; it installs
no source code. On engine start a separate app-cache YAML exposes `D:\Models\video\minimaxh3`,
`ltx25` and `relayvsr`; the owner's own extra_model_paths.yaml is untouched.

MP4 files and JSON recipes live in the app's videos data directory; AGENTFIT_HOME is honored.
Recipes are atomically published. Jobs run in background threads and do not resume after an app
restart; completed gallery files survive. The two-hour timeout and Cancel delete only this job
from the engine queue, interrupting the engine only if that prompt is running. Restarting the
shared engine affects image generation too, and the button only stops a process owned by the app.

## Official workflow references

- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_i2v.json (FL2VA, 20 steps or 8-step Turbo)
- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_multiframe_reference.json (chained Add Guide)
- https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native and https://docs.comfy.org/tutorials/video/minimax/minimax-h3-multiframe
- https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md
- https://huggingface.co/lightx2v/Minimax-h3-Turbo (FL2V Turbo v1.1 768p, 8 steps; v1.2 carries full-rank adaln deltas that do not fit the pruned checkpoint)
- https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_ltx2_5_flf2v.json and video_ltx2_5_i2v.json (two-stage)

## Live tests on this PC, 2026-11-17 (RTX 4080 SUPER 16 GB, 32 GB RAM)

The owner's test folder `reference-video-test/` (not committed) holds two jobs:
`auto-explosion` (an assembled 1967 fastback as first frame, its exploded view as
last frame) and `sequence-drone` (six keyframes: closed airplane window shade,
shade open on a tower above the clouds, aerial view of the city, close to the wavy
facade, street level, the entrance) with the target one-take `risultato.mp4`.
Results and contact sheets are in `reference-video-test/results/`.

"Cuts" below is the largest frame-to-frame change divided by the clip's median
change, measured on 96 × 54 grey frames. The target `risultato.mp4` peaks at 3.8×
(ignoring its screen-recording start); a hard cut is a single spike far above that.

| Run | Path | Size · frames | Time | Result |
|---|---|---|---|---|
| H3 FL2VA explosion, 20 steps | direct graph | 1344 × 768 · 158 | 986 s (46.8 s/step) | Clean, sharp, mechanical separation; the explosion finishes at about 3.3 s and then holds |
| H3 FL2VA drone, 6 keyframes, 20 steps | direct graph | 1344 × 768 · 243 | 37 min (108 s/step) | Through the window, over and into the clouds, out over the city, down the facade, street, entrance; peak 4.0×, no cut. Best quality |
| LTX drone, one stage, inner guides 1.0 | direct graph | 1280 × 704 · 241 | 161 s | Freezes on each keyframe then jumps: peak 16×. Rejected |
| LTX drone, one stage, inner guides 0.6 | direct graph | 1280 × 704 · 241 | 156 s | Continuous, peak 3.5×; facade descent smeared |
| LTX drone, two-stage, times 0/1.5/3.5/6/8.25/10, seed 7 | direct graph | 1280 × 704 · 241 | 106 s | Continuous, peak 3.7×, sharper than one stage |
| LTX explosion, two-stage, seed 7 | direct graph | 1280 × 704 · 145 | 65 s | Doors and panels open and separate into the exploded view; softer than H3 |
| LTX drone through the app | API, app-started engine, Full HD finishing | 1920 × 1080 · 300 at 30 fps | 576 s | Done: 300 distinct frames, audio kept, peak 3.7×, keyframes at frames 0/36/84/144/198/240 |
| H3 FL2VA explosion through the app, plain prompt with `Sound:`/`Music:` lines | API, app-built H3 layout, Full HD finishing | 1920 × 1080 · 180 at 30 fps (158 native) | 1018 s | Done: 180 distinct frames, audio kept, peak 2.6×; asking for an even pace moved the end of the separation from about 3.3 s to about 4 s |

"Direct graph" means the same node graph the app builds, queued straight into
ComfyUI from a test script; "through the app" means the isolated core from source
(`AGENTFIT_HOME` in a scratch folder), its own setup (which verified 44.4 GB for H3
and 39.7 GB for LTX without downloading them again) and the engine it started.

The first run through the app failed although ComfyUI finished: during the second
LTX stage the 20 GB transformer was staged again with 1.8 GB of RAM free, ComfyUI
did not answer HTTP for about 3.5 minutes and one history poll timed out. A read
timeout now counts as a busy engine and is tolerated for up to 15 minutes
(`ENGINE_SILENCE_LIMIT`); a refused connection still fails at once. The rerun
recorded that silence and completed. On 32 GB of RAM these 20 GB models run, but the
first run after a restart or after Free VRAM reloads them from disk and takes longer.

Measured afterwards, through the app, in the Scroll Heroes set (see Prompt collections):
the 8-step Turbo LoRA v1.1 on ten 6-second H3 transitions at 1344 × 768 took 453.5–457.0 s
each including Full HD finishing; LTX two-stage 8-second one-takes took 124.7–133.6 s and a
10-second one 453.9 s. Not measured yet: clips longer than 10 s, portrait keyframes, and the
H3 drone through the app (its 37-minute graph ran directly, as above).

## History (profiles removed before 2026-11-17)

The sections below describe earlier profiles and are kept as history.

### Earlier verification on this PC, 2026-10-30

The original Q6_K I2V pair produced an MP4 at 256 square, 5 frames and 4 steps in 46.4 s.
A separate first/last-frame graph using those same files produced 17 frames at 256 square,
8 steps, CFG 4 and shift 8 in 60.9 s. Decoded endpoint comparisons were closer to their intended
inputs than to the opposite input. These were synthetic clothed-portrait pipeline checks, not
quality benchmarks. The earlier MP4 was served through the API and decoded in the browser player.

## Full HD desktop hero output

The current profiles have the same final format in the UI: 1920 x 1080, 16:9, 30 fps,
5 through 15 whole seconds (150 through 450 output frames). Output is H.264/yuv420p
MP4 with faststart; LTX-2.5 and Ref2VA preserve generated audio as AAC, while Wan
VACE is silent. Defaults are 768 x 512 for LTX, 832 x 480 for Wan VACE and the
upstream 1344 x 768 canvas for Ref2VA.
Portrait inputs rotate the canvas orientation. The output is explicitly described
as upscaled. Lanczos scaling and centered padding preserve the whole image and
aspect ratio; this is not native 1080p or learned super-resolution. Higher native
resolutions consume more memory.

The optional `duration_seconds` request field activates this pipeline. The service
uses each profile's native rate and temporal alignment: 81-241 source frames at
16 fps for Wan VACE, 121-361 at 24 fps for LTX-2.5, and 124-362 at 24 fps aligned
to 17n+5 for Ref2VA. Native versus requested export timing remains explicit.
Reference images condition the subject or the timeline; none of the current
profiles is a first/last-frame model. Continuous generation uses one latent,
without repeated clips or loops. Older slow-motion recipes remain readable, but
the current reference profiles reject that control. Longer generations cost more
memory and may lose coherence; accepting a tensor length does not establish a
quality guarantee.
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


## Prompt collections (since 2026-11-18)

A prompt collection keeps finished videos together with everything that made them, so a
video can be made again or varied. The Videos page shows them in **Prompt collections**
(header shortcut): a list of videos, and for the selected one the player, the video prompt,
model, duration, generation size, Turbo, seed, generation time, the second each keyframe is
pinned to, notes, and every keyframe picture with its image prompt, workflow (text to image
or edit of another keyframe), image model, seed, steps and CFG, each with a Copy button.
**Use in the generator** hands the pictures to the engine again and fills the generator
with the same pictures, times, prompt, model, duration, size, seed and Turbo; nothing is
generated until Generate video is pressed.

A collection is a folder `<app data>/videos/collections/<id>/` holding `collection.json`, the
MP4 of each entry and its keyframe pictures (`imagegen/video_collections.py` validates the
JSON; the folder name must equal its `id`; file names never leave the folder; a missing file
is shown as missing, never replaced). Routes: `GET /api/videos/collections`, and under
`/api/videos/collections/{collection_id}/entries/{entry_id}`: `GET file` (MP4, Range),
`POST save`, `GET keyframes/{index}/file`, `POST use`. The app reads collections; it does not
write them.

**Scroll Heroes** (20 videos, 2026-11-17) is the first collection. It was assembled by
`D:\render-work\scroll-heroes\tools\export_collection.py` from records only: image prompts,
seeds, steps and CFG from the generation rows of the app that made the pictures (each PNG
matched by SHA-256), video prompts and settings from the gallery recipes, titles and "edited
from" links from `spec.py`. Where the final video differs from the plan (17-museum used three
keyframes, 18-sprinter five), the recipe wins.

How those videos were made, to do it again:

- **Keyframe pictures:** Qwen-Image 2.1 UC, 1344 × 768, 25 steps. For a before/after pair,
  generate the harder picture from text and make the other one as an edit of it, so object,
  angle and volume stay identical. Edits run at CFG 2.5 (the model's `kind_sampling`).
- **Transitions (first and last frame):** MiniMax H3 FL2VA, Turbo 8 steps, 6 s: about
  7.6 minutes each. Prompt: what changes, in order, camera move, then `Sound:` and `Music:`.
- **One-takes (FPV, 4–5 keyframes):** LTX-2.5 two-stage, 8–10 s, about 2 minutes each (10 s:
  7.5 minutes). Every keyframe must already show the next destination in its centre distance;
  pictures of unconnected places give split-screen collages (LTX) or hard cuts (H3). Prompt:
  "One continuous FPV drone shot with no cuts.", the camera moves in order, the pace, the sound.


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

## History: MiniMax H3 Ref2VA-VSA (removed 2026-11-17)

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
full-size cache. LTX-2.5 and Wan VACE use the same lifecycle through
`POST /api/videos/ltx25/setup` and `POST /api/videos/wan-vace/setup` (with their
`/cancel` endpoints). Each setup installs its pinned custom nodes first and then
downloads the model files. `GET /api/videos/status` includes setup progress and
runtime checks for all three profiles.
Starting or restarting the shared engine loads the nodes and the new model paths.

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
FLF form required both images; at that time Ref2VA exposed one reference image.
Since 2026-11-11 it exposes up to nine, each with its own preview and remove
button, and the saved recipe keeps their order.

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
