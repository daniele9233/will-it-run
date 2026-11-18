# Will It Run 0.4.4

A tidier Videos page: dropdowns instead of long columns, and prompt collections that
show one video at a time.

**Not built.** The Windows build of this release stopped on a test of the generated
code map, so no installer was attached. The same changes ship in
[0.4.5](RELEASE_NOTES_0.4.5.md).

## What's new

- **Video gallery: a dropdown instead of a column of prompts.** The gallery used to
  list every generated video as a full-width button under the player. It is now one
  **Video to show** dropdown next to the title. Each entry carries its number, the date
  and time it was made, its seconds and the opening of its prompt, so videos whose
  prompts start with the same words can still be told apart.
- **Saved video prompts: a dropdown too.** The search box filters the dropdown; the
  chosen pair is shown in full with **Use these prompts** and **Delete saved prompts**.
- **Prompt collections, one video at a time.** A **Collection** and a **Video of the
  collection** dropdown replace the stacked collections and their side lists; the
  video last shown in each collection is kept when you switch. The description folds
  under **About this collection**. The player sits beside a fact sheet (model,
  duration, resolution, seed, generation time, keyframe seconds), the notes and
  **Use in the generator**. The keyframes are a row of thumbnails: the chosen one is
  shown large, with how it was made and its image prompt.
- **Keyframes cut without a model** (a collection keyframe whose workflow ends in
  `_crop`) read "Crop of picture N" instead of "Edit of picture N", and leave out the
  seed, steps and CFG line instead of showing three unknown values.

## Verified on the maintainer's PC

The page was exercised in the browser against the core running from source on a
scratch data folder with 19 generated videos and two collections (Home Journeys and
Golden Estates): choosing a gallery video changes the player, prompt and settings;
switching collection and video changes the detail; a thumbnail shows its picture and
prompt; the saved-prompt search filters the dropdown. TypeScript, ESLint, the Vitest
suite and the Italian catalog pass. Nothing changed in the core, its routes or its
data.

## Windows installation

Download `WillItRun-0.4.4-Setup-x64.exe` and its `.sha256` from this release.
The installer is not code-signed; Windows SmartScreen may request confirmation.
Video and image generation need ComfyUI and model weights as described in the
[0.4.3 release notes](RELEASE_NOTES_0.4.3.md); nothing about them changed.
