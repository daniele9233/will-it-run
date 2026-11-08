"""Video progress belongs to one prompt and never turns socket loss into job failure."""
from types import SimpleNamespace

import pytest

from agentfit.imagegen.video_progress import ProgressStream, VideoProgress


def tracker(*samplers):
    graph = {node: {"class_type": "KSamplerAdvanced"} for node in samplers}
    graph.update({"13": {"class_type": "VAEDecode"}, "14": {"class_type": "CreateVideo"}, "15": {"class_type": "SaveVideo"}})
    progress = VideoProgress(graph)
    progress.prompt_id = "our-video"
    return progress


def event(kind, **data):
    return {"type": kind, "data": {"prompt_id": "our-video", **data}}


@pytest.mark.parametrize("prompt_id", [None, "another-video", "an-image"])
def test_foreign_or_unidentified_progress_is_never_displayed(prompt_id):
    progress = tracker("11", "12")
    assert not progress.event(event("progress", prompt_id=prompt_id, node="11", value=10, max=10))
    assert progress.percent == 0


def test_two_experts_advance_monotonically_and_wait_for_persistence():
    progress = tracker("11", "12")
    first = progress.event(event("progress", node="11", value=10, max=10))
    second = progress.event(event("progress", node="12", value=5, max=10))
    assert first["percent"] < second["percent"] < 100
    assert second["step"] == 5 and second["total"] == 10
    late = progress.event(event("progress", node="11", value=1, max=10))
    assert late["percent"] == second["percent"]
    decoded = progress.event(event("executing", node="13"))
    encoded = progress.event(event("executing", node="15"))
    assert second["percent"] < decoded["percent"] <= encoded["percent"] < 100


def test_one_sampler_and_cached_samplers_are_supported():
    progress = tracker("11")
    assert progress.event(event("executing", node="11"))["stage"] == "sampling"
    assert progress.event(event("progress", value=3, max=4))["step"] == 3
    completed = progress.event(event("execution_cached", nodes=["11"]))
    assert completed["percent"] < 100
    assert completed["step"] is None


@pytest.mark.parametrize("message", [None, {}, {"data": []},
    event("progress", node="11", value=True, max=4),
    event("progress", node="11", value=1, max=0),
    event("progress", node="11", value=5, max=4),
    event("progress", node="11", value=-1, max=4),
    event("progress", node="loader", value=1, max=4),
    event("execution_cached", nodes=None),
])
def test_malformed_events_do_not_fabricate_progress(message):
    assert not tracker("11").event(message)


def test_unavailable_socket_keeps_the_http_generation_path_working(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr("agentfit.imagegen.video_progress.connect", unavailable)
    stream = ProgressStream(SimpleNamespace(base_url="http://localhost:8188", client_id="video"))
    assert stream.poll() == []
    stream.close()


def test_stream_filters_previews_and_invalid_json_and_closes_on_disconnect(monkeypatch):
    messages = iter([b"preview", "not-json", '{"type":"execution_start","data":{"prompt_id":"our-video"}}'])
    closed = []

    def receive(**kwargs):
        try:
            return next(messages)
        except StopIteration:
            raise OSError("disconnected") from None

    socket = SimpleNamespace(recv=receive, close=lambda: closed.append(True))
    addresses = []

    def connect(address, **kwargs):
        addresses.append(address)
        return socket

    monkeypatch.setattr("agentfit.imagegen.video_progress.connect", connect)
    stream = ProgressStream(SimpleNamespace(base_url="https://localhost:8188/comfy/", client_id="video id"))
    assert stream.poll() == [event("execution_start")]
    assert addresses == ["wss://localhost:8188/comfy/ws?clientId=video+id"]
    assert closed == [True]
    assert stream.poll() == []
    stream.close()
    assert closed == [True]
