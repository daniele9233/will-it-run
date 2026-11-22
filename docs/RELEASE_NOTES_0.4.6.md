# Will It Run 0.4.6

MiniMax H3 FL2VA and LTX-2.5 now offer **Text-to-Video** in **Videos → Generation mode**.
Enter a prompt to generate a video with audio from scratch. Select **Keyframes** to use
images. Both modes share the same installed models and the same gallery.

- **MiniMax H3 FL2VA** uses the native ComfyUI text-only conditioning path with either
  20 steps or the existing 8-step Turbo setting. The prompt can include `Sound:` and
  `Music:` lines.
- **LTX-2.5** uses the existing two-stage INT8 workflow without attaching images.
- **Reuse settings** restores the generation mode and seed. Older clips and prompt
  collections retain their keyframe mode.

The Windows x64 installation bundles the desktop executable and its Python core.
ComfyUI and the video weights are managed separately. The installer and checksum
are attached when the Windows release build completes.
