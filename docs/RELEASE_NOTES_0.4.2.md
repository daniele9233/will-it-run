# Will It Run 0.4.2

Local reference-video generation and existing-video restoration, with verified
model setup, explicit input modes and measured GPU results.

## What's new

- Add MiniMax H3 **Ref2VA-VSA** through the pinned Kablex ComfyUI extension and
  its own four-step Turbo workflow. Preserve generated video and audio, expose
  VSA sparsity, and use the model's actual 17n+5 frame grid at 24 FPS.
- Add **RelayVSR 2x LightX2V and 4x** to the Videos page. Restore a gallery video
  or local video file, choose native scale, 1080p or UHD 4K, select an interval
  and adjust detail strength. Results have their own gallery and native save.
- Download pinned model files with published sizes and SHA-256 verification,
  cancellable/resumable transfers and a disk-space reserve. GPU libraries remain
  in the external ComfyUI environment.
- Show actual task phases and frame progress. Keep generation and restoration
  in one video task slot; reserve 100% for a verified, saved result.
- Preserve anamorphic display geometry and source frame order, measured average
  FPS and audio. Explicitly normalize irregular timestamps and join output
  segments on a continuous clock.
- Bound 4K RAM with coarse temporal context, then trim matching latent keyframes
  and images before Flash decoding so context is not decoded repeatedly.
- Clarify input modes in English and Italian: Wan 2.2 I2V accepts a starting frame
  with optional ending guidance; Wan 2.1 FLF2V requires first and last frames;
  Ref2VA uses one subject reference without locking the endpoints.
- Remove Google Flow and both Wan Fun-InP profiles. Keep Wan 2.2 I2V A14B Q6_K,
  Wan 2.1 FLF2V 14B 720P Q4_K_M and the existing 3D tools. Saved videos remain
  readable after retiring their generation model.
- Update translations, runtime notices, studio documentation, project decisions,
  lessons and the generated code map.

## Verified on the maintainer's PC

Windows 11, RTX 4080 SUPER 16 GB, 32 GB system RAM:

- Ref2VA generated a five-second Full HD MP4 with audio in 153.7 seconds,
  including finishing and validation.
- Both RelayVSR models processed the same **whole** 312-frame, approximately
  13-second anamorphic video with audio. Every output retained 312 frames,
  average FPS 11700/487, 16:9 square-pixel display geometry and an audio track.

| RelayVSR model | Output | Whole-job time | Processing FPS |
|---|---|---:|---:|
| 2x | 1920x1080 | 55.08 s | 5.66 |
| 4x | 1920x1080 | 52.27 s | 5.97 |
| 2x | 3840x2160 | 307.85 s | 1.01 |
| 4x | 3840x2160 | 258.00 s | 1.21 |

Sampled whole-GPU usage peaked at 11,762 MiB, including desktop usage. These
measurements include preparation, loading/inference, encoding and verification;
they do not establish real-time performance. Source bytes were unchanged.
The detailed test record is in [VIDEO_STUDIO.md](VIDEO_STUDIO.md).

The project checks cover backend tests, eight import contracts, Ruff, TypeScript,
ESLint, 162 frontend tests, translations and generated maps. CI and the release
workflow repeat the complete checks on clean Windows runners.

## Windows installation and video prerequisites

Download `WillItRun-0.4.2-Setup-x64.exe` and its `.sha256` from this release.
The installer is not code-signed; Windows SmartScreen may request confirmation.

ComfyUI is a separate local runtime. RelayVSR needs its native RelayVSR nodes,
CUDA BF16 and the comfy-kitchen CUDA `window_attention` backend. Ref2VA-VSA
needs native MiniMax H3 support and CUDA `sol_attn`; upstream recommends 24 GB
VRAM, while the specific local 16 GB configuration above was successfully tested.
The interface reports missing prerequisites before queueing work. FFmpeg and
FFprobe are used locally for video preparation, audio and output verification.

RelayVSR's two models and matching decoders require 10.58 GB; Ref2VA's six
components require 48.28 GB. Model weights are downloaded separately. The
installer and source repository contain no model weights, private application
data, input videos or generated media. Machine-specific model cleanup on the
maintainer's PC is recorded separately in the project documentation.
