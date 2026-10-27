# Third-party notices

Will It Run includes material derived from the following project. Runtime dependencies
installed from PyPI, npm and crates.io are distributed under their own licenses and are
not listed here.

## Cloning kit (not bundled)

Clone a site fetches one project from GitHub on the user's PC, at one pinned commit, with Git
(`backend/src/agentfit/clone/kits.py`). Will It Run does not ship its files.

- **ai-site-cloner**: <https://github.com/Mahanaicoach/ai-site-cloner>, commit `ad6189bf3394`. MIT, by
  Mahanaicoach. Its Next.js project (scripts, components, configuration) is copied into the folder the user
  chooses; its `clone-website` skill is given to the model as its author wrote it, followed by a note on how the
  app's tools map to the ones the skill names.
- **Playwright** (`playwright-core`, Apache-2.0, Microsoft) is installed from npm into the app's data folder and
  drives the user's own Chrome or Edge.

## llmfit

- Source: <https://github.com/AlexsJones/llmfit>
- License: MIT
- Copyright (c) 2026 Alex Jones
- Commit studied: `1a4e9a093a4ce1b26cca35431e7bba537f8b01dd`

### Derived components

| Will It Run file | Derived from (llmfit-core) | Nature |
|---|---|---|
| `backend/src/agentfit/fit/quant.py` | `src/models.rs`: `quant_bpp`, `quant_speed_multiplier`, `quant_bytes_per_param`, `quant_quality_penalty` | Tables ported to Python and generalized to arbitrary quantization labels |
| `backend/src/agentfit/fit/memory.py` | `src/models.rs`: `estimate_memory_gb_with_kv`, `kv_cache_gb` | Formulas ported; extended with real file sizes, vision projector and parallel slots |
| `backend/src/agentfit/fit/speed.py` | `src/fit.rs`: `estimate_tps`, `dense_cpu_offload_tps`, `moe_tier2_params`, `default_moe_overhead` | Estimation model and constants ported and simplified |
| `backend/src/agentfit/hardware/gpu_tables.py` | `src/hardware.rs`: `gpu_memory_bandwidth_gbps`, `gpu_compute_capability` | Lookup data, Windows desktop GPUs only |
| `backend/src/agentfit/hardware/windows.py` (registry VRAM query) | `src/hardware.rs`: `WINDOWS_REGISTRY_VRAM_PS_COMMAND` | PowerShell approach adapted into a combined probe |
| `backend/src/agentfit/catalog/data/seed_catalog.json.gz` | `data/hf_models.json` | Filtered subset (GGUF text/vision models, selected fields), built by `scripts/build_seed_catalog.py` |
| `backend/src/agentfit/catalog/data/family_scores.json` | `data/use_case_benchmarks.json` | Data, with one documented modification |

### MIT License (llmfit)

```
MIT License

Copyright (c) 2026 Alex Jones

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## llama.cpp

`backend/src/agentfit/fit/accuracy.py` contains the perplexity figures and file sizes
that the llama.cpp project publishes for each GGUF quantization in
`tools/quantize/quantize.cpp` (`QUANT_OPTIONS`, measured on Llama-3-8B). They are
reproduced as reference data, with the source named wherever the interface shows them.

- Project: https://github.com/ggml-org/llama.cpp
- License: MIT. Copyright (c) 2023-2026 The ggml authors.

The MIT License text above applies, with that copyright notice.

## Design inspiration

Will It Run's interface takes inspiration from the general design language of tools such
as LM Studio, Linear, VS Code and Docker Desktop: dark, dense, panel-based. No logos,
icons, artwork, layouts or other assets from those products are used. The Will It Run mark
is original. Interface icons are from [Lucide](https://lucide.dev) (ISC License).

## Trademarks

LM Studio, Hugging Face, NVIDIA, AMD, Intel, Windows and other names are trademarks of
their respective owners. Will It Run is not affiliated with or endorsed by any of them.

## Agent skills (not bundled)

The skills of a coding agent are fetched from GitHub on the user's PC, at one pinned commit each, with Git
(`backend/src/agentfit/clone/skills.py`), and kept with the licence of their authors under the app's data folder.
Will It Run does not ship their files; a run copies the ones it uses into the project it builds, for the model to read.

- **greensock/gsap-skills**, commit `aed9cfd32777`: MIT, GreenSock.
- **anthropics/skills** (`frontend-design`), commit `dbd4588f9e10`: Apache-2.0, Anthropic.
- **vercel-labs/agent-skills** (`react-best-practices`, `web-design-guidelines`), commit `6ec269328bc4`: MIT, Vercel.
- **cesartevisual/threejs-skills**, commit `84f9bcb4bea1`: MIT.
- **microsoft/playwright-cli** (`playwright-cli`), commit `b1a5903c5b63`: Apache-2.0, Microsoft.
