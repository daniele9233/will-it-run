"""Running an Arena: the same questions put to each model in turn, one model in memory at a time.

``ArenaService.start`` checks the request, builds the suite, writes the run and its questions to the database and hands the
work to a thread. The thread takes the models one after the other through the model gateway (which loads one, and unloads
it before the next), asks every question in the same words with the same sampling, has the verifier judge the reply, and
saves the verdict at once, so a stopped or crashed run keeps what it had. ``resume`` asks what is missing.

Nothing is judged by a model. A reply that the runner could not get (the server answered an error, the model did not
load) is a *technical error*: it is shown, it is asked again on resume, and it never counts against the model.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentfit.arena import text as tx
from agentfit.arena.categories import CATEGORIES, PRESETS
from agentfit.arena.model import Answer, Question, ToolCall, Verdict, why
from agentfit.arena.store import ArenaStore, new_run_id
from agentfit.arena.suite import DEFAULT_SEED, Suite, build_suite
from agentfit.arena.toolprobe import Tools
from agentfit.arena.verifiers import Context, verify
from agentfit.cloud.providers import is_cloud, label_of, parse_key
from agentfit.db import utcnow
from agentfit.events import EventBus
from agentfit.i18n import tr
from agentfit.logging_setup import get_logger, log_event
from agentfit.workflows.dsl import LoadingPolicy, ModelSlot

log = get_logger("arena")

DEFAULT_CONTEXT = 8192
# The pack with the long texts needs room for them and for the answer.
LONG_CONTEXT = 16384
MAX_MODELS = 8
# After this many technical errors in a row, a model is left: the server is broken, and asking on would only fill the run with errors.
MAX_STRAIGHT_ERRORS = 3
# The text of an answer is sent to the screen at most this often, however fast the model writes.
DELTA_INTERVAL = 0.12

Emit = Callable[[str, dict[str, Any]], None]


class ArenaError(Exception):
    """The request cannot be run. ``code`` tells the interface what to ask the person."""

    def __init__(self, message: str, code: str = "invalid", status: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra


class _Cancelled(Exception):
    pass


class _TimedOut(Exception):
    pass


class RunRequest(BaseModel):
    """What the person chose. The defaults are the ones that make runs comparable: greedy decoding, one answer per question."""

    model_config = ConfigDict(protected_namespaces=())

    models: list[str] = Field(min_length=1, max_length=MAX_MODELS)
    seed: int = DEFAULT_SEED
    preset: str | None = "standard"
    categories: list[str] | None = None
    repeats: int = Field(default=1, ge=1, le=10)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1, le=1000)
    repeat_penalty: float = Field(default=1.0, ge=0.5, le=2.0)
    # None: the suite's seed. Each repeat of a question adds its number to it.
    sampling_seed: int | None = None
    max_tokens: int = Field(default=2048, ge=128, le=32768)
    # None: 8192, or 16384 when the pack has the long texts. The same for every model.
    context: int | None = Field(default=None, ge=2048, le=262144)
    timeout_s: int = Field(default=600, ge=30, le=3600)
    title: str = ""
    # Models LM Studio already holds are unloaded to measure one at a time: the person has to agree.
    confirm_unload: bool = False
    # A cloud model costs money and sends the questions to its provider: the person has to agree.
    confirm_cloud: bool = False

    @field_validator("models")
    @classmethod
    def _distinct(cls, value: list[str]) -> list[str]:
        cleaned = [m.strip() for m in value if m and m.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("the same model is listed twice")
        if not cleaned:
            raise ValueError("choose at least one model")
        return cleaned


# -- reading a model's reply ----------------------------------------------------------------------


def read_response(response: dict[str, Any]) -> tuple[Answer, dict[str, Any]]:
    """An OpenAI-style completion as an ``Answer`` (the reasoning set apart, the tool calls parsed) and its token counts."""
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    raw = message.get("content") or ""
    if isinstance(raw, list):  # content in parts
        raw = "".join(str(part.get("text", "")) for part in raw if isinstance(part, dict))
    separate = str(message.get("reasoning_content") or message.get("reasoning") or "")
    text, inline = tx.strip_think(str(raw))
    calls: list[ToolCall] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        wrote = function.get("arguments")
        arguments: dict[str, Any] | None = None
        if isinstance(wrote, dict):
            arguments = wrote
        elif isinstance(wrote, str):
            try:
                value = json.loads(wrote) if wrote.strip() else {}
            except ValueError:
                value = None
            arguments = value if isinstance(value, dict) else None
        calls.append(ToolCall(name=str(function.get("name") or ""), arguments=arguments, raw=wrote if isinstance(wrote, str) else json.dumps(wrote)))
    usage = response.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    counts = {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"), "reasoning_tokens": details.get("reasoning_tokens")}
    answer = Answer(text=text, raw=str(raw), reasoning="\n".join(p for p in (separate, inline) if p), tool_calls=calls, finish_reason=choice.get("finish_reason"))
    return answer, counts


class _Deltas:
    """The text a model writes, handed to the screen a few times a second instead of once a word."""

    def __init__(self, emit: Emit, model: str, question: str) -> None:
        self.emit, self.model, self.question = emit, model, question
        self._buffer: dict[str, str] = {}
        self._last = 0.0

    def add(self, kind: str, text: str) -> None:
        self._buffer[kind] = self._buffer.get(kind, "") + text
        now = time.monotonic()
        if now - self._last >= DELTA_INTERVAL:
            self.flush()

    def flush(self) -> None:
        self._last = time.monotonic()
        buffered, self._buffer = self._buffer, {}
        for kind, text in buffered.items():
            self.emit("arena.delta", {"model": self.model, "question": self.question, "kind": kind, "text": text})


@dataclass
class _Active:
    run_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


@dataclass
class _Plan:
    suite: Suite
    request: RunRequest
    context: int
    models: list[dict[str, Any]]
    settings: dict[str, Any]


class ArenaService:
    """Starts, follows, stops and resumes runs. One run works at a time: it needs the graphics card to itself."""

    def __init__(
        self,
        store: ArenaStore,
        runtime: Any | Callable[[], Any],
        bus: EventBus,
        *,
        installed: Callable[[], list[Any]],
        gateway: Callable[[dict[str, ModelSlot], Emit], Any],
        tools: Tools | None = None,
        environment: Callable[[], dict[str, Any]] | None = None,
        workdir: Path | None = None,
    ) -> None:
        self.store = store
        # The runtime, or what returns the current one: the connection settings can change between two runs.
        self._runtime_source: Callable[[], Any] = runtime if callable(runtime) and not hasattr(runtime, "complete") else (lambda: runtime)
        self.bus = bus
        self._installed = installed
        self._gateway = gateway
        self.tools = tools or Tools()
        self._environment = environment or (lambda: {})
        self._workdir = workdir
        self._lock = threading.RLock()
        self._active: dict[str, _Active] = {}
        # Runs the app was working on when it closed are over: they can be resumed, not followed.
        self.store.interrupt_running()

    @property
    def runtime(self) -> Any:
        return self._runtime_source()

    @property
    def _local(self) -> Any:
        """What serves local models, whether or not cloud ones are served beside it."""
        return getattr(self.runtime, "local", self.runtime)

    # -- what is going on ---------------------------------------------------
    def busy(self) -> str | None:
        """The id of the run that is working, if any."""
        with self._lock:
            for run_id, active in self._active.items():
                if active.thread is not None and active.thread.is_alive():
                    return run_id
        return None

    def join(self, run_id: str, timeout: float | None = None) -> None:
        """Wait for a run's thread. For tests and for closing the app."""
        with self._lock:
            active = self._active.get(run_id)
        if active is not None and active.thread is not None:
            active.thread.join(timeout)

    def _publish(self, topic: str, payload: dict[str, Any], *, keep: bool = True) -> None:
        self.bus.publish(topic, payload, keep=keep)

    # -- planning -----------------------------------------------------------
    def _installed_by_key(self) -> dict[str, Any]:
        try:
            return {m.model_key: m for m in self._installed()}
        except Exception:
            return {}

    def _plan(self, request: RunRequest) -> _Plan:
        if request.preset is not None and request.preset not in PRESETS:
            raise ArenaError(tr("Unknown pack: {pack}. Use one of {packs}.", pack=request.preset, packs=", ".join(PRESETS)))
        unknown = [c for c in (request.categories or []) if c not in CATEGORIES]
        if unknown:
            raise ArenaError(tr("Unknown categories: {names}.", names=", ".join(unknown)))
        suite = build_suite(request.seed, request.preset, request.categories)
        if not suite.questions:
            raise ArenaError(tr("The chosen packs and categories hold no questions."))
        installed = self._installed_by_key()
        models: list[dict[str, Any]] = []
        for key in request.models:
            if is_cloud(key):
                try:
                    provider, _ = parse_key(key)
                except ValueError as exc:
                    raise ArenaError(str(exc)) from exc
                models.append({"model_key": key, "label": label_of(key), "cloud": True, "vision": bool(provider.vision),
                               "info": {"provider": provider.id}})
                continue
            row = installed.get(key)
            if row is None:
                raise ArenaError(tr("{model} is not installed in LM Studio. Choose a model from the list.", model=key), code="unknown_model", status=409)
            models.append({
                "model_key": key, "label": row.display_name or key, "cloud": False, "vision": bool(row.vision),
                "info": {"params": row.params_string, "quant": row.quant, "size_bytes": row.size_bytes, "architecture": row.architecture,
                         "tool_use": bool(row.tool_use), "reasoning": bool(getattr(row, "reasoning", False)), "max_context": row.max_context_length},
            })
        long_texts = any(q.category == "longctx" for q in suite.questions)
        wanted = request.context or (LONG_CONTEXT if long_texts else DEFAULT_CONTEXT)
        # The answer and the prompt share the window: twice the room for the longest answer is never too little.
        context = max(wanted, 2 * request.max_tokens)
        context = -(-context // 1024) * 1024
        for model in models:
            model["context_asked"] = context
        sampling_seed = request.sampling_seed if request.sampling_seed is not None else suite.seed
        settings = {
            "temperature": request.temperature, "top_p": request.top_p, "top_k": request.top_k, "repeat_penalty": request.repeat_penalty,
            "seed": sampling_seed, "repeats": request.repeats, "max_tokens": request.max_tokens, "context": context, "timeout_s": request.timeout_s,
            "same_prompt": True,
            # Cloud providers choose their own sampling: it is not sent, and the report has to say so.
            "cloud_sampling": "provider default" if any(m["cloud"] for m in models) else None,
        }
        return _Plan(suite, request, context, models, settings)

    def describe(self, request: RunRequest) -> dict[str, Any]:
        """What a request would ask, without starting it."""
        plan = self._plan(request)
        questions = len(plan.suite.questions)
        return {
            "questions": questions, "models": len(plan.models), "answers": questions * len(plan.models) * request.repeats,
            "counts": plan.suite.counts, "suite": {"id": plan.suite.id, "version": plan.suite.version, "hash": plan.suite.hash, "bank_hash": plan.suite.bank_hash},
            "context": plan.context, "cloud": [m["model_key"] for m in plan.models if m["cloud"]],
            "blind": [m["model_key"] for m in plan.models if not m["vision"] and any("vision" in q.needs for q in plan.suite.questions)],
        }

    # -- starting -----------------------------------------------------------
    def start(self, request: RunRequest) -> str:
        with self._lock:
            running = self.busy()
            if running:
                raise ArenaError(tr("Another Arena run is working: stop it, or wait for it to finish."), code="busy", status=409, run=running)
            plan = self._plan(request)
            cloud = [m["model_key"] for m in plan.models if m["cloud"]]
            if cloud and not request.confirm_cloud:
                raise ArenaError(tr("Cloud models send the questions to their provider and are paid by the answer."), code="needs_cloud", status=409, cloud=cloud)
            local = [m for m in plan.models if not m["cloud"]]
            loaded: list[dict[str, Any]] = []
            if local:
                if not self._local.is_available() and not self._local.ensure_server():
                    raise ArenaError(tr("LM Studio's local server is not running and could not be started."), code="offline", status=503)
                loaded = [{"instance_id": i.instance_id, "model_key": i.model_key} for i in self.runtime.list_loaded()]
                if loaded and not request.confirm_unload:
                    raise ArenaError(tr("LM Studio already holds models. To measure one model at a time they have to be unloaded."), code="needs_unload", status=409, loaded=loaded)
            run_id = new_run_id()
            environment = {**self._environment(), "tools": self.tools.report(), "suite": {"id": plan.suite.id, "version": plan.suite.version}}
            self.store.create_run(
                run_id, title=request.title.strip() or self._default_title(plan), suite=plan.suite, settings=plan.settings, environment=environment,
                models=plan.models, questions=[q.to_json(with_assets=True) for q in plan.suite.questions],
                total=len(plan.suite.questions) * len(plan.models) * request.repeats,
            )
            self._launch(run_id, unload_first=bool(loaded))
        log_event(log, "arena run started", run=run_id, models=len(plan.models), questions=len(plan.suite.questions))
        return run_id

    @staticmethod
    def _default_title(plan: _Plan) -> str:
        names = [m["label"] for m in plan.models]
        return " · ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")

    def _launch(self, run_id: str, *, unload_first: bool) -> None:
        active = _Active(run_id)
        active.thread = threading.Thread(target=self._work, args=(active, unload_first), daemon=True, name=f"arena-{run_id}")
        self._active[run_id] = active
        self.store.update_run(run_id, status="running", started_at=utcnow(), finished_at=None, note="")
        self._publish("arena.run", {"run_id": run_id, "status": "running"})
        active.thread.start()

    # -- stopping and resuming ---------------------------------------------
    def stop(self, run_id: str) -> bool:
        with self._lock:
            active = self._active.get(run_id)
        if active is not None and active.thread is not None and active.thread.is_alive():
            active.cancel.set()
            self.store.update_run(run_id, note="stopping")
            self._publish("arena.run", {"run_id": run_id, "status": "stopping"})
            return True
        return False

    def resume(self, run_id: str, *, confirm_unload: bool = False) -> None:
        with self._lock:
            run = self.store.get_run(run_id)
            if run is None:
                raise ArenaError(tr("This run does not exist."), code="missing", status=404)
            if self.busy():
                raise ArenaError(tr("Another Arena run is working: stop it, or wait for it to finish."), code="busy", status=409, run=self.busy())
            if run["status"] in ("queued", "running"):
                raise ArenaError(tr("This run is still working."), code="state", status=409)
            if self.store.count_results(run_id) >= run["total"] and not self.store.count_errors(run_id):
                raise ArenaError(tr("Every question was answered: there is nothing left to ask."), code="complete", status=409)
            models = self.store.models(run_id)
            keys = [m["model_key"] for m in models]
            pending_local = [k for k in keys if not is_cloud(k)]
            loaded: list[dict[str, Any]] = []
            if pending_local:
                if not self._local.is_available() and not self._local.ensure_server():
                    raise ArenaError(tr("LM Studio's local server is not running and could not be started."), code="offline", status=503)
                loaded = [{"instance_id": i.instance_id, "model_key": i.model_key} for i in self.runtime.list_loaded()]
                if loaded and not confirm_unload:
                    raise ArenaError(tr("LM Studio already holds models. To measure one model at a time they have to be unloaded."), code="needs_unload", status=409, loaded=loaded)
            self.store.forget_errors(run_id)
            for model in models:
                if model["status"] in ("failed", "stopped", "running"):
                    self.store.update_model(run_id, model["model_key"], status="pending", error=None)
            self._launch(run_id, unload_first=bool(loaded))

    def delete(self, run_id: str) -> bool:
        with self._lock:
            active = self._active.get(run_id)
            if active is not None and active.thread is not None and active.thread.is_alive():
                raise ArenaError(tr("This run is working: stop it first."), code="busy", status=409)
            self._active.pop(run_id, None)
            return self.store.delete_run(run_id)

    # -- the work ------------------------------------------------------------
    def _work(self, active: _Active, unload_first: bool) -> None:
        run_id = active.run_id
        folder = Path(tempfile.mkdtemp(prefix="arena-", dir=str(self._workdir) if self._workdir else None))
        manager: Any = None
        status, note = "done", ""
        try:
            run = self.store.get_run(run_id)
            assert run is not None
            questions = [Question.from_json(d) for d in self.store.all_questions(run_id)]
            models = self.store.models(run_id)
            settings = run["settings"]
            slots = {
                f"m{m['position']}": ModelSlot(label=m["label"], model_key=m["model_key"], context_length=int(settings["context"]),
                                                loading_policy=LoadingPolicy.UNLOAD_AFTER_USE)
                for m in models
            }
            free_before: dict[str, float] = {}
            manager = self._gateway(slots, lambda topic, payload: self._on_gateway(run_id, free_before, topic, payload))
            if unload_first:
                self._unload_foreign(run_id)
            ctx = Context(tools=self.tools, workdir=folder, cancelled=active.cancel.is_set)
            for model in models:
                if active.cancel.is_set():
                    break
                self._run_model(active, run, model, f"m{model['position']}", questions, settings, manager, ctx)
            if active.cancel.is_set():
                status = "stopped"
            else:
                states = [m["status"] for m in self.store.models(run_id)]
                status = "failed" if states and all(s == "failed" for s in states) else "done"
        except Exception as exc:
            log.exception("arena run crashed")
            status, note = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            if manager is not None:
                with contextlib.suppress(Exception):
                    manager.close()
            shutil.rmtree(folder, ignore_errors=True)
            self.store.update_run(run_id, status=status, finished_at=utcnow(), note=note, done=self.store.count_results(run_id))
            self._publish("arena.run", {"run_id": run_id, "status": status, "note": note})
            log_event(log, "arena run ended", run=run_id, status=status)

    def _on_gateway(self, run_id: str, free_before: dict[str, float], topic: str, payload: dict[str, Any]) -> None:
        """What the gateway says while it swaps models goes to the screen, and the graphics memory a model took is kept with it."""
        self._publish("arena.model", {"run_id": run_id, "topic": topic, **payload})
        try:
            self._note_memory(run_id, free_before, payload)
        except Exception:
            log.exception("arena: the memory a model took could not be kept")

    def _note_memory(self, run_id: str, free_before: dict[str, float], payload: dict[str, Any]) -> None:
        """Free VRAM before the last load and after it, as the gateway measured them: what the model holds at the context it got
        (the file, its cache and the runtime's own share). The same measurement for every model of the run, so quantizations compare."""
        phase, key = payload.get("phase"), payload.get("to")
        if not key or payload.get("cloud"):
            return
        if phase == "loading" and payload.get("vram_free_mib") is not None:
            free_before[key] = float(payload["vram_free_mib"])
        elif phase == "ready" and not payload.get("already_loaded"):
            fields: dict[str, Any] = {"partial_offload": bool(payload.get("partial_offload")), "saturated": bool(payload.get("saturated"))}
            after = payload.get("vram_free_mib")
            if key in free_before and after is not None and free_before[key] - float(after) > 0:
                fields["vram_mib"] = round(free_before[key] - float(after))
                fields["vram_total_mib"] = payload.get("vram_total_mib")
            self.store.merge_model_info(run_id, key, **fields)

    def _unload_foreign(self, run_id: str) -> None:
        for instance in self.runtime.list_loaded():
            with contextlib.suppress(Exception):
                self.runtime.unload(instance.instance_id)
                self._publish("arena.model", {"run_id": run_id, "topic": "model.switch", "phase": "unloaded", "model": instance.model_key})

    def _run_model(self, active: _Active, run: dict[str, Any], model: dict[str, Any], slot: str, questions: list[Question], settings: dict[str, Any],
                   manager: Any, ctx: Context) -> None:
        run_id, key = run["id"], model["model_key"]
        done = self.store.result_keys(run_id)
        repeats = int(settings["repeats"])
        sees = bool(model["vision"])
        pending = [(q, r) for q in questions for r in range(repeats) if (key, q.id, r) not in done]
        if not pending:
            self.store.update_model(run_id, key, status="done", finished_at=utcnow())
            return
        self.store.update_model(run_id, key, status="running", started_at=utcnow(), error=None)
        self._publish("arena.model", {"run_id": run_id, "topic": "arena.model", "phase": "start", "model": key})
        # What the model cannot do is not asked: it is skipped, and it says so, so the model is not failed for it.
        askable: list[tuple[Question, int]] = []
        for q, r in pending:
            if "vision" in q.needs and not sees:
                verdict = Verdict.skipped(why("This model cannot see pictures, so the question was not asked."))
                self._save(run_id, key, q, r, {"verdict": verdict})
            else:
                askable.append((q, r))
        if not askable:
            self.store.update_model(run_id, key, status="done", finished_at=utcnow())
            return
        waited = time.perf_counter()
        try:
            lease = manager.acquire(slot)
        except Exception as exc:
            message = str(exc)
            log.warning("arena: could not load %s: %s", key, message)
            self.store.update_model(run_id, key, status="failed", finished_at=utcnow(), error=message[:600])
            self._publish("arena.model", {"run_id": run_id, "topic": "arena.model", "phase": "failed", "model": key, "message": message})
            return
        # What it took to have the model ready: unloading the one before, loading this one (nothing, for a cloud model).
        self.store.update_model(run_id, key, context_granted=lease.context_length, load_seconds=round(time.perf_counter() - waited, 2))
        try:
            straight = 0
            for q, r in askable:
                if active.cancel.is_set():
                    break
                outcome = self._ask(active, run, key, lease.instance_id, q, r, settings, ctx)
                if outcome is None:  # stopped in the middle of an answer: nothing of it is kept
                    break
                straight = straight + 1 if outcome == "error" else 0
                if straight >= MAX_STRAIGHT_ERRORS:
                    self.store.update_model(run_id, key, status="failed", finished_at=utcnow(), error="The server kept answering with errors, so the model was left out.")
                    return
            saved = self.store.result_keys(run_id)
            finished = all((key, q.id, r) in saved for q, r in pending)
            self.store.update_model(run_id, key, status="done" if finished else "stopped", finished_at=utcnow())
        finally:
            with contextlib.suppress(Exception):
                manager.release(slot)

    def _ask(self, active: _Active, run: dict[str, Any], key: str, instance: str, q: Question, repeat: int, settings: dict[str, Any], ctx: Context) -> str | None:
        """Ask one question and save the verdict. Returns the verdict's status, or None if the run was stopped before the answer came."""
        run_id = run["id"]
        deltas = _Deltas(lambda topic, payload: self._publish(topic, {"run_id": run_id, **payload}, keep=False), key, q.id)
        started = time.perf_counter()
        first: list[float] = []

        def on_delta(kind: str, text: str) -> None:
            now = time.perf_counter()
            if not first:
                first.append(now)
            if active.cancel.is_set():
                raise _Cancelled()
            if now - started > float(settings["timeout_s"]):
                raise _TimedOut()
            deltas.add(kind, text)

        sampling = {"seed": int(settings["seed"]) + repeat, "top_p": settings.get("top_p"), "top_k": settings.get("top_k"), "repeat_penalty": settings.get("repeat_penalty")}
        self._publish("arena.progress", {"run_id": run_id, "model": key, "question": q.id, "repeat": repeat, "phase": "asking", "done": self.store.count_results(run_id), "total": run["total"]})
        try:
            response = self.runtime.complete(
                instance, q.chat_messages(), tools=list(q.tools) if q.tools else None, response_format=q.response_format,
                temperature=float(settings["temperature"]), max_tokens=max(q.max_tokens, int(settings["max_tokens"])), on_delta=on_delta, sampling=sampling,
            )
        except _Cancelled:
            return None
        except _TimedOut:
            verdict = Verdict.error(why("There was no answer within {seconds} seconds.", seconds=int(settings["timeout_s"])))
            return self._finish(run, key, q, repeat, {"verdict": verdict, "duration_s": time.perf_counter() - started, "error": "timeout"})
        except Exception as exc:
            verdict = Verdict.error(why("The server could not answer: {error}", error=str(exc)[:300] or type(exc).__name__))
            return self._finish(run, key, q, repeat, {"verdict": verdict, "duration_s": time.perf_counter() - started, "error": str(exc)[:600]})
        finally:
            deltas.flush()
        ended = time.perf_counter()
        answer, counts = read_response(response)
        verdict = verify(q, answer, ctx)
        if active.cancel.is_set() and verdict.status == "error":
            return None
        output = counts.get("output_tokens")
        generating = ended - first[0] if first else None
        result = {
            "verdict": verdict, "answer": answer.text, "raw": answer.raw, "reasoning": answer.reasoning, "finish_reason": answer.finish_reason,
            "tool_calls": [{"name": c.name, "arguments": c.arguments, "raw": c.raw} for c in answer.tool_calls], **counts,
            "ttft_s": (first[0] - started) if first else None, "duration_s": ended - started,
            "tps": round(output / generating, 2) if output and generating and generating > 0 else None,
        }
        return self._finish(run, key, q, repeat, result)

    def _save(self, run_id: str, key: str, q: Question, repeat: int, result: dict[str, Any]) -> None:
        verdict: Verdict = result["verdict"]
        self.store.save_result(run_id, key, q.id, repeat, {**result, "verdict": verdict.to_json(), "status": verdict.status, "score": verdict.score, "kind": verdict.kind, "method": verdict.method})

    def _finish(self, run: dict[str, Any], key: str, q: Question, repeat: int, result: dict[str, Any]) -> str:
        run_id = run["id"]
        self._save(run_id, key, q, repeat, result)
        verdict: Verdict = result["verdict"]
        done = self.store.count_results(run_id)
        self.store.update_run(run_id, done=done)
        self._publish("arena.progress", {
            "run_id": run_id, "model": key, "question": q.id, "repeat": repeat, "phase": "answered", "done": done, "total": run["total"],
            "status": verdict.status, "score": verdict.score, "kind": verdict.kind, "tps": result.get("tps"),
        })
        return verdict.status

