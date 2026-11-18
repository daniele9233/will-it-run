# Will It Run 0.4.3

Keyframe video: first frame, first and last frame, or one continuous shot through
up to nine pictures, with two new local models, prompt collections and a fix for
photo edits with Qwen-Image.

## What's new

- **Two keyframe video models replace three reference profiles.** MiniMax H3
  FL2VA (highest quality, 20 steps or an 8-step Turbo draft) and LTX-2.5 keyframes
  (two-stage with the x2 latent upscaler, fast iterations), both on native ComfyUI
  0.44 nodes: no custom node is installed or pinned any more. LTX-2.5 MSR/AssetGuide,
  Wan 2.1 VACE Multi-Ref and MiniMax H3 Ref2VA-VSA were removed with their weights,
  nodes, routes and manifests. Saved gallery videos stay playable.
- **Keyframes pinned to seconds.** One picture is the first frame, two are the first
  and last frame, three or more give one continuous shot (FPV one-take) through every
  picture at its time, in one generation, without cuts or stitched clips. The Videos
  page orders the pictures, sets the second of each or spreads them evenly, and
  rejects times outside the clip, out of order or on the same frame.
- **MiniMax H3 prompts are laid out for you** in the official FL2VA format (frame
  alignment, multimodal description, soundscape, music); optional `Sound:` and
  `Music:` lines fill the audio fields. LTX-2.5 prompts get the keyframe instruction
  of its own templates.
- **Prompt collections.** Finished videos are kept with every prompt that made them:
  each keyframe picture with its image prompt, workflow, seed, steps and CFG, plus the
  video prompt and settings. **Use in the generator** loads an entry back with its
  pictures, times, prompt, model, duration, size, seed and Turbo.
- **Qwen-Image photo edits run at CFG 2.5.** Qwen-Image 2.1 UC generates at CFG 1,
  and the edit workflow inherited it: at CFG 1 an edit ignored its instruction and
  filled flat backgrounds with noise. A model can now carry its own sampling per kind
  of workflow; the Images page switches the CFG when moving between Create and Edit
  and keeps the other values you set.
- **A busy engine is not a dead engine.** With 20 GB video models on 32 GB of RAM,
  ComfyUI can stop answering for minutes while it loads weights and still finish.
  A read timeout is now tolerated for up to 15 minutes; a refused connection still
  fails at once.
- Pinned, verified downloads for both models (size, SHA-256 and revision from the
  Hugging Face API), with resume and cancellation. The generated code map now splits
  the HTTP routes into one card per routes file.

## Verified on the maintainer's PC

Windows 11, RTX 4080 SUPER 16 GB, 32 GB system RAM, through the application
(Full HD 30 FPS finishing with audio):

| Run | Time |
|---|---:|
| LTX-2.5, 10 s one-take through 6 keyframes | 576 s (models reloaded from disk) |
| MiniMax H3 FL2VA, 6 s first/last frame, 20 steps | 1,018 s |
| MiniMax H3 FL2VA Turbo, 6 s first/last frame, 10 videos | 453.5–457.0 s each |
| LTX-2.5, 8 s one-take through 3–5 keyframes, 9 videos | 124.7–133.6 s each |
| LTX-2.5, 10 s one-take through 5 keyframes | 453.9 s |
| Qwen-Image 2.1 UC edit at 1344 × 768, default CFG now 2.5 | 95 s including loading |

The 6-keyframe H3 drone one-take (10 s, 20 steps, 1344 × 768) ran in 37 minutes as
the same graph queued directly. Continuity was measured as the largest
frame-to-frame change over the median: 3.7–4.0× for the drone one-takes, against
3.8× for the reference video and 16× for a version with hard jumps. Twice ComfyUI was silent
for about four minutes during these runs and the job completed. The detailed record
is in [VIDEO_STUDIO.md](https://github.com/daniele9233/will-it-run/blob/v0.4.3/docs/VIDEO_STUDIO.md).

Not measured: clips longer than 10 s and portrait keyframes.

## Windows installation and video prerequisites

Download `WillItRun-0.4.3-Setup-x64.exe` and its `.sha256` from this release.
The installer is not code-signed; Windows SmartScreen may request confirmation.

ComfyUI is a separate local runtime (tested with 0.44); both video models use its
native nodes and need an NVIDIA GPU with CUDA and BF16. Their verified files are
44.4 GB (MiniMax H3 FL2VA with its encoder, VAEs and Turbo LoRA) and 39.7 GB
(LTX-2.5 with its encoder, VAEs and upscaler); files the two share with earlier
profiles are reused rather than downloaded again. FFmpeg and FFprobe are used
locally for Full HD finishing. Model weights are downloaded separately. The
installer and source repository contain no model weights, private application
data, input pictures or generated media.
