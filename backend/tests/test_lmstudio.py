"""LM Studio integration against a mocked HTTP server and a fake CLI."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from agentfit.config import RuntimeSettings, Settings
from agentfit.lmstudio import detect
from agentfit.lmstudio.cli import LmsCli, LmsCliError, parse_estimate, parse_runtime_ls, strip_ansi
from agentfit.lmstudio.client import LMStudioClient, LMStudioError, LMStudioOffline, ModelNotFound
from agentfit.lmstudio.models import LMStudioInstall
from agentfit.lmstudio.runtime import (
    LMStudioRuntime,
    installed_from_cli,
    installed_from_rest,
    merge_installed,
)
from agentfit.runtime import RuntimeUnavailable
from fakes import write_gguf

REST_MODELS = {
    "models": [
        {
            "type": "llm", "publisher": "unsloth", "key": "qwen3.8-27b", "display_name": "Qwen3.8 27B UD", "architecture": "qwen35",
            "quantization": {"name": "Q3_K_XL", "bits_per_weight": 3}, "size_bytes": 14074004752, "params_string": "27B",
            "loaded_instances": [{"id": "qwen3.8-27b", "config": {"context_length": 8192}}], "max_context_length": 262144, "format": "gguf",
            "capabilities": {"vision": True, "trained_for_tool_use": True, "reasoning": {"allowed_options": ["off", "on"], "default": "on"}},
        },
        {
            "type": "embedding", "publisher": "nomic-ai", "key": "text-embedding-nomic", "display_name": "Nomic Embed", "architecture": "nomic-bert",
            "quantization": {"name": "Q4_K_M"}, "size_bytes": 84000000, "loaded_instances": [], "max_context_length": 2048, "format": "gguf", "capabilities": {},
        },
    ]
}

CLI_LS = [
    {
        "type": "llm", "modelKey": "qwen3.8-27b", "format": "gguf", "displayName": "Qwen3.8 27B UD", "publisher": "unsloth",
        "path": "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q3_K_XL.gguf", "sizeBytes": 14074004752, "paramsString": "27B",
        "architecture": "qwen35", "quantization": {"name": "Q3_K_XL", "bits": 3}, "vision": True, "trainedForToolUse": True, "maxContextLength": 262144,
    }
]


class FakeCli(LmsCli):
    def __init__(
        self, *, ls: list[dict[str, Any]] | None = None, fail: bool = False, running: bool = True, port: int = 1234,
        load_error: str | None = None,
    ) -> None:
        super().__init__("lms.exe")
        self._ls, self._fail, self._running, self._port = ls or [], fail, running, port
        # What ``lms load`` says when it is asked for MTP and refuses (a model without a head, or an LM Studio without the flags).
        self._load_error = load_error
        self.commands: list[list[str]] = []

    def run(self, args: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
        self.commands.append(args)
        if self._fail:
            return 1, "", "Error: something went wrong"
        if args[:2] == ["server", "start"]:
            self._running = True
            return 0, "", ""
        if args[:1] == ["load"] and self._load_error and "--speculative-draft-mtp" in args:
            return 1, "", self._load_error
        if args[:2] == ["ls", "--json"]:
            return 0, json.dumps(self._ls), ""
        if args[:2] == ["ps", "--json"]:
            return 0, "[]", ""
        if args[:3] == ["server", "status", "--json"]:
            return 0, json.dumps({"running": self._running, "port": self._port}), ""
        if args[:2] == ["version", "--json"]:
            return 0, json.dumps({"version": "abc123"}), ""
        return 0, "", ""


def make_runtime(handler, cli: LmsCli | None = None) -> LMStudioRuntime:
    client = LMStudioClient("http://127.0.0.1:1234", client=httpx.Client(transport=httpx.MockTransport(handler)))
    install = LMStudioInstall(installed=True, home="C:/lm", cli_path="lms.exe", models_dir="D:/Models", server_port=1234)
    return LMStudioRuntime(Settings(), client=client, cli=cli or FakeCli(ls=CLI_LS), install=install)


def offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


# -- model discovery ----------------------------------------------------------


def test_model_discovery_merges_cli_paths_with_rest_state():
    runtime = make_runtime(lambda r: httpx.Response(200, json=REST_MODELS))
    models = {m.model_key: m for m in runtime.list_installed()}
    llm = models["qwen3.8-27b"]
    assert llm.repo == "unsloth/Qwen3.8-27B-GGUF" and llm.filename == "Qwen3.8-27B-UD-Q3_K_XL.gguf"
    assert llm.quant == "Q3_K_XL" and llm.vision and llm.tool_use and llm.reasoning
    assert llm.is_loaded and llm.loaded_instances[0].context_length == 8192
    assert models["text-embedding-nomic"].type == "embedding"


def test_loaded_models_listed_from_rest():
    runtime = make_runtime(lambda r: httpx.Response(200, json=REST_MODELS))
    assert [(i.instance_id, i.model_key) for i in runtime.list_loaded()] == [("qwen3.8-27b", "qwen3.8-27b")]


def test_cli_row_parsing_handles_short_paths():
    hub_model = installed_from_cli({"modelKey": "google/gemma", "path": "google/gemma-4-12b-qat", "quantization": {"name": "Q4_0"}})
    assert hub_model.repo is None and hub_model.quant == "Q4_0"


def test_merge_keeps_rest_only_models():
    rest = [installed_from_rest(row) for row in REST_MODELS["models"]]
    assert {m.model_key for m in merge_installed([], rest)} == {"qwen3.8-27b", "text-embedding-nomic"}


# -- LM Studio offline / API unavailable --------------------------------------


def test_offline_server_is_reported_not_raised_by_status():
    runtime = make_runtime(offline, FakeCli(ls=CLI_LS, running=False))
    status = runtime.status()
    assert status.installed and not status.api_reachable and not status.server_running
    assert any("not running" in note for note in status.notes)
    assert not runtime.is_available()


def test_installed_models_still_listed_through_cli_when_api_is_down():
    runtime = make_runtime(offline)
    models = runtime.list_installed()
    assert [m.model_key for m in models] == ["qwen3.8-27b"] and not models[0].is_loaded


def test_no_cli_and_no_api_raises_runtime_unavailable():
    runtime = make_runtime(offline, LmsCli(None))
    with pytest.raises(RuntimeUnavailable):
        runtime.list_installed()


def test_api_unavailable_error_is_typed():
    client = LMStudioClient(client=httpx.Client(transport=httpx.MockTransport(offline)))
    with pytest.raises(LMStudioOffline):
        client.list_models()


def test_api_server_error_surfaces_the_message():
    client = LMStudioClient(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"error": {"message": "engine crashed"}}))))
    with pytest.raises(LMStudioError, match="engine crashed"):
        client.list_models()


def test_port_mismatch_is_explained():
    runtime = make_runtime(offline, FakeCli(running=True))
    runtime.settings.runtime.lmstudio_port = 9999
    status = runtime.status()
    assert status.server_running and any("port 1234" in n for n in status.notes)


def test_a_server_on_another_port_is_found_without_editing_the_settings():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port != 1235:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=REST_MODELS)

    client = LMStudioClient("http://127.0.0.1:1234", client=httpx.Client(transport=httpx.MockTransport(handler)))
    install = LMStudioInstall(installed=True, home="C:/lm", cli_path="lms.exe", models_dir="D:/Models", server_port=1235)
    runtime = LMStudioRuntime(Settings(), client=client, cli=FakeCli(ls=CLI_LS, port=1235), install=install)
    status = runtime.status()
    assert status.api_reachable and status.server_port == 1235
    assert runtime.client.base_url == "http://127.0.0.1:1235"
    assert not any("could not reach" in note for note in status.notes)


def test_start_server_follows_the_port_the_cli_reports():
    listening = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if not listening["on"] or request.url.port != 1235:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=REST_MODELS)

    cli = FakeCli(ls=CLI_LS, running=False, port=1235)
    original = cli.run

    def run(args: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
        result = original(args, timeout)
        if args[:2] == ["server", "start"]:
            listening["on"] = True
        return result

    cli.run = run  # type: ignore[method-assign]
    client = LMStudioClient("http://127.0.0.1:1234", client=httpx.Client(transport=httpx.MockTransport(handler)))
    install = LMStudioInstall(installed=True, home="C:/lm", cli_path="lms.exe", models_dir="D:/Models", server_port=1235)
    runtime = LMStudioRuntime(Settings(), client=client, cli=cli, install=install)
    assert runtime.ensure_server()
    assert ["server", "start"] in cli.commands and runtime.client.base_url == "http://127.0.0.1:1235"


def test_the_bionic_layout_is_detected(tmp_path, monkeypatch):
    home = tmp_path / ".lmstudio"
    models = tmp_path / "Models"
    models.mkdir()
    app = tmp_path / "Bionic" / "Bionic.exe"
    app.parent.mkdir()
    app.write_bytes(b"")
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "lms.exe").write_bytes(b"")
    (home / "settings.json").write_text(json.dumps({"downloadsFolder": str(models)}), encoding="utf-8")
    internal = home / "apps" / "bionic" / ".internal"
    internal.mkdir(parents=True)
    (internal / "http-server-config.json").write_text(json.dumps({"port": 1235}), encoding="utf-8")
    (internal / "app-install-location.json").write_text(json.dumps({"path": str(app)}), encoding="utf-8")
    monkeypatch.setattr(detect, "lmstudio_home", lambda: home)
    install = detect.detect_install()
    assert install.installed and install.app_path == str(app)
    assert install.server_port == 1235 and install.models_dir == str(models)


# -- load / unload ------------------------------------------------------------


def test_model_load_sends_documented_body_and_returns_timing():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": []})
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"type": "llm", "instance_id": "spark", "load_time_seconds": 3.6, "status": "loaded", "load_config": {"context_length": 8192}})

    result = make_runtime(handler).load("spark", context_length=8192)
    assert seen["path"] == "/api/v1/models/load"
    assert seen["body"] == {"model": "spark", "echo_load_config": True, "context_length": 8192}
    assert result.instance_id == "spark" and result.load_time_seconds == 3.6 and not result.already_loaded


def test_load_reuses_an_already_loaded_instance():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=REST_MODELS)

    result = make_runtime(handler).load("qwen3.8-27b")
    assert result.already_loaded and "/api/v1/models/load" not in calls


def test_explicit_gpu_ratio_goes_through_the_cli():
    cli = FakeCli(ls=CLI_LS)
    runtime = make_runtime(lambda r: httpx.Response(200, json={"models": []}), cli)
    runtime.load("spark", context_length=4096, gpu="0.5")
    assert ["load", "spark", "-y", "-c", "4096", "--gpu", "0.5"] in cli.commands


def test_model_load_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(500, json={"error": {"message": "Failed to load model: insufficient memory"}})

    with pytest.raises(LMStudioError, match="insufficient memory"):
        make_runtime(handler).load("huge")


def test_model_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404, json={"error": {"message": "Model not found: nope"}})

    with pytest.raises(ModelNotFound):
        make_runtime(handler).load("nope")


def test_model_unload_sends_instance_id():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"instance_id": "spark"})

    make_runtime(handler).unload("spark")
    assert seen == {"path": "/api/v1/models/unload", "body": {"instance_id": "spark"}}


def test_unload_falls_back_to_cli_when_api_is_down():
    cli = FakeCli()
    make_runtime(offline, cli).unload("spark")
    assert ["unload", "spark"] in cli.commands


# -- download endpoints -------------------------------------------------------


def test_download_and_status_use_documented_endpoints():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.method == "POST":
            assert json.loads(request.content) == {"model": "https://huggingface.co/pub/Model-GGUF", "quantization": "Q4_K_M"}
            return httpx.Response(200, json={"job_id": "job_1", "status": "downloading", "total_size_bytes": 1000})
        return httpx.Response(200, json={"job_id": "job_1", "status": "completed", "total_size_bytes": 1000, "downloaded_bytes": 1000})

    client = LMStudioClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    job = client.download("https://huggingface.co/pub/Model-GGUF", "Q4_K_M")
    done = client.download_status(job.job_id or "")
    assert paths == ["/api/v1/models/download", "/api/v1/models/download/status/job_1"]
    assert job.status == "downloading" and done.status == "completed" and done.downloaded_bytes == 1000


def test_download_error_response():
    client = LMStudioClient(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "Could not resolve model"}))))
    with pytest.raises(LMStudioError, match="Could not resolve"):
        client.download("nope/nope")


# -- inference ----------------------------------------------------------------


def test_native_chat_returns_runtime_stats():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/api/v1/chat" and body["model"] == "spark" and body["stream"] is False
        return httpx.Response(200, json={
            "model_instance_id": "spark",
            "output": [{"type": "reasoning", "content": "thinking"}, {"type": "message", "content": "hello"}],
            "stats": {"input_tokens": 264, "total_output_tokens": 200, "tokens_per_second": 108.7, "time_to_first_token_seconds": 0.061},
        })

    result = make_runtime(handler).measure("spark", "hi", max_output_tokens=200)
    assert result.text == "hello" and result.reasoning == "thinking"
    assert result.stats.tokens_per_second == 108.7 and result.stats.output_tokens == 200


def test_openai_compatible_completion_passes_tools_and_schema():
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "{}"}}]})

    runtime = make_runtime(handler)
    runtime.complete("spark", [{"role": "user", "content": "x"}], tools=[{"type": "function"}], response_format={"type": "json_schema"})
    assert seen["tools"] == [{"type": "function"}] and seen["response_format"] == {"type": "json_schema"} and seen["stream"] is False


def test_the_servers_crash_page_is_reported_in_words_not_in_html():
    page = '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Error</title>\n</head>\n<body>\n<pre>Internal Server Error</pre>\n</body>\n</html>\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=page, headers={"content-type": "text/html; charset=utf-8"})

    runtime = make_runtime(handler)
    with pytest.raises(LMStudioError) as plain:
        runtime.complete("spark", [{"role": "user", "content": "x"}])
    assert str(plain.value) == "HTTP 500 Internal Server Error" and plain.value.status == 500
    with pytest.raises(LMStudioError) as streamed:
        runtime.complete("spark", [{"role": "user", "content": "x"}], on_delta=lambda kind, text: None)
    assert str(streamed.value) == "HTTP 500 Internal Server Error"


def test_api_token_is_sent_as_bearer():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"models": []})

    LMStudioClient(api_token="secret-token", client=httpx.Client(transport=httpx.MockTransport(handler))).list_models()
    assert seen["auth"] == "Bearer secret-token"


# -- CLI parsing --------------------------------------------------------------


def test_estimate_parsing_handles_locale_number_formats():
    text = "Model: spark-x2.5-4b\nContext Length: 8.192\nEstimated GPU Memory:   4.65 GiB\nEstimated Total Memory: 4,65 GiB\n"
    estimate = parse_estimate(text)
    assert estimate.context_length == 8192 and estimate.gpu_memory_gb == 4.65 and estimate.total_memory_gb == 4.65


def test_runtime_ls_returns_selected_engines():
    text = (
        "LLM ENGINE                                   SELECTED    MODEL FORMAT\n"
        "llama.cpp-win-x86_64-avx2@2.43.0                             GGUF\n"
        "llama.cpp-win-x86_64-nvidia-cuda-avx2@2.53.0    \u2713            GGUF\n"
    )
    assert parse_runtime_ls(text) == ["llama.cpp-win-x86_64-nvidia-cuda-avx2@2.53.0"]


def test_ansi_is_stripped_and_cli_errors_are_typed():
    assert strip_ansi("\x1b[38;5;166mLM Studio\x1b[0m") == "LM Studio"
    with pytest.raises(LmsCliError, match="something went wrong"):
        FakeCli(fail=True).ls()
    with pytest.raises(LmsCliError, match="not installed"):
        LmsCli(None).ls()


# -- MTP: speculative decoding through the model's own head ------------------------------------------

MTP_MODEL = "qwen3.8-27b-gsq-rco-mtp"
MTP_FILE = "ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-IQ3_S-MTP/Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf"
MTP_LS = [{"type": "llm", "modelKey": MTP_MODEL, "format": "gguf", "path": MTP_FILE, "sizeBytes": 12_120_016_960, "architecture": "qwen35",
           "quantization": {"name": "IQ3_S", "bits": 3}}]
MTP_REFUSAL = "Error: MTP speculative decoding requires a GGUF model with a bundled supported MTP head."
LOADED_MTP_MODEL = {"models": [{"type": "llm", "key": MTP_MODEL, "loaded_instances": [{"id": MTP_MODEL, "config": {"context_length": 8192}}]}]}


def mtp_runtime(tmp_path, *, mtp_layers: int | None = 1, load_error: str | None = None, rest_models: dict[str, Any] | None = None,
                settings: Settings | None = None, vram_mib: float | None = 16376.0):
    """A runtime whose models folder holds one Qwen3.8 file, with or without an MTP head. Returns it, its CLI and what the server saw."""
    write_gguf(tmp_path / MTP_FILE, mtp_layers=mtp_layers)
    seen: dict[str, Any] = {"paths": [], "bodies": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["paths"].append(request.url.path)
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=rest_models or {"models": []})
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/api/v1/models/load":
            return httpx.Response(200, json={"type": "llm", "instance_id": MTP_MODEL, "load_time_seconds": 2.0, "load_config": {"context_length": 8192}})
        seen["bodies"].append(body)
        if body.get("stream"):
            chunk = {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 1}}
            return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    cli = FakeCli(ls=MTP_LS, load_error=load_error)
    client = LMStudioClient("http://127.0.0.1:1234", client=httpx.Client(transport=httpx.MockTransport(handler)))
    install = LMStudioInstall(installed=True, home="C:/lm", cli_path="lms.exe", models_dir=str(tmp_path), server_port=1234)
    runtime = LMStudioRuntime(settings or Settings(), client=client, cli=cli, install=install, total_vram_mib=lambda: vram_mib)
    return runtime, cli, seen


MTP_LOAD = ["load", MTP_MODEL, "-y", "-c", "16384", "--gpu", "max", "--parallel", "1", "--speculative-draft-mtp",
            "--speculative-draft-max-tokens", "2", "--speculative-draft-min-continue-probability", "0"]


def test_a_model_with_an_mtp_head_is_loaded_with_speculative_decoding_on_one_slot_and_full_offload(tmp_path):
    runtime, cli, seen = mtp_runtime(tmp_path)
    result = runtime.load(MTP_MODEL, context_length=16384)
    assert MTP_LOAD in cli.commands
    assert result.speculative == "mtp" and "/api/v1/models/load" not in seen["paths"]


def test_requests_to_an_mtp_model_carry_a_repeat_penalty_of_one_and_other_models_get_lm_studios_own(tmp_path):
    runtime, _, seen = mtp_runtime(tmp_path)
    runtime.load(MTP_MODEL, context_length=16384)
    messages = [{"role": "user", "content": "x"}]
    runtime.complete(MTP_MODEL, messages)
    assert seen["bodies"][-1]["repeat_penalty"] == 1.0
    streamed: list[str] = []
    runtime.complete(MTP_MODEL, messages, on_delta=lambda kind, text: streamed.append(text))
    assert seen["bodies"][-1]["repeat_penalty"] == 1.0 and seen["bodies"][-1]["stream"] is True and streamed == ["ok"]
    runtime.complete("some-other-model", messages)
    assert "repeat_penalty" not in seen["bodies"][-1]


def test_a_model_without_an_mtp_head_is_loaded_as_it_always_was(tmp_path):
    runtime, cli, seen = mtp_runtime(tmp_path, mtp_layers=None)
    result = runtime.load(MTP_MODEL, context_length=8192)
    assert result.speculative is None and "/api/v1/models/load" in seen["paths"]
    assert not any(command[:1] == ["load"] for command in cli.commands)
    runtime.complete(MTP_MODEL, [{"role": "user", "content": "x"}])
    assert "repeat_penalty" not in seen["bodies"][-1]


def test_the_setting_turns_mtp_off_and_sets_how_many_tokens_are_guessed(tmp_path):
    off = Settings()
    off.runtime.speculative_mtp = False
    runtime, cli, seen = mtp_runtime(tmp_path, settings=off)
    assert runtime.load(MTP_MODEL, context_length=16384).speculative is None
    assert "/api/v1/models/load" in seen["paths"] and not any("--speculative-draft-mtp" in command for command in cli.commands)

    three = Settings()
    three.runtime.speculative_draft_tokens = 3
    runtime, cli, _ = mtp_runtime(tmp_path, settings=three)
    runtime.load(MTP_MODEL, context_length=16384)
    assert next(c for c in cli.commands if c[:1] == ["load"])[-4:-2] == ["--speculative-draft-max-tokens", "3"]
    with pytest.raises(ValidationError):
        RuntimeSettings(speculative_draft_tokens=0)


def test_lm_studio_refusing_mtp_is_not_a_failure_and_is_not_asked_again(tmp_path):
    runtime, cli, _ = mtp_runtime(tmp_path, load_error=MTP_REFUSAL)
    result = runtime.load(MTP_MODEL, context_length=16384)
    assert result.speculative is None
    assert [c for c in cli.commands if c[:1] == ["load"]] == [MTP_LOAD, ["load", MTP_MODEL, "-y", "-c", "16384"]]
    runtime.load(MTP_MODEL, context_length=16384)
    assert sum("--speculative-draft-mtp" in command for command in cli.commands) == 1


def test_a_failure_that_has_nothing_to_do_with_mtp_is_not_blamed_on_it(tmp_path):
    runtime, cli, seen = mtp_runtime(tmp_path, load_error="Error: not enough memory to load the model")
    result = runtime.load(MTP_MODEL, context_length=16384)
    assert result.speculative is None and "/api/v1/models/load" in seen["paths"]  # the REST load was tried instead
    runtime.load(MTP_MODEL, context_length=16384)
    assert sum("--speculative-draft-mtp" in command for command in cli.commands) == 2


def test_a_model_the_person_loaded_in_lm_studio_counts_as_running_its_mtp_head(tmp_path):
    runtime, cli, seen = mtp_runtime(tmp_path, rest_models=LOADED_MTP_MODEL)
    result = runtime.load(MTP_MODEL)
    assert result.already_loaded and result.speculative == "mtp" and not any(command[:1] == ["load"] for command in cli.commands)
    runtime.complete(MTP_MODEL, [{"role": "user", "content": "x"}])
    assert seen["bodies"][-1]["repeat_penalty"] == 1.0
    runtime.unload(MTP_MODEL)
    runtime.complete(MTP_MODEL, [{"role": "user", "content": "x"}])
    assert "repeat_penalty" not in seen["bodies"][-1]


def test_full_offload_is_asked_only_for_a_model_that_fits_the_card_and_never_over_a_ratio_the_caller_chose(tmp_path):
    for vram in (2000.0, None):  # a card too small for the file and its working room, and a card that cannot be read
        runtime, cli, _ = mtp_runtime(tmp_path, vram_mib=vram)
        runtime.load(MTP_MODEL, context_length=16384)
        command = next(c for c in cli.commands if c[:1] == ["load"])
        assert "--speculative-draft-mtp" in command and "--gpu" not in command
    runtime, cli, _ = mtp_runtime(tmp_path)
    runtime.load(MTP_MODEL, context_length=16384, gpu="0.5")
    assert next(c for c in cli.commands if c[:1] == ["load"])[5:7] == ["--gpu", "0.5"]
