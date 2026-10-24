"""Local benchmark: measure a model on this PC and store the result.

Timing numbers (tokens/s, time to first token, load time) come from LM Studio's own
``stats`` block. VRAM, RAM and GPU utilization are sampled from nvidia-smi and the OS
while the model generates. Prompt-processing speed is derived as input tokens divided by
time to first token, so it is labelled approximate.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import psutil
from pydantic import BaseModel, Field

from agentfit.db import Database, utcnow
from agentfit.events import EventBus
from agentfit.events import bus as default_bus
from agentfit.hardware import nvidia
from agentfit.lmstudio.client import LMStudioError
from agentfit.lmstudio.runtime import LMStudioRuntime
from agentfit.logging_setup import get_logger, log_event

log = get_logger("benchmark")

WARMUP_PROMPT = "Reply with the single word: ready"
# A fixed, moderately long prompt so prompt-processing speed is measurable and comparable.
BENCH_PROMPT = (
    "You are reviewing a design for a job queue service. The service accepts jobs over HTTP, stores them "
    "in PostgreSQL, and a pool of workers claims jobs with SELECT ... FOR UPDATE SKIP LOCKED. Each job has "
    "a type, a JSON payload, a priority from 0 to 9, a not-before timestamp, an attempt counter and a "
    "maximum number of attempts. Failed jobs are retried with exponential backoff and jitter. Workers send "
    "a heartbeat every ten seconds; a reaper process requeues jobs whose heartbeat is older than one "
    "minute. Completed jobs are moved to an archive table by a nightly task. Operators need to pause a job "
    "type, drain a worker for deployment, and inspect why a job is stuck. The team is deciding between "
    "keeping this design or moving to a message broker.\n\n"
    "Write a thorough technical assessment. Cover: correctness risks in the claim and heartbeat logic, "
    "what happens during a database failover, how priorities interact with starvation, index design for "
    "the claim query, how to make handlers idempotent, observability the operators will need, and the "
    "conditions under which a message broker would be the better choice. Use numbered sections and be "
    "specific about failure modes."
)

ProgressFn = Callable[[str, dict[str, Any]], None]


class BenchmarkError(Exception):
    pass


class BenchmarkResult(BaseModel):
    id: int | None = None
    model_key: str
    quant: str | None = None
    context_length: int | None = None
    load_time_s: float | None = None
    ttft_ms: float | None = None
    prompt_tps: float | None = None
    generation_tps: float | None = None
    vram_used_mb: float | None = None
    ram_used_mb: float | None = None
    gpu_util_percent: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    hardware_fingerprint: str | None = None
    created_at: str = ""
    notes: list[str] = Field(default_factory=list)


class _Sampler:
    """Polls GPU and RAM usage on a background thread, keeping the peaks."""

    def __init__(self, interval: float = 0.4) -> None:
        self.interval = interval
        self.peak_vram_mb = 0.0
        self.peak_util = 0.0
        self.peak_ram_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _sample(self) -> None:
        for gpu in nvidia.live_usage():
            self.peak_vram_mb = max(self.peak_vram_mb, float(gpu.get("vram_used_mb") or 0))
            self.peak_util = max(self.peak_util, float(gpu.get("utilization_percent") or 0))
        vm = psutil.virtual_memory()
        self.peak_ram_bytes = max(self.peak_ram_bytes, int(vm.total - vm.available))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval)

    def __enter__(self) -> _Sampler:
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=3)


def _baseline() -> tuple[float, int]:
    vram = sum(float(g.get("vram_used_mb") or 0) for g in nvidia.live_usage()[:1])
    vm = psutil.virtual_memory()
    return vram, int(vm.total - vm.available)


class BenchmarkEngine:
    def __init__(self, db: Database, runtime: LMStudioRuntime, *, bus: EventBus | None = None) -> None:
        self.db = db
        self.runtime = runtime
        self.bus = bus or default_bus

    def run(
        self,
        model_key: str,
        *,
        context_length: int | None = 8192,
        max_output_tokens: int = 256,
        unload_others: bool = True,
        keep_loaded: bool = False,
        hardware_fingerprint: str | None = None,
        progress: ProgressFn | None = None,
    ) -> BenchmarkResult:
        def report(stage: str, **data: Any) -> None:
            self.bus.publish("benchmark.progress", {"model_key": model_key, "stage": stage, **data})
            if progress:
                progress(stage, data)

        if not self.runtime.is_available():
            raise BenchmarkError("LM Studio's local server is not running. Start it, then benchmark again.")
        installed = {m.model_key: m for m in self.runtime.list_installed()}
        model = installed.get(model_key)
        if model is None:
            raise BenchmarkError(f"Model '{model_key}' is not installed in LM Studio.")
        notes: list[str] = []

        loaded = self.runtime.list_loaded()
        was_loaded = any(i.model_key == model_key for i in loaded)
        if unload_others:
            for inst in loaded:
                if inst.model_key != model_key:
                    report("unloading", instance=inst.instance_id)
                    self.runtime.unload(inst.instance_id)
        elif any(i.model_key != model_key for i in loaded):
            notes.append("Other models were loaded during the benchmark; memory figures include them.")

        base_vram, base_ram = _baseline() if not was_loaded else (None, None)
        try:
            report("loading")
            load = self.runtime.load(model_key, context_length=context_length)
            if load.already_loaded:
                notes.append("Model was already loaded: load time and memory delta were not measured.")
            report("warmup")
            self.runtime.measure(load.instance_id, WARMUP_PROMPT, max_output_tokens=8)
            report("generating")
            with _Sampler() as sampler:
                started = time.perf_counter()
                chat = self.runtime.measure(load.instance_id, BENCH_PROMPT, max_output_tokens=max_output_tokens)
                elapsed = time.perf_counter() - started
        except LMStudioError as exc:
            raise BenchmarkError(f"Benchmark failed: {exc}") from exc

        stats = chat.stats
        ttft = stats.time_to_first_token_seconds
        prompt_tps = None
        if ttft and stats.input_tokens and ttft > 0:
            prompt_tps = stats.input_tokens / ttft
            notes.append("Prompt speed is approximate (input tokens / time to first token).")
        generation_tps = stats.tokens_per_second
        if generation_tps is None and stats.output_tokens and elapsed > (ttft or 0):
            generation_tps = stats.output_tokens / (elapsed - (ttft or 0))
            notes.append("Generation speed computed from wall-clock time.")

        result = BenchmarkResult(
            model_key=model_key,
            quant=model.quant,
            context_length=context_length,
            load_time_s=None if load.already_loaded else load.load_time_seconds,
            ttft_ms=round(ttft * 1000, 1) if ttft is not None else None,
            prompt_tps=round(prompt_tps, 1) if prompt_tps else None,
            generation_tps=round(generation_tps, 2) if generation_tps else None,
            vram_used_mb=round(sampler.peak_vram_mb - base_vram, 0) if base_vram is not None else None,
            ram_used_mb=round((sampler.peak_ram_bytes - base_ram) / 1024**2, 0) if base_ram is not None else None,
            gpu_util_percent=round(sampler.peak_util, 0) if sampler.peak_util else None,
            input_tokens=stats.input_tokens,
            output_tokens=stats.output_tokens,
            hardware_fingerprint=hardware_fingerprint,
            created_at=utcnow(),
            notes=notes,
        )
        if not keep_loaded and not was_loaded:
            try:
                self.runtime.unload(load.instance_id)
            except LMStudioError:
                notes.append("Could not unload the model after the benchmark.")
        result.id = self._store(result)
        log_event(log, "benchmark complete", model=model_key, tps=result.generation_tps, ttft_ms=result.ttft_ms)
        report("done", result=result.model_dump())
        self.bus.publish("notify", {
            "title": "Benchmark complete",
            "body": f"{model.display_name}: {result.generation_tps or 0:.1f} tok/s",
        })
        return result

    def _store(self, result: BenchmarkResult) -> int:
        cursor = self.db.execute(
            "INSERT INTO benchmark(model_key, quant, context_length, load_time_s, ttft_ms, prompt_tps, generation_tps, "
            "vram_used_mb, ram_used_mb, gpu_util_percent, output_tokens, hardware_fingerprint, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.model_key, result.quant, result.context_length, result.load_time_s, result.ttft_ms,
                result.prompt_tps, result.generation_tps, result.vram_used_mb, result.ram_used_mb,
                result.gpu_util_percent, result.output_tokens, result.hardware_fingerprint,
                json.dumps({"notes": result.notes, "input_tokens": result.input_tokens}), result.created_at,
            ),
        )
        return int(cursor.lastrowid or 0)

    def history(self, model_key: str | None = None, limit: int = 100) -> list[BenchmarkResult]:
        sql = "SELECT * FROM benchmark"
        params: tuple[Any, ...] = ()
        if model_key:
            sql += " WHERE model_key = ?"
            params = (model_key,)
        rows = self.db.query(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*params, limit))
        out: list[BenchmarkResult] = []
        for row in rows:
            details = json.loads(row.pop("details") or "{}")
            out.append(BenchmarkResult(**row, notes=details.get("notes", []), input_tokens=details.get("input_tokens")))
        return out
