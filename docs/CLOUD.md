# Cloud models

Will It Run runs models on this PC through LM Studio. It can also call four cloud providers with
your own API keys: **Claude** (Anthropic), **ChatGPT** (OpenAI), **DeepSeek** and **Gemini**
(Google). A cloud model goes wherever a local model goes: Clone a site, Chat, and any slot of a
workflow.

## Adding a key

Settings > **Cloud models**, or *Add cloud models* (*Aggiungi modelli cloud*) under a model menu that lists them greyed out.
Paste the key and press *Save and test*: the key is tried at once by asking the provider for its
model list, which is what fills the menus.

- Keys are encrypted with Windows DPAPI for your Windows account (`%APPDATA%\Will It Run\secrets`).
  Another account, or a copy of the file on another PC, cannot read them. They never appear in the
  settings file, the database, the log or the HTTP API: the interface is told only whether a key is
  set and its last four characters.
- A key in an environment variable is used when none is saved: `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`).
- What you send a cloud model leaves this PC: your questions, and the files, pages and
  screenshots the model reads. Every request is paid on your account.

The Arena can put a cloud model through its questions beside local ones (see [ARENA.md](ARENA.md)): it
asks for your agreement first, because every answer is paid for. The provider chooses its own sampling,
so the temperature and the seed are not sent, and the report says so.

## How a cloud model is named

`cloud:<provider>/<model>`, e.g. `cloud:anthropic/claude-opus-5-5`, `cloud:deepseek/deepseek-v4-pro`.
The model gateway hands such a slot out at once: nothing is loaded and no VRAM is spent. Its context
is the model's own (from the provider's list, or a table), capped by *Most context for a cloud model*
(200,000 tokens by default), because every token is paid for.

## How each provider is called

| Provider | API | Notes |
|---|---|---|
| Claude | Anthropic Messages API through the official `anthropic` SDK | Adaptive thinking with summarized reasoning (shown live), effort `high`, prompt caching on. Thinking blocks are kept and sent back unchanged in a tool loop; if the engine shortens old history the API is asked to drop the blocks that no longer match instead of refusing. The server-side refusal fallback is on for the models that accept it; a refusal becomes an answer that says so. |
| ChatGPT | OpenAI chat completions | `max_completion_tokens`, no temperature (reasoning models accept only their own). |
| DeepSeek | OpenAI-compatible chat completions | With tools in the request DeepSeek needs every earlier assistant turn's `reasoning_content` back: it is remembered and returned. JSON-schema output becomes JSON mode. |
| Gemini | Google's OpenAI-compatible endpoint | Gemini 3 attaches a thought signature to tool calls and wants it back: it is remembered and returned. |

Temperature is never sent to a cloud provider. Busy answers (429, 5xx) are tried again twice; a
refused key says where to fix it.

The default model of each provider is the first of its live list that is in a short preference
list (`claude-opus-5-5`, `gpt-5.5`, `deepseek-v4-pro`, `gemini-3.8-pro`…); the live list decides.

## Files

| What | File |
|---|---|
| Providers, model naming, context table | `backend/src/agentfit/cloud/providers.py` |
| DPAPI key store | `cloud/secrets.py` |
| OpenAI-compatible client (OpenAI, DeepSeek, Gemini) | `cloud/openai_wire.py` |
| Anthropic client | `cloud/anthropic_wire.py` |
| Keys, model lists, `HybridRuntime` (local or cloud per model key) | `cloud/service.py` |
| Settings page section | `desktop/src/components/CloudKeys.tsx` |

Tests: `backend/tests/test_cloud.py` (no request leaves the machine: HTTP is mocked, the Anthropic
SDK is replaced by a stand-in that returns real SDK message objects).
