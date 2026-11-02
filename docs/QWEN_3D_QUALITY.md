# Qwen 3D quality trial — 2026-11-01

The owner requested a new, more detailed Japanese garden in the same stylized
direction as Cloud Sanctuary, with research into the author's repository first.

## Primary sources checked

- [Model card](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder), repository revision
  `196c03ff49d8d99f49235ad19c7d48f940bc7523` at inspection.
- [Technical report](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/Qwen3.5-4B-3D-Coder_Technical_Report.pdf),
  28 October 2026, especially Appendix B.
- [Training dataset](https://huggingface.co/datasets/ccore/ThreeJS-3D-Scenes), including
  `scene-003268` (temple garden complex), `scene-002466` (pavilion beside a pond),
  and `scene-002437` (flowering cherry tree). These are training references, not
  independent evaluations. The new scene is generated from an original prompt.
- [Published generation defaults](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/generation_config.json).

The model card recommends temperature 0.6–0.8, top-p 0.90–0.95, top-k 20–40 and
repetition penalty 1.0, with MTP disabled in LM Studio. Its system prompt uses
Three.js core r160 and custom controls. Will It Run embeds core r187 as global
`THREE`, so the prompt must adapt the import contract to the offline host.

Appendix B evaluates Q8_0 with temperature 0.6, top-p 0.9, top-k 20, min-p 0,
repetition penalty 1, a 65,536-token context, 32,768-token reasoning budget and
64,512-token generation limit. Attempt seeds are 1729 through 1733. These are
an exploratory protocol, not proven universal optimum settings. Up to five attempts
and three local syntax/identifier repairs were allowed. The report separates
successful rendering from spatial and visual quality.

## Local trial

The installed Q8_0 file is 4,482,402,816 bytes with SHA-256
`c30b02629456aa2bc32e522de0f58eb1aafd8518a7634128fed67d1486fc2a9a`, matching
the author's published file. Inventory size can include the companion projector.

This experiment uses the existing model gateway with explicit MTP-off and one
parallel slot. Sampling is 0.6 / 0.9 / 20 / repetition 1 / seed 1729. The generation
ceiling is 64,512 with conservative input headroom (61,326 output tokens for the
initial prompt). Min-p and a separate reasoning budget are not overridden by this
trial; it does not claim exact reproduction of Appendix B. Existing application
defaults are unchanged. Original prompts, raw responses and trial artifacts live
in ignored `.shots/qwen-quality`, not the public repository.

The prompt prioritizes continuous opaque terrain, physically joined supports,
closed roof construction, a coherent three-object composition, instanced blossoms,
controlled dusk lighting and responsive framing. This is an art-direction hypothesis
to inspect visually, not an established model-quality guarantee.

## Previous-scene finding

Cloud Sanctuary created arrays for island geometry but left its meshes with empty
`BufferGeometry` objects. Runtime success did not expose the missing surfaces.
Future visual checks should inspect empty/non-finite geometry as well as runtime
errors, and review multiple angles. The earlier original is preserved.

## Results and selected scene

Two genuine local Qwen generations were completed: seed 1729 in 278 seconds
(24,031 output tokens, including 8,071 reasoning tokens), and seed 1730 in 303.8
seconds (25,983 output tokens, including 7,963 reasoning tokens). Both finished
with `stop` and complete documents. The first needed three identifier/property
repairs. The second needed additional API, array-index and terrain-math repairs;
it does not meet the report's three-repair acceptance budget. Neither is described
as an unmodified one-shot success.

The selected result is **SAKURA — Atelier Edition**, derived from the first scene.
Codex refinements include a denser small-blossom crown, corrected texture tinting,
balanced fill light, grounded path, bridge landing steps, hanging lanterns, timber
brackets, brass fittings, functioning light/motion toggles and stable responsive
camera framing computed from bounds on both axes. This is Qwen generation followed
by code repairs and artistic refinement, not a pure model-quality benchmark.

Final browser inspection: 430 meshes, approximately 128,189 instanced triangles,
no empty meshes, non-finite vertex positions or out-of-range geometry indices.
Day/night round trip, pause, orbit, zoom and camera reset passed with no JavaScript
runtime errors. Both landscape and portrait were checked. At 640×900, the projected
scene bounding box remains inside the frame (maximum absolute normalized x 0.904,
y 0.549). This is visual/interaction evidence, not a mobile performance benchmark.
Project checks also passed. Raw outputs, repaired copies, refinements and screenshots
remain separately preserved in ignored trial storage.
