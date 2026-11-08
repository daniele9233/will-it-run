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
| Wan 2.2 Fun-InP 5B Q6_K | Single QuantStack GGUF | WanFunInpaintToVideo | Same UMT5; **Wan 2.2 VAE** | 20 uni_pc/simple steps, CFG 6, shift 8, 24 fps |
| Wan 2.2 Fun-InP A14B Q4_K_M | QuantStack HighNoise and LowNoise pair | WanFunInpaintToVideo | Same UMT5 and Wan 2.1 VAE | 20 Euler/simple steps, CFG 3.5, shift 8, 16 fps |
| Wan 2.1 FLF2V 14B 720P Q4_K_M | Single city96 GGUF | WanFirstLastFrameToVideo; both pictures required | Same UMT5 and Wan 2.1 VAE; CLIP Vision H encodes **both** pictures | 20 uni_pc/simple steps, CFG 3, shift 8, 16 fps |

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

Native frames must be 4n+1, up to 361. Sizes are multiples of 16 (32 for Fun 5B); the UI adapts its choices.
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

All four profiles have the same final format in the UI: 1920 x 1080, 16:9, 30 fps,
6 through 15 whole seconds (180 through 450 output frames), H.264/yuv420p MP4 with
faststart and no audio. Generation defaults to 512 x 288 to limit memory; 768 x 448
and 1024 x 576 are selectable. The output is explicitly described as upscaled.
Lanczos scaling and a centered crop preserve aspect ratio; this is not native 1080p
or learned super-resolution. Higher native resolutions consume more memory.

The optional `duration_seconds` request field activates this pipeline. The service
derives `frames = seconds * profile.fps + 1` and uses that profile's native rate:
97-241 source frames at 16 fps, or 145-361 at 24 fps for Fun 5B. The entire duration
is generated as one continuous latent, with the user's original first/last pictures
conditioning the actual endpoints. No repeated clips, loops or five-second clip
stretched to fifteen seconds. Long generations cost more memory and may lose
coherence; accepting a tensor length does not establish a quality guarantee.
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

## Google Flow, browser-assisted

The Videos page also has a Google Flow panel, independent of ComfyUI. Choose two
local PNG/JPEG/WebP references and a prompt, then Prepare for Flow. Their copies
and exact prompt are kept under `videos/flow/<id>` in local app data. Open Flow,
upload those images as first/last frames, paste the prompt and generate with the
owner's existing included credits. Generation, login, model selection and spending
remain in Google's browser UI; no API key, cookies or Google credentials enter
Will It Run. There is no autonomous MCP/browser bridge in the desktop app.

Import the downloaded MP4 to extract 150 WebP frames locally, at up to 1280x720.
FFprobe validates a real video stream with duration 0.5–60 seconds; FFmpeg extracts
the sequence. The ZIP and manifest become available only after all 150 frames
exist. Imports are labelled Google Flow (user-imported), without inventing model
metadata. Original source files remain unchanged. Interrupted work is reported as
failed and can be imported again. The separate local Wan controls still work.
