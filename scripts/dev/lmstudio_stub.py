"""A stand-in for LM Studio's local server, for trying the interface without a GPU.

It answers the handful of endpoints Will It Run uses and "generates" canned text, slowly
enough to watch it arrive and to press Stop. It loads nothing and touches no model, so it
can run while the real LM Studio is busy with something else.

    python scripts/dev/lmstudio_stub.py [port]          # default 12399

What it says depends on the request, so the different paths of a run can be tried:

* a request that mentions ``notes.txt`` and has tools  -> one ``write_file`` call, then an answer
* a request that mentions ``long``                     -> a few hundred words, a word at a time
* anything else                                         -> a short answer

It also has one embedding model, ``stub-embed``, whose vectors are made from the words of
the text: enough for the project memory to rank by, and nothing like a real model's sense
of meaning.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import zlib
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODELS = {
    "stub-7b": {"display_name": "Stub 7B", "params_string": "7B", "size_bytes": 4_400_000_000, "quant": "Q4_K_M"},
    "stub-27b": {"display_name": "Stub 27B", "params_string": "27B", "size_bytes": 14_000_000_000, "quant": "Q3_K_XL"},
}
EMBEDDING = "stub-embed"
WORD_SECONDS = 0.04
loaded: dict[str, int] = {}
app = FastAPI()


@app.get("/api/v1/models")
def models() -> dict[str, Any]:
    embedding = {
        "type": "embedding", "publisher": "stub", "key": EMBEDDING, "display_name": "Stub Embed", "architecture": "stub",
        "quantization": {"name": "Q4_K_M"}, "size_bytes": 84_000_000, "max_context_length": 2048, "format": "gguf",
        "loaded_instances": [{"id": EMBEDDING, "config": {"context_length": 2048}}] if EMBEDDING in loaded else [],
    }
    return {
        "models": [embedding] + [
            {
                "type": "llm", "publisher": "stub", "key": key, "display_name": info["display_name"], "architecture": "stub",
                "quantization": {"name": info["quant"]}, "size_bytes": info["size_bytes"], "params_string": info["params_string"],
                "max_context_length": 65536, "format": "gguf", "capabilities": {"trained_for_tool_use": True},
                "loaded_instances": [{"id": key, "config": {"context_length": loaded[key]}}] if key in loaded else [],
            }
            for key, info in MODELS.items()
        ]
    }


@app.post("/api/v1/models/load")
async def load(request: Request) -> dict[str, Any]:
    body = await request.json()
    await asyncio.sleep(0.6)
    loaded[body["model"]] = int(body.get("context_length") or 8192)
    return {"instance_id": body["model"], "load_time_seconds": 0.6, "load_config": {"context_length": loaded[body["model"]]}}


@app.post("/api/v1/models/unload")
async def unload(request: Request) -> dict[str, Any]:
    loaded.pop((await request.json()).get("instance_id"), None)
    return {}


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> dict[str, Any]:
    body = await request.json()
    inputs = body.get("input") or []
    if isinstance(inputs, str):
        inputs = [inputs]
    rows = []
    for index, text in enumerate(inputs):
        vector = [0.0] * 256
        for word in re.findall(r"[a-z0-9]{3,}", str(text).lower()):
            vector[zlib.crc32(word[:5].encode()) % 256] += 1.0
        vector[0] += 0.01  # never a zero vector
        rows.append({"object": "embedding", "index": index, "embedding": vector})
    return {"object": "list", "data": rows, "model": body.get("model")}


@app.post("/api/v1/chat")
async def measure(request: Request) -> dict[str, Any]:
    body = await request.json()
    return {
        "model_instance_id": body.get("model"), "output": [{"type": "message", "content": "1 2 3 4 5"}],
        "stats": {"input_tokens": 12, "total_output_tokens": 48, "tokens_per_second": 50.0, "time_to_first_token_seconds": 0.1},
    }


def _request_text(messages: list[dict[str, Any]]) -> str:
    """What was asked this time: the task of the last user message, without the history under it."""
    text = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
    head = text.split("## Task\n", 1)[-1]
    return head.split("\n\n## ", 1)[0].lower()


def _plan(body: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """(text to write, tool call to make) for this request."""
    messages = body["messages"]
    asked = _request_text(messages)
    answered = messages[-1].get("role") == "tool"
    if body.get("tools") and "notes.txt" in asked and not answered:
        call = {"name": "write_file", "arguments": json.dumps({"path": "notes.txt", "content": "High tide follows the Moon.\nLow tide comes six hours later.\n"})}
        return "", call
    if answered:
        return "Done: I wrote notes.txt with two lines about tides. I did not run anything else.", None
    if "long" in asked:
        # "very long" lasts about a minute: time enough to look at a run that is working.
        return " ".join(f"word{n}" for n in range(1, 1501 if "very long" in asked else 401)), None
    if body.get("response_format"):
        schema = body["response_format"]["json_schema"]["schema"]
        fill = {"string": "ok", "boolean": True, "integer": 0, "number": 0, "array": [], "object": {}}
        return json.dumps({name: fill.get(schema["properties"][name].get("type", "string"), "ok") for name in schema.get("required", [])}), None
    return "Hello. This is the stand-in model: it answers with fixed text, so what you see is the interface at work, not a model.", None


@app.post("/v1/chat/completions")
async def completions(request: Request) -> Any:
    body = await request.json()
    text, call = _plan(body)
    usage = {"prompt_tokens": 200, "completion_tokens": max(8, len(text.split()) + 6), "completion_tokens_details": {"reasoning_tokens": 6}}
    if not body.get("stream"):
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if call:
            message["tool_calls"] = [{"id": f"call_{int(time.time() * 1000)}", "type": "function", "function": call}]
        return JSONResponse({"choices": [{"message": message, "finish_reason": "tool_calls" if call else "stop"}], "usage": usage})

    async def events() -> Any:
        def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
            return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"

        for word in "Thinking about what was asked, briefly.".split():
            yield chunk({"reasoning_content": word + " "})
            await asyncio.sleep(WORD_SECONDS)
        if call:
            yield chunk({"tool_calls": [{"index": 0, "id": f"call_{int(time.time() * 1000)}", "type": "function", "function": call}]})
        for word in text.split(" ") if text else []:
            yield chunk({"content": word + " "})
            await asyncio.sleep(WORD_SECONDS)
        yield chunk({}, "tool_calls" if call else "stop")
        yield "data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 12399, log_level="warning")
