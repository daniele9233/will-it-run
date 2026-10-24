# LM Studio integration

LM Studio is Will It Run's model runtime. Will It Run only uses interfaces LM Studio
documents. This page lists every one, so nothing here is invented. Verified against
LM Studio CLI commit `1017bcb` with the server on port 1234.

Code: `backend/src/agentfit/lmstudio`.

## Detection

| What | How |
|---|---|
| Home directory | `~/.lmstudio`, or the path in `~/.lmstudio-home-pointer` |
| CLI | `<home>/bin/lms.exe`, else `lms` on `PATH` |
| Application | `<home>/.internal/app-install-location.json`, else the default install folders |
| Models directory | The `downloadsFolder` key of `<home>/settings.json` or `<home>/apps/*/settings.json`, else `<home>/models` |
| Configured port | `<home>/.internal/http-server-config.json` |
| Server running | `GET /api/v1/models` answers, else `lms server status --json` |

Only the `downloadsFolder` and `port` keys are read from LM Studio's settings files.
Other keys in those files (including stored tokens) are never read.

## `lms` CLI commands used

```
lms version --json
lms server status --json
lms server start
lms ls --json                      installed models with their file paths
lms ps --json                      loaded models
lms load <key> -y [-c N] [--gpu R] [--identifier ID] [--ttl S] [--parallel N]
               [--speculative-draft-mtp --speculative-draft-max-tokens N --speculative-draft-min-continue-probability 0]
lms load <key> --estimate-only     LM Studio's own memory estimate
lms unload <identifier> | --all
lms runtime ls                     selected inference engines
lms get <model> -y --gguf          (available; the REST download is used by default)
```

## REST API v1 endpoints used

Documented at <https://lmstudio.ai/docs/developer/rest>.

| Endpoint | Used for |
|---|---|
| `GET /api/v1/models` | Installed models, capabilities, loaded instances |
| `POST /api/v1/models/load` `{model, context_length, echo_load_config}` | Loading; returns `instance_id`, `load_time_seconds` |
| `POST /api/v1/models/unload` `{instance_id}` | Unloading |
| `POST /api/v1/models/download` `{model, quantization}` | Downloads; returns `job_id` |
| `GET /api/v1/models/download/status/:job_id` | Download progress |
| `POST /api/v1/chat` `{model, input, max_output_tokens, ...}` | Benchmarks: its `stats` block gives `tokens_per_second`, `time_to_first_token_seconds`, token counts |

## OpenAI-compatible endpoint used

| Endpoint | Used for |
|---|---|
| `POST /v1/chat/completions` | Agent turns: `tools` for function calling and `response_format: {type: "json_schema"}` for structured output |

## Why two sources for the model list

`lms ls --json` knows each model's file path (`publisher/repo/file.gguf`), which is how
Will It Run maps an installed model back to its Hugging Face repository. The REST API knows
capabilities and which instances are loaded. Will It Run merges both; either one alone
still works (CLI only when the server is off, REST only when the CLI is missing).

## Loading

- An already-loaded model is reused, not loaded twice.
- The REST load call has no GPU-offload parameter. When an explicit ratio is requested,
  Will It Run uses `lms load --gpu <ratio>`.
- The REST load call has no idle-timeout parameter either. Workflow loads therefore go
  through `lms load <key> -c <context> --ttl <seconds> -y` when the CLI is installed,
  and through REST without a timeout when it is not. `lms ps --json` confirmed
  `ttlMs: 600000` on a model loaded this way.
