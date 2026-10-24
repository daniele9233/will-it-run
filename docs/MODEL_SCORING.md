# Model fit and scoring

Code: `backend/src/agentfit/fit` (fit) and `backend/src/agentfit/catalog/scoring.py` (score).

## 1. Memory

For one model file at one context length:

```
total = weights + KV cache x parallel slots + runtime overhead (0.5 GB)
        + vision projector + draft model
```

| Term | Source | Confidence |
|---|---|---|
| Weights | The real file size from Hugging Face or LM Studio. Only when unknown: `params x bytes/param(quant)` | file = high, estimate = medium |
| KV cache | Exact when layer geometry is known: `2 x layers x kv_heads x head_dim x ctx x bytes`. Otherwise `0.000008 x params_B x ctx` GB | "exact" / "approximate" shown in the UI |
| Vision projector | The repo's `mmproj-*.gguf` size, else a 0.9 GB estimate | |
| Parallel slots | KV cache multiplied by the number of concurrent sequences | |

Bytes per parameter by quantization (whole file, including higher-precision embedding
layers): Q2 0.37, Q3 0.48, Q4 0.58, Q5 0.68, Q6 0.80, Q8 1.05, F16 2.0. Suffixes adjust
it (`_S` x0.96, `_L` x1.05, `_XL` x1.08) and imatrix `IQ` quants are about 6% smaller.

GGUF files that cannot be the whole model (draft heads, projectors, anything under 45%
of the expected size for its quantization) are not offered as "the model".

## 2. Placement

