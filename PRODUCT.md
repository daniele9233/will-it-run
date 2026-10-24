# Will It Run: product context for design work

register: product

## What it is

A Windows 11 desktop app that answers "what AI models can my PC actually run well?" and
then "what local AI team can this PC support?". It inspects hardware, recommends and
installs models through LM Studio, benchmarks them, and builds LangGraph agent teams
that run locally.

## Who uses it, and where

A local-AI enthusiast or developer at a desktop with a discrete GPU, usually in the
evening, with LM Studio and a terminal open on a second monitor. They know what VRAM
is; they may not know what a KV cache is. They are checking numbers and making
decisions that cost disk space and time: which 10 GB file to download, whether a team
will fit in 16 GB.

That scene is why the default theme is dark: the app sits next to a terminal and a
model runner for hours. Light and System themes exist for people who work otherwise.

## Principles

1. **Simple on the surface, powerful underneath.** The dashboard uses plain words
   ("Excellent", "Limited"). Expert mode exposes the exact file, quantization, KV cache
   and offload ratio. Same data, different depth.
2. **Every automatic choice explains itself.** Wherever Will It Run decides something there
   is a "Why?" with concrete numbers.
3. **Never fabricate.** Unknown is shown as Unknown; estimates are labelled with their
   confidence; measured replaces estimated.
4. **Numbers are the content.** Tabular figures, units always shown, meters with the
   recommended limit marked.
5. **Local-first, and it shows.** Live results versus cached is always stated.

## Design language

Dark, technical, minimal, dense without clutter. Inspired by the general language of
LM Studio, Linear, VS Code and Docker Desktop; no assets or layouts copied.

- Color strategy: restrained. Chroma-0 near-black surface, a cool second neutral for
  panels, one configurable accent (default steel blue, hue ~222).
- Semantic status color, used only for meaning: green perfect/healthy, accent
  active/selected, yellow warning, orange limited, red incompatible/error, gray
  unavailable.
- One type family (Segoe UI Variable), a mono for identifiers and code, fixed scale.
- Rows and lists over card grids. No nested cards. No decorative gradients or glass.
- Motion conveys state only: 150 to 220 ms, exponential ease-out, reduced-motion honored.
- Inline panels before modals. The command palette (Ctrl+K) and the YAML viewer are the
  only overlays.

Tokens: `desktop/src/styles.css`. Primitives: `desktop/src/components/ui.tsx`.