- During workflows, the [model gateway](LANGGRAPH.md#model-loading-the-gateway) decides
  what to unload before a load, picks the context from the free VRAM, and uses
  `lms load <key> --estimate-only -c <context>` as its first idea of what a model costs.
  The estimator reports what the model wants even when that exceeds the card.

## Faster generation with MTP

Some GGUF files carry a multi-token-prediction head next to the model (the `-mtp` files of Qwen3.8, for
example; their metadata has `<architecture>.nextn_predict_layers` above 0 and tensors named `blk.N.nextn.*`).
LM Studio can use it for speculative decoding: the head guesses the next tokens, the model checks the guesses
in one pass and keeps the right ones, so the answers are the same and come faster. Will It Run recognises such
a file by reading its header (`lmstudio/gguf.py`, a fraction of a second, only the metadata) and, when
**Settings → Runtime → Faster generation (MTP)** is on (the default), loads it differently:

| What | Why |
|---|---|
| `--speculative-draft-mtp --speculative-draft-max-tokens 2` (`runtime.speculative_draft_tokens`) | Two guesses per step was the best for prose and explanations; code gains from 3 or 4 |
| `--speculative-draft-min-continue-probability 0` | A threshold made prose slower (48 against 60 tok/s): the head's confidence is a poor guide |
| `--parallel 1` | LM Studio's default of four slots costs 1.4 GiB more on a 27B, and a run asks for one answer at a time |
| `--gpu max`, when the file plus 2.5 GiB fits the card | On automatic, LM Studio left part of the model off the GPU: 55 instead of 66-68 tok/s with the VRAM in use the same to within 100 MiB. Not asked when the file is too big or the card cannot be read |
| `repeat_penalty: 1.0` in every chat request to that model | LM Studio's own default of 1.1 penalises tokens the head cannot know about: acceptance fell from 52 % to 42 % |

If LM Studio refuses (a file without a head, or an LM Studio without these flags: the error names MTP or
speculative decoding) the model is loaded as before and not asked again until Will It Run restarts. Any other
failure is not blamed on MTP.

Measured on an RTX 4080 SUPER 16 GB with `Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf` (12.1 GB, 16K context), through
the runtime of this program: 44 tok/s without MTP, 67-74 with it (60 with LM Studio's default repeat penalty).
On a direct `llama-server`, long runs of 45-80 s gave 64-68 on prose and explanations at temperature 0.4, 72 in
thinking mode and 86 on code. Prose at temperature 0.7 or more gives 59-65: the higher the temperature, the
fewer guesses are right.

## Downloads

Two methods (Settings → Runtime → Download method):

| | Will It Run | LM Studio |
|---|---|---|
| Mechanism | HTTPS from Hugging Face into `<folder>/<publisher>/<repo>/<file>` | `POST /api/v1/models/download` |
| Progress | Byte-accurate | Job status polling |
| Pause / stop | Yes | Not available: the documented API has no such call |
| Resume after restart | Yes (HTTP range) | No |
| Hash verification | SHA-256 against the repository's published hash | Done by LM Studio |
| Exact file | By exact filename | By quantization |
| Folder | LM Studio's, or any folder chosen for the download | LM Studio's only |

`auto` (default) uses Will It Run's own downloader whenever a models folder is known, so
every download can be paused and stopped. LM Studio's downloader is used only when the
user selects it, or as a fallback when no folder is known. In both cases the
[disk-safety check](SECURITY.md#disk-safety) runs first, against the drive of the
folder that will actually be written.

### Saving outside LM Studio's folder

The install panel has a *Save to* choice: LM Studio's folder, a suggested folder on each
other drive, or any path. LM Studio only scans its own models folder, so a file saved
elsewhere is linked into `<LM Studio models>/<publisher>/<repo>/`:

1. a hard link when both folders are on the same drive;
2. a symbolic link across drives;
3. if Windows refuses the symbolic link (it needs Developer Mode or administrator
   rights), a directory junction for the repository folder, which needs neither. A
   junction is only created over a missing or empty folder, never over existing models.

The data exists once, in the folder the user chose. The download list says what
happened. `lms import` is deliberately not used for this: on a machine whose models
folder was moved it reported *"Could not locate LM Studio configuration file, using
default path"* and would have linked into the wrong folder (checked with `--dry-run`).

`HF_ENDPOINT` selects a Hugging Face mirror, as in the official tools.

## Reasoning models and structured output

Models with a thinking mode spend output tokens reasoning before they answer. LM
Studio's OpenAI-compatible endpoint documents no parameter to limit that, so Will It Run
does not send one. Instead:

- an agent's output budget follows the model's context window rather than a fixed cap;
- if a reply ends with `finish_reason: length` and no answer, the agent retries with
  explicit feedback to answer briefly;
- reasoning tokens are reported per agent in the session view.

If a model or engine rejects `response_format`, the agent falls back to prompt-only JSON
and validates the result against the schema itself.

## When LM Studio is absent or offline

- Not installed: Will It Run says "LM Studio is required for local model execution" and
  offers **Open LM Studio Download** or **Continue without runtime**. It never installs
  LM Studio. Hardware analysis and the model explorer still work.
- Installed, server off: installed models are still listed through the CLI. Loading,
  benchmarking and running ask to start the server (`lms server start`).
- Wrong port: the status explains which port the server is on and where to change it.
- API authentication enabled in LM Studio: enter the token in Settings → Runtime. It is
  stored in your settings file, sent as a bearer token, never returned to the UI and
  never logged.