Against the [usable budget](HARDWARE_DETECTION.md#from-detected-memory-to-usable-memory),
not physical memory:

1. Fits in usable VRAM → **GPU** (or **GPU split** when it needs several cards)
2. Else, if at least 15% of the weights fit on the GPU and the rest fits in usable RAM →
   **partial offload**
3. Else, if it fits in usable RAM → **CPU**
4. Else → does not fit

## 3. Speed

Token generation is memory-bandwidth bound: each token reads the active weights once.

```
tok/s = GPU memory bandwidth / (params x speed-bytes/param) x 0.55
```

- GPU bandwidth comes from a per-card table (RTX 4080 SUPER: 736 GB/s).
- The 0.55 efficiency factor is configurable (`hardware.tps_efficiency`).
- Mixture-of-experts models use active parameters and an expert-count overhead factor.
- Partial offload: GPU-resident bytes at GPU speed, the rest at 60% of DDR bandwidth
  (from the RAM's SPD speed), times added.
- CPU only: DDR bandwidth x 0.6.
- GPU not in the table: a per-backend constant, marked LOW confidence.

Prompt-processing speed and time to first token are coarse ratios of decode speed and
are always labelled LOW confidence.

Calibration check on the development machine: Spark-X2.5-4B Q8_0 was estimated at
99 tok/s and measured at 108.7 tok/s.

## 4. Fit category

Will It Run never answers just "fits" or "doesn't fit". `target` is
`performance.target_tokens_per_second` (default 20).

| Category | Meaning |
|---|---|
| **PERFECT** | Fully on GPU, uses at most 90% of the usable VRAM, at least 1.5x target speed |
| **EXCELLENT** | Fully on GPU and at least target speed (or 90 to 97% of usable VRAM) |
| **GOOD** | At least half the target speed; the ceiling for partial offload |
| **ACCEPTABLE** | At least a quarter of the target speed; the ceiling for CPU-only |
| **SLOW** | Runs, at least 1.5 tok/s |
| **MEMORY TIGHT** | Fits on GPU but leaves under 3% of the budget, or offload fills over 90% of usable RAM |
| **NOT RECOMMENDED** | Runs below 1.5 tok/s |
| **IMPOSSIBLE** | Does not fit in usable VRAM plus RAM even at a 2K context |

Every result carries `reasons`, the `+` / `−` lines shown under "Why".

## 5. Choosing a quantization

All of a model's files are evaluated and ranked by

```
utility = 0.45 x fit category + 0.35 x quantization quality + 0.20 x speed
```

with two rules on top:

- Quantizations below 3 bits are a last resort: used only when nothing at 3 bits or more
  reaches GOOD.
- If nothing runs at the requested context, the context is halved (down to 2K) and the
  result says so.

The choice explains itself against its neighbours, for example: *"Will It Run selected
Q4_K_M instead of Q6_K: Q6_K would leave only 320 MB of usable VRAM, while Q4_K_M leaves
about 2.1 GB and is more stable on this computer."*

## 6. Will It Run score

A weighted sum of eight components, each 0 to 100. Weights are configurable in Settings
and normalized, so only their proportions matter.

| Component | Default | What it measures |
|---|---|---|
| Hardware fit | 25% | The fit category |
| Task quality | 25% | Family and size strength for the job, minus the quantization penalty |
| Specialization | 15% | Whether the model's categories match the job |
| Speed | 10% | tok/s against the target |
| Context | 8% | Usable context against what the job needs |
| Tool use | 7% | Declared tool-calling support |
| Reliability | 5% | Metadata confidence and adoption (log of downloads) |
| Local benchmark | 5% | Measured tok/s; a neutral 50 when not measured |

The headline is `total / 10`, shown as for example **9.4 / 10**.

**Alignment is not a component.** A model named "uncensored" or "abliterated" scores
exactly like its standard twin; a test asserts this. If you set a model preference in
Settings, matching models that are runnable and credible for the task (quality at least
50) get a visible `preference_bonus` of 6 points. The other kind loses nothing.
See [UNCENSORED_MODELS.md](UNCENSORED_MODELS.md).

## 7. Task quality is an estimate, and says so

There is no public benchmark number for most quantized community builds. Task quality is
derived from:

- a size baseline (active parameters for MoE models), and
- a per-family table of relative task strength (coding / reasoning / chat) aggregated
  from public leaderboards, adapted from llmfit.

It is labelled "estimate" with a confidence level (MEDIUM when the family is known, LOW
on size alone, UNKNOWN with zero scores when the parameter count is not available).
Stars in the UI are this estimate. They are never presented as benchmark results.

## 8. Confidence shown to the user

| Figure | Typical confidence |
|---|---|
| Hardware detection | HIGH |
| VRAM estimate (real file size, exact KV) | HIGH |
| TPS estimate (GPU in the bandwidth table) | MEDIUM |
| Task quality | MEDIUM or LOW |
| Local benchmark | HIGH |

Measured results always replace estimates for that model.

## 9. Score for every file

The explorer picks one quantization per model (section 5), but the *Files* tab scores
every file of the repository with the same function the recommender uses
(`score_model`), against the fit of that exact file. A lighter quantization raises task
quality and may lower hardware fit and speed, so the scores differ per file and the
recommended one is not always the largest. Files can be sorted by score, accuracy or
size.

## 10. Accuracy by quantization

`fit/accuracy.py` answers "how much quality does this quantization keep?" with a
published measurement instead of a guess: the perplexity increase over the unquantized
model that the llama.cpp project lists for each quantization, measured on Llama-3-8B
(`tools/quantize/quantize.cpp`, `QUANT_OPTIONS`).

| Quantization | Perplexity increase | Will It Run wording |
|---|---|---|
| Q8_0 | +0.0026 | Near lossless |
| Q6_K | +0.0217 | Near lossless |
| Q5_K_M | +0.0569 | Very low loss |
| Q5_K_S | +0.1049 | Very low loss |
| Q4_K_M | +0.1754 | Low loss |
| Q4_K_S | +0.2689 | Low loss |
| Q3_K_L | +0.5562 | Noticeable loss |
| Q3_K_M | +0.6569 | Noticeable loss |
| Q3_K_S | +1.6321 | High loss |
| Q2_K | +3.5199 | Severe loss |

The six wordings are Will It Run's own bands over that number (up to 0.03, 0.11, 0.30,
0.70, 2.0, above). Bits per weight come from the published file sizes.

What is and is not claimed:

- The figures were measured on one 8B model. Larger models usually lose less at the same
  quantization and smaller ones lose more. The interface says so wherever it shows them.
- A label the table does not contain is never given a number of its own. `Q4_K_L`,
  `Q3_K_XL` and Unsloth `UD-` variants show the figure of the closest measured
  quantization that is not better (`basis: nearest`).
- imatrix (`IQ`) quantizations have no published perplexity in that table. They are
  grouped with the smallest measured quantization of the same bit width, shown with a
  `~` and no number (`basis: bits`).
- Anything unrecognized is shown as Unknown.

How it feeds the score: the quantization penalty on task quality (section 6) is
0 / -1 / -2 / -5 points for 8, 6, 5 and 4 bits, as before. Below 4 bits it follows the
measured level instead of the bit count: -9 for noticeable loss (Q3_K_M, Q3_K_L), -16
for high loss (Q3_K_S, IQ3) and -30 for severe loss (Q2, IQ2). A bit-count table
under-rates that drop, and without this a 2-bit file that fits perfectly scored almost
the same as a 4-bit one.

The *Recommended* mark in the Files tab is the file Will It Run would pick among all of
them (section 5), whichever file happens to be installed.

