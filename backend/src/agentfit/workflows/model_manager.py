"""The model gateway: every model call of a workflow run goes through it.

A workflow may use several models (one per slot). They rarely fit in graphics memory
together, so the gateway swaps them, and it does so by measuring instead of assuming:

1. Ask the runtime what is loaded. The right model with enough context: use it.
2. Otherwise free memory. Other models stay only when the incoming one measurably
   fits next to them at its full context; a context is never shrunk to keep a
   neighbour loaded.
3. Measure free VRAM. Taken at this point the figure includes whatever the desktop
   and the browser hold right now.
4. Choose the largest context that fits: the model's fixed cost plus its cost per
   1000 tokens of context, with a margin. Those two numbers start from the runtime's
   own estimate and are replaced by what real loads take. On this project's test PC
   the estimate for a 27B model was 836 MiB too high and counted 100 MiB per 1000
   tokens where a real load takes 40, so the first load of a model that came out
   smaller than asked is repeated once at the context the measurement allows; two
   loads at different contexts give the true line, which is kept for next time.
5. Load with that context and an idle timeout, so a crash cannot leave a model in
   memory for ever.
6. Measure again. When almost no VRAM is left, Windows does not fail: the driver
   spills the model into system RAM and it keeps answering several times slower.
   That is treated as a failed load: unload, retry with a smaller context.
7. A model that nearly fills the card by itself gets the smallest context from step 4,
   which is too little for an agent that calls tools, while on the same PC it was
   measured to run at full speed with twelve times that: Windows hands the model the
   VRAM idle desktop programs hold. So when a request needs more than the plan allows,
   or an earlier load already proved more, the gateway loads past the plan, by at most
   ``stretch_mib``, and keeps the result only if the model answers at its usual speed.
   The free-VRAM figure cannot tell: it read 560 MiB both at 61 tokens per second and,
   with a context that really overflowed, at 12.
8. What does tell is the GPU memory the model's process holds in system RAM, which Windows
   counts per process (``spill_probe``). It is read after every load, and again while the
   model works: other programs take VRAM as time passes, and a load that fit can start to
   overflow an hour later. Seen twice in a row above ``spill_mib``, the overflow makes the
   gateway reload the model with half the context; at the smallest context it says so.

A request too large for the slot's model can fall back to the slot's wide-context
variant, typically the same model at a smaller quantization, whose saved memory becomes
context.

A slot set to automatic (``auto_tune``) has no fixed context. Each request says how much
it needs; the model is loaded with that plus headroom, at least ``AUTO_BASE_CONTEXT``,
never more than the model supports and never more than step 4 allows. It is reloaded
larger only when a later request needs more than it has, and it never shrinks.

Callers hold the run's LLM lock while they acquire and call, so one request is served
at a time and no other agent can swap the model out from under it.

Without a VRAM probe (no NVIDIA GPU) or without the runtime's memory estimate, the
gateway falls back to the sizes the team builder planned.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agentfit.cloud.providers import is_cloud, label_of
from agentfit.lmstudio.models import LoadedInstance
from agentfit.runtime import ModelRuntime
from agentfit.workflows.dsl import LoadingPolicy, ModelSlot

EmitFn = Callable[[str, dict[str, Any]], None]

# Context is chosen in steps of this many tokens.
CONTEXT_STEP = 1024
# How far apart the two estimates that define a memory profile have to be.
_PROFILE_SPAN = 8192
# Real loads kept per model, and how far apart two must be to trust the slope between them.
MAX_POINTS = 8
MIN_SPAN = 2048
# A first measurement is worth a second load only if it allows this much more context.
RETUNE_GAIN = 1.5
# Automatic slots: the context a model gets for a small request, the most it is given
# when the runtime does not say what the model supports, and the room left above a request
# so the next, slightly larger one does not force a reload.
AUTO_BASE_CONTEXT = 16384
AUTO_MAX_CONTEXT = 131072
AUTO_HEADROOM = 1.25
# What an agent that calls tools asks for when its prompt alone needs less: the base
# context. It matters where the plan gives less than that, because a need above the plan
# is what makes the gateway try a larger load.
AUTO_WORKING_NEED = int(AUTO_BASE_CONTEXT / AUTO_HEADROOM)
# When a model has to be loaded again because the conversation outgrew its context, it gets at
# least this much more than it had. Seen on a 27B reading a capture 12,000 characters at a time:
# 16k, 23k, 38k, 46k, 57k, five reloads of 10-13 s each, one per read.
AUTO_GROWTH = 1.5


class ModelLoadError(Exception):
    pass


@dataclass(frozen=True)
class VramReading:
    total_mib: float
    used_mib: float

    @property
    def free_mib(self) -> float:
        return max(0.0, self.total_mib - self.used_mib)


VramProbe = Callable[[], VramReading | None]
# MiB of GPU memory the model's process holds in system RAM. None when it cannot be read.
SpillProbe = Callable[[], float | None]


def nvidia_vram_probe() -> VramReading | None:
    """Free VRAM of the largest NVIDIA GPU, straight from nvidia-smi. None when unavailable."""
    from agentfit.hardware import nvidia

    try:
        rows = [r for r in nvidia.live_usage() if r.get("vram_total_mb") and r.get("vram_used_mb") is not None]
    except Exception:
        return None
    if not rows:
        return None
    gpu = max(rows, key=lambda r: float(r["vram_total_mb"] or 0))
    return VramReading(total_mib=float(gpu["vram_total_mb"] or 0), used_mib=float(gpu["vram_used_mb"] or 0))


class GatewayOptions(BaseModel):
    # VRAM that must remain free on paper when a context is chosen. It has to be larger
    # than ``saturated_mib``, or a load that goes exactly as planned would look saturated.
    margin_mib: int = 600
    # Less than this free after a load means the model is spilling into system RAM.
    saturated_mib: int = 480
    # Idle time after which the runtime unloads a model by itself. 0 turns it off.
    idle_ttl_seconds: int = 600
    min_context: int = 4096
    max_attempts: int = 3
    # How far past the plan a load may be tried, in MiB the plan says are not there.
    stretch_mib: int = 1024
    # A model answering slower than this share of its usual speed is spilling into system RAM.
    slow_ratio: float = 0.5
    # Length of the generation that measures speed after a load.
    probe_tokens: int = 48
    # More than this much of the model in system RAM means it does not fit: it answers several times slower.
    spill_mib: int = 300
    # While a model works, how often its overflow is looked at.
    spill_check_seconds: float = 45.0


SPEED_PROMPT = "Count from 1 to 60, one number per line."


class MemoryProfile(BaseModel):
    """What a model costs in VRAM: a fixed part plus a part that grows with the context."""

    fixed_mib: float
    per_1k_mib: float
    # "estimate": from the runtime's own estimator. "measured": corrected by real loads.
    source: str = "estimate"
    # Set for one load, never stored: the model is larger than the whole card even at the
    # smallest context, so the runtime keeps part of it in system RAM by design.
    partial_offload: bool = False
    # Real loads, as [context, MiB the load took]. Two at different contexts give the true line.
    points: list[tuple[int, float]] = Field(default_factory=list)
    # The estimator's own cost per 1000 tokens, kept to judge whether a measured one is believable.
    estimate_per_1k_mib: float | None = None
    # Generation speed measured right after a load the plan vouched for, in tokens per second:
    # what "its usual speed" means when a larger load has to be judged.
    tokens_per_second: float | None = None
    # Largest context that was loaded past the plan and still answered at that speed.
    verified_context: int | None = None

    def need_mib(self, context: int) -> float:
        return self.fixed_mib + self.per_1k_mib * context / 1000

    def with_load(self, context: int, used_mib: float) -> MemoryProfile:
        """This profile after a real load at ``context`` took ``used_mib``."""
        points = [p for p in self.points if p[0] != context] + [(context, round(used_mib, 1))]
        points = sorted(points[-MAX_POINTS:])
        fixed, slope = used_mib - self.per_1k_mib * context / 1000, self.per_1k_mib
        span = points[-1][0] - points[0][0]
        if span >= MIN_SPAN:
            # Least squares through every real load.
            n = len(points)
            mean_c = sum(c for c, _ in points) / n
            mean_u = sum(u for _, u in points) / n
            var = sum((c - mean_c) ** 2 for c, _ in points)
            fitted = sum((c - mean_c) * (u - mean_u) for c, u in points) / var * 1000
            ceiling = 2 * (self.estimate_per_1k_mib or self.per_1k_mib or fitted)
            if 0 < fitted <= ceiling:
                slope = fitted
                fixed = mean_u - slope * mean_c / 1000
        return self.model_copy(
            update={"fixed_mib": round(fixed, 1), "per_1k_mib": round(slope, 2), "source": "measured", "points": points}
        )


class ProfileStore(Protocol):
    def get(self, model_key: str) -> MemoryProfile | None: ...

    def put(self, model_key: str, profile: MemoryProfile) -> None: ...


class MemoryProfileStore:
    """Profiles kept for the life of the process, and in the database when one is given."""

    NAMESPACE = "model_memory"

    def __init__(self, db: Any = None) -> None:
        self._db = db
        self._cache: dict[str, MemoryProfile] = {}

    def get(self, model_key: str) -> MemoryProfile | None:
        if model_key in self._cache:
            return self._cache[model_key]
        if self._db is None:
            return None
        try:
            raw = self._db.kv_get(self.NAMESPACE, model_key)
            profile = MemoryProfile.model_validate(raw) if raw else None
        except Exception:
            return None
        if profile:
            self._cache[model_key] = profile
        return profile

    def put(self, model_key: str, profile: MemoryProfile) -> None:
        self._cache[model_key] = profile
        if self._db is not None:
            # A profile that cannot be saved is simply estimated again next time.
            with contextlib.suppress(Exception):
                self._db.kv_set(self.NAMESPACE, model_key, profile.model_dump())


@dataclass(frozen=True)
class Lease:
    """A model that is in memory and ready for one request."""

    instance_id: str
    model_key: str
    context_length: int
    slot: str
    # True when the slot's wide-context variant is serving instead of its main model.
    wide: bool = False


def _floor_step(tokens: float) -> int:
    return int(tokens) // CONTEXT_STEP * CONTEXT_STEP


def _ceil_step(tokens: float) -> int:
    return -(-int(tokens) // CONTEXT_STEP) * CONTEXT_STEP


def estimate_profile(runtime: Any, model_key: str, low: int, high: int) -> MemoryProfile | None:
    """A memory profile from the runtime's own estimator, asked at two contexts. None when it has none."""
    estimate = getattr(runtime, "estimate", None)
    if estimate is None:
        return None
    try:
        first, second = estimate(model_key, low), estimate(model_key, high)
    except Exception:
        return None
    if not first or not second or first.gpu_memory_gb is None or second.gpu_memory_gb is None:
        return None
    per_1k = max(0.0, (second.gpu_memory_gb - first.gpu_memory_gb) * 1024 / ((high - low) / 1000))
    return MemoryProfile(
        fixed_mib=round(first.gpu_memory_gb * 1024 - per_1k * low / 1000, 1),
        per_1k_mib=round(per_1k, 2),
        estimate_per_1k_mib=round(per_1k, 2),
    )


def safe_context(profile: MemoryProfile, room_mib: float, cap: int) -> int:
    """Largest context, in steps, whose memory fits ``room_mib``. 0 when not even the model itself fits."""
    left = room_mib - profile.fixed_mib
    if left < 0:
        return 0
    if profile.per_1k_mib <= 0:
        return cap
    return max(0, min(cap, _floor_step(left / profile.per_1k_mib * 1000)))


class ModelManager:
    def __init__(
        self,
        runtime: ModelRuntime,
        slots: dict[str, ModelSlot],
        emit: EmitFn,
        *,
        vram_budget_gb: float | None = None,
        vram_probe: VramProbe | None = None,
        profiles: ProfileStore | None = None,
        options: GatewayOptions | None = None,
        sleep: Callable[[float], None] = time.sleep,
        spill_probe: SpillProbe | None = None,
        keep_after_run: bool = False,
    ) -> None:
        self.runtime = runtime
        self.slots = slots
        self.emit = emit
        self.vram_budget_gb = vram_budget_gb
        self.options = options or GatewayOptions()
        self._probe = vram_probe
        self._profiles: ProfileStore = profiles or MemoryProfileStore()
        self._sleep = sleep
        self._lock = threading.RLock()
        # model_key -> instance id, as last seen in the runtime.
        self._instances: dict[str, str] = {}
        self._last_used: dict[str, float] = {}
        self._loaded_by_run: set[str] = set()
        # model_key -> context this run loaded it with.
        self._granted: dict[str, int] = {}
        # model_key -> largest context that fit at the last swap, when it was less than asked.
        # It stops a request that wants more from reloading a model that cannot get more, and
        # it is thrown away at every swap, because that is when VRAM is measured again.
        self._ceiling: dict[str, int] = {}
        # model_key -> smallest context that was loaded past the plan and measured too slow.
        # Kept for the whole run, so the same overflow is not tried again at every swap.
        self._too_much: dict[str, int] = {}
        self._spill_probe = spill_probe
        # A conversation: the model stays for the next message. The idle timeout, or another run
        # that needs the memory, takes it out.
        self._keep_after_run = keep_after_run
        self._spill_checked = 0.0
        # model_key -> overflow seen once while it worked, waiting to be seen again before acting on it.
        self._spill_seen: dict[str, float] = {}
        # model_key -> what to reload it with after an overflow: the context, and what was measured.
        self._shrink: dict[str, dict[str, Any]] = {}
        # model_key -> the context an overflow sent it back to: the most to try again in this run.
        self._retry_at: dict[str, int] = {}
        # Models larger than the card, which overflow by design, and those already reported at their smallest context.
        self._partial: set[str] = set()
        self._spill_reported: set[str] = set()
        # model_key -> the most context the model supports, as the runtime reports it.
        self._model_max: dict[str, int | None] | None = None
        self.switch_count = 0

    # -- slot helpers -------------------------------------------------------
    def _slot(self, slot_id: str) -> ModelSlot:
        slot = self.slots.get(slot_id)
        if slot is None:
            raise ModelLoadError(f"Unknown model slot '{slot_id}'")
        if not slot.model_key:
            raise ModelLoadError(f"Model slot '{slot_id}' has no model assigned")
        return slot

    def _supported_context(self, model_key: str) -> int:
        """The most context the model itself supports; a generous ceiling when the runtime does not say."""
        if self._model_max is None:
            try:
                self._model_max = {m.model_key: m.max_context_length for m in self.runtime.list_installed()}
            except Exception:
                self._model_max = {}
        return int(self._model_max.get(model_key) or AUTO_MAX_CONTEXT)

    def _cap(self, slot: ModelSlot) -> int:
        """The most context this slot may ever ask for."""
        return self._supported_context(slot.model_key) if slot.auto_tune else slot.context_length

    def _target(self, slot: ModelSlot, need: int) -> int:
        """Context to load the slot's model with for a request that needs ``need`` tokens."""
        if not slot.auto_tune:
            return slot.context_length
        wanted = max(AUTO_BASE_CONTEXT, _ceil_step(need * AUTO_HEADROOM))
        return max(self.options.min_context, min(self._cap(slot), wanted))

    def _policy_for(self, model_key: str) -> LoadingPolicy:
        # An automatic slot leaves the decision to the gateway: stay while there is room, go when there is not.
        policies = [
            LoadingPolicy.LOAD_ON_DEMAND if s.auto_tune else s.loading_policy
            for s in self.slots.values() if s.model_key == model_key
        ]
        if LoadingPolicy.KEEP_LOADED in policies:
            return LoadingPolicy.KEEP_LOADED
        return policies[0] if policies else LoadingPolicy.LOAD_ON_DEMAND

    def _vram_of(self, model_key: str) -> float | None:
        sizes = [s.vram_gb for s in self.slots.values() if s.model_key == model_key and s.vram_gb]
        return max(sizes) if sizes else None

    def effective_context(self, slot_id: str) -> int:
        """Context the slot's model runs with right now, or its planned context."""
        slot = self.slots.get(slot_id)
        if slot is None:
            return 8192
        return self._granted.get(slot.model_key, slot.context_length)

    # -- measuring ----------------------------------------------------------
    def _measure(self, *, settle: bool = False) -> VramReading | None:
        if self._probe is None:
            return None
        if settle:
            self._sleep(0.4)  # the driver reports freed or newly used memory a moment late
        try:
            return self._probe()
        except Exception:
            return None

    def _loaded_now(self) -> dict[str, LoadedInstance]:
        """What the runtime has in memory. It is the truth: idle timeouts and the user can unload behind our back."""
        try:
            # The embedding model of the project memory is listed too. It holds about 450 MiB of VRAM even
            # with its weights in RAM, so it counts, and as no slot uses it, it is the first to be unloaded
            # when a model needs the room. The memory loads it again when the card has room to spare.
            loaded = {inst.model_key: inst for inst in self.runtime.list_loaded()}
        except Exception:
            return {k: LoadedInstance(instance_id=v, model_key=k, context_length=self._granted.get(k)) for k, v in self._instances.items()}
        self._instances = {k: inst.instance_id for k, inst in loaded.items()}
        return loaded

    def _profile(self, model_key: str, target_context: int) -> MemoryProfile | None:
        known = self._profiles.get(model_key)
        if known is not None:
            return known
        low = self.options.min_context
        profile = estimate_profile(self.runtime, model_key, low, max(target_context, low + _PROFILE_SPAN))
        if profile is not None:
            self._profiles.put(model_key, profile)
        return profile

    def _sized(self, profile: MemoryProfile | None, reading: VramReading | None) -> MemoryProfile | None:
        """Mark a model that is larger than the whole card: there is no context that makes it fit."""
        if profile is None or reading is None:
            return profile
        too_big = profile.need_mib(self.options.min_context) > reading.total_mib
        return profile.model_copy(update={"partial_offload": too_big})

    @property
    def _margin_mib(self) -> float:
        """The planning margin, never so small that a correct plan would trip the saturation check."""
        return max(self.options.margin_mib, self.options.saturated_mib + 100)

    def _fit_context(self, profile: MemoryProfile | None, target: int, reading: VramReading | None) -> int:
        """Largest context up to ``target`` that fits the free VRAM with the margin."""
        if profile is None or reading is None or profile.partial_offload:
            return target
        room = reading.free_mib - self._margin_mib - profile.fixed_mib
        if profile.per_1k_mib <= 0:
            return target if room >= 0 else min(target, self.options.min_context)
        fits = _floor_step(room / profile.per_1k_mib * 1000)
        return max(min(target, self.options.min_context), min(target, fits))

    def _reach(self, key: str, profile: MemoryProfile | None, target: int, reading: VramReading | None, plan: int) -> int:
        """The most context worth trying past the plan: within ``stretch_mib`` of fitting on paper."""
        if profile is None or reading is None or profile.partial_offload or plan >= target:
            return plan
        room = reading.free_mib + self.options.stretch_mib - profile.fixed_mib
        if profile.per_1k_mib <= 0:
            reach = target if room >= 0 else plan
        else:
            reach = min(target, _floor_step(room / profile.per_1k_mib * 1000))
        refused = self._too_much.get(key)
        if refused:
            # Something was too much in this run: go no further than what has really loaded well.
            known = [c for c in (profile.verified_context, self._retry_at.get(key)) if c and c < refused]
            reach = min(reach, max(known, default=plan))
        return max(plan, reach)

    def _speed(self, instance_id: str) -> float | None:
        """Tokens per second of a short generation, from the runtime's own statistics. None when it has none."""
        try:
            result = self.runtime.measure(instance_id, SPEED_PROMPT, max_output_tokens=self.options.probe_tokens)
            speed = result.stats.tokens_per_second
        except Exception:
            return None
        return float(speed) if speed and speed > 0 else None

    def _spill(self) -> float | None:
        if self._spill_probe is None:
            return None
        try:
            return self._spill_probe()
        except Exception:
            return None

    def _watch_spill(self, slot: ModelSlot, loaded: dict[str, LoadedInstance]) -> None:
        """While a model works, notice that it has started to overflow and arrange a smaller reload."""
        opts = self.options
        key = next((k for k in (slot.model_key, slot.wide_model_key or "") if k in loaded and k in self._granted), "")
        if self._spill_probe is None or not key or key in self._partial or key in self._shrink:
            return
        now = time.monotonic()
        if now - self._spill_checked < opts.spill_check_seconds:
            return
        self._spill_checked = now
        spilled = self._spill()
        if spilled is None or spilled <= opts.spill_mib:
            self._spill_seen.pop(key, None)
            return
        if key not in self._spill_seen:
            # Seen once. Another program may be taking VRAM for a moment: it has to be there at the next look too.
            self._spill_seen[key] = spilled
            return
        self._spill_seen.pop(key, None)
        granted = self._granted[key]
        smaller = max(opts.min_context, _floor_step(granted / 2))
        if smaller >= granted:
            if key not in self._spill_reported:
                self._spill_reported.add(key)
                self.emit("model.switch", {"phase": "spilling", "to": key, "spilled_mib": round(spilled), "context_length": granted, "next_context": None})
            return
        self._too_much[key] = min(granted, self._too_much.get(key, granted))
        self._retry_at[key] = smaller
        self._shrink[key] = {"context": smaller, "from": granted, "spilled_mib": round(spilled)}
        profile = self._profiles.get(key)
        if profile is not None and profile.verified_context and granted <= profile.verified_context:
            # It held its speed when it was loaded, and does not hold the card now.
            self._profiles.put(key, profile.model_copy(update={"verified_context": None}))
        self.emit("model.switch", {"phase": "spilling", "to": key, "spilled_mib": round(spilled), "context_length": granted, "next_context": smaller})

    # -- reuse --------------------------------------------------------------
    def _reuse(self, slot: ModelSlot, slot_id: str, loaded: dict[str, LoadedInstance], need: int) -> Lease | None:
        def have(key: str, planned: int) -> int:
            return loaded[key].context_length or self._granted.get(key) or planned

        def lease(key: str, planned: int, wide: bool) -> Lease:
            self._last_used[key] = time.monotonic()
            return Lease(loaded[key].instance_id, key, have(key, planned), slot_id, wide)

        if slot.model_key in self._shrink or (slot.wide_model_key or "") in self._shrink:
            return None  # it overflows: reload it smaller instead of using it as it is
        cap = self._cap(slot)
        main_cap = min(self._ceiling.get(slot.model_key, cap), cap)
        wide_key = slot.wide_model_key or ""
        wants_wide = bool(wide_key) and need > main_cap
        # Enough for this request, or already as much as this model can get on this PC.
        if slot.model_key in loaded and not wants_wide and have(slot.model_key, cap) >= min(need, main_cap):
            return lease(slot.model_key, cap, False)
        if wants_wide and wide_key in loaded:
            wide_target = slot.wide_context_length or cap
            wide_cap = min(self._ceiling.get(wide_key, wide_target), wide_target)
            if have(wide_key, wide_target) >= min(need, wide_cap):
                return lease(wide_key, wide_target, True)
        return None

    # -- freeing memory -----------------------------------------------------
    def _unload_key(self, model_key: str, instance_id: str, incoming: str) -> bool:
        try:
            self.runtime.unload(instance_id)
        except Exception as exc:
            self.emit("model.switch", {"phase": "warning", "to": incoming, "message": f"Could not unload {model_key}: {exc}"})
            return False
        self._instances.pop(model_key, None)
        self._granted.pop(model_key, None)
        return True

    def _planned_evictions(self, incoming: str, others: list[str]) -> list[str]:
        """Without measurements: who has to go, by the sizes the team builder planned."""
        if not others:
            return []
        need = self._vram_of(incoming)
        if self.vram_budget_gb is None or need is None:
            # Without sizes, be conservative: only models marked keep-loaded stay.
            return [k for k in others if self._policy_for(k) != LoadingPolicy.KEEP_LOADED]
        used = sum(self._vram_of(k) or 0.0 for k in others)
        free = self.vram_budget_gb - used
        if need <= free:
            return []
        evict: list[str] = []
        for key in self._eviction_order(others):
            if need <= free:
                break
            evict.append(key)
            free += self._vram_of(key) or 0.0
        return evict

    def _eviction_order(self, keys: list[str]) -> list[str]:
        """On-demand models first, least recently used first; keep-loaded ones last."""
        return sorted(keys, key=lambda k: (self._policy_for(k) == LoadingPolicy.KEEP_LOADED, self._last_used.get(k, 0.0)))

    def _make_room(
        self, incoming: str, profile: MemoryProfile | None, target: int, others: list[str], loaded: dict[str, LoadedInstance]
    ) -> list[str]:
        """Unload other models until the incoming one fits at its full context."""
        if not others:
            return []
        reading = self._measure()
        profile = self._sized(profile, reading)
        if reading is None or profile is None:
            planned = self._planned_evictions(incoming, others)
            return [k for k in planned if self._unload_key(k, loaded[k].instance_id, incoming)]
        need = float("inf") if profile.partial_offload else profile.need_mib(target) + self._margin_mib
        evicted: list[str] = []
        for key in self._eviction_order(others):
            if reading is not None and reading.free_mib >= need:
                break
            if self._unload_key(key, loaded[key].instance_id, incoming):
                evicted.append(key)
                reading = self._measure(settle=True)
        return evicted

    # -- loading ------------------------------------------------------------
    def _load(self, model_key: str, context: int) -> tuple[str, int, float | None]:
        """Load and return (instance id, context actually applied, load seconds)."""
        ttl = self.options.idle_ttl_seconds or None
        try:
            result = self.runtime.load(model_key, context_length=context, ttl_seconds=ttl)
        except TypeError:
            # A runtime without idle timeouts.
            result = self.runtime.load(model_key, context_length=context)
        applied = (result.load_config or {}).get("context_length") if result.load_config else None
        return result.instance_id, int(applied or context), result.load_time_seconds

    def _calibrate(
        self, model_key: str, profile: MemoryProfile | None, context: int, before: VramReading | None, after: VramReading | None
    ) -> MemoryProfile | None:
        """Fold what this load really took into the profile, when the reading is believable."""
        if profile is None or before is None or after is None:
            return profile
        # A model believed larger than the card is only a measurement if it turned out to fit.
        if profile.partial_offload and after.free_mib < self.options.saturated_mib:
            return profile
        used = after.used_mib - before.used_mib
        expected = profile.need_mib(context)
        # Something else took or released VRAM while the model was loading: not a measurement.
        if used <= 0 or not (0.5 * expected <= used <= 1.3 * expected):
            return profile
        learned = profile.with_load(context, used).model_copy(update={"partial_offload": False})
        self._profiles.put(model_key, learned)
        return learned

    def _swap(self, slot: ModelSlot, slot_id: str, loaded: dict[str, LoadedInstance], need: int) -> Lease:
        opts = self.options
        started = time.perf_counter()
        # Limits found by measuring in this run stay; the ones a plan set are measured again now.
        self._ceiling = {k: v for k, v in self._ceiling.items() if k in self._too_much}
        own = {slot.model_key, slot.wide_model_key or slot.model_key}
        stale = [k for k in loaded if k in own]  # this slot's own model, loaded with too little context
        others = [k for k in loaded if k not in own]
        self.emit("model.switch", {"phase": "start", "to": slot.model_key, "slot": slot_id, "unloading": stale + others})

        wanted = self._target(slot, need)
        if slot.auto_tune and stale:
            had = max(loaded[k].context_length or self._granted.get(k) or 0 for k in stale)
            if had and need > had:
                wanted = max(wanted, min(self._cap(slot), _ceil_step(had * AUTO_GROWTH)))
        shrink = next((self._shrink.pop(k) for k in list(own) if k in self._shrink), None)
        if shrink is not None:
            wanted = min(wanted, shrink["context"])
        profile = self._profile(slot.model_key, wanted)
        unloaded = [k for k in stale if self._unload_key(k, loaded[k].instance_id, slot.model_key)]
        if unloaded:
            self._measure(settle=True)
        unloaded += self._make_room(slot.model_key, profile, wanted, others, loaded)
        remaining = [k for k in others if k not in unloaded]
        unload_seconds = time.perf_counter() - started

        # Choose the model (main or wide variant) and the context, from what is free now.
        reading = self._measure()
        profile = self._sized(profile, reading)
        key, target, wide = slot.model_key, wanted, False
        context = self._fit_context(profile, target, reading)
        if reading is not None and context < target:
            # Remembered even if the wide variant ends up serving: it is why the main model is not used.
            self._ceiling[slot.model_key] = context
        if slot.wide_model_key and need > context:
            wide_target = slot.wide_context_length or max(target, _floor_step(need + CONTEXT_STEP))
            wide_profile = self._sized(self._profile(slot.wide_model_key, wide_target), reading)
            wide_context = self._fit_context(wide_profile, wide_target, reading)
            if wide_context > context:
                key, target, wide, profile, context = slot.wide_model_key, wide_target, True, wide_profile, wide_context

        # Past the plan: what this request needs, or what an earlier load of this model proved.
        plan = context
        reach = self._reach(key, profile, target, reading, plan)
        proven = max(plan, min(profile.verified_context or plan, reach)) if profile else plan
        goal = reach if need > plan else proven
        usual = profile.tokens_per_second if profile else None
        if goal > plan and usual:
            context = goal  # its usual speed is known, so the load can be judged straight away

        attempts = 0
        saturated = False
        retuned = False
        refused = False
        # The largest context the plan vouches for. A load above it has to prove itself by its speed.
        paper = plan
        speed: float | None = None
        while True:
            attempts += 1
            before = self._measure()
            self.emit(
                "model.switch",
                {"phase": "loading", "to": key, "slot": slot_id, "context_length": context,
                 "vram_free_mib": round(before.free_mib) if before else None},
            )
            try:
                instance, applied, load_seconds = self._load(key, context)
            except Exception as exc:
                self.emit("model.switch", {"phase": "failed", "to": key, "message": str(exc)})
                raise ModelLoadError(f"Could not load model '{key}': {exc}") from exc
            after = self._measure(settle=True)
            self._instances[key] = instance
            partial = bool(profile and profile.partial_offload)
            beyond = applied > paper
            # Speed is measured only where it decides something: past the plan, or where a later request may go past it.
            speed = self._speed(instance) if after is not None and not partial and (beyond or reach > applied) else None
            # Past the plan, the load stays only if it is seen to answer at the model's usual speed.
            # There the speed is the whole verdict: the free figure was seen at 457 MiB on a load
            # that answered at full speed and at 560 MiB on one that answered five times slower.
            too_slow = beyond and (speed is None or usual is None or speed < usual * opts.slow_ratio)
            # What the model holds in system RAM is the overflow itself. Where Windows reports it, it decides;
            # where it does not, the free figure has to do for a load the plan vouched for.
            spilled = self._spill() if not partial else None
            overflow = spilled is not None and spilled > opts.spill_mib
            low = overflow if spilled is not None else (after is not None and after.free_mib < opts.saturated_mib)
            saturated = not partial and ((too_slow or overflow) if beyond else low)
            if not saturated:
                if speed is not None and profile is not None and (not beyond or usual is None or speed > usual):
                    # A load the plan vouches for sets the usual speed; one past it can only raise it.
                    usual = speed
                    profile = profile.model_copy(update={"tokens_per_second": round(speed, 1)})
                    self._profiles.put(key, profile)
                better = applied
                if beyond:
                    # The card was full when this loaded, so the reading says nothing about what the
                    # model costs. What is learned is that this context works.
                    if profile is not None and applied > (profile.verified_context or 0):
                        profile = profile.model_copy(update={"verified_context": applied})
                        self._profiles.put(key, profile)
                else:
                    first_real_load = profile is not None and not profile.points
                    profile = self._calibrate(key, profile, applied, before, after)
                    if first_real_load and applied < target:
                        better = self._fit_context(profile, target, before)
                        paper = max(paper, better)
                worth_it = better >= applied * RETUNE_GAIN
                if goal > max(applied, better) and usual is not None and not refused:
                    # Loaded at the plan first, to learn the model's usual speed. Now the larger load can be judged.
                    better, worth_it = goal, True
                if worth_it and not retuned and attempts < opts.max_attempts:
                    # The estimate was pessimistic: the measurement allows much more context. One
                    # more load now, and the two real loads give the true cost for every later run.
                    retuned = True
                    self.emit(
                        "model.switch",
                        {"phase": "retuning", "to": key, "slot": slot_id, "from_context": applied, "context_length": better},
                    )
                    self._unload_key(key, instance, key)
                    self._measure(settle=True)
                    context = better
                    continue
                break
            self.emit(
                "model.switch",
                {"phase": "saturated", "to": key, "slot": slot_id, "context_length": applied,
                 "vram_free_mib": round(after.free_mib) if after else None,
                 "tokens_per_second": round(speed, 1) if speed else None,
                 "usual_tokens_per_second": round(usual, 1) if usual else None,
                 "spilled_mib": round(spilled) if spilled is not None else None},
            )
            refused = True  # after a load that was too much, nothing larger is tried in this swap
            if beyond:
                self._too_much[key] = min(applied, self._too_much.get(key, applied))
                if profile is not None and profile.verified_context and applied <= profile.verified_context:
                    # It worked before and does not now: something else holds more of the card today.
                    profile = profile.model_copy(update={"verified_context": None})
                    self._profiles.put(key, profile)
            if attempts >= opts.max_attempts or (not remaining and applied <= opts.min_context):
                break  # nothing left to try: run saturated and say so
            self._unload_key(key, instance, key)
            if remaining:
                # First give the model the whole card, then think about a smaller context.
                unloaded += [k for k in remaining if self._unload_key(k, loaded[k].instance_id, key)]
                remaining = []
            elif beyond:
                # Past the plan and too much. Next a context that has loaded well before, or halfway
                # back when none has; the plan itself when no load can be spared after this one.
                known = (profile.verified_context if profile else None) or paper
                step = known if paper < known < applied else _floor_step((applied + paper) / 2)
                context = step if step > paper and attempts + 1 < opts.max_attempts else paper
            else:
                context = max(opts.min_context, _floor_step(applied * 0.75))
            self._measure(settle=True)

        total = time.perf_counter() - started
        self._granted[key] = applied
        if self._probe is not None:
            # A larger context that was possible and not needed stays open for a request that needs it,
            # unless it was tried and refused or there is no speed to judge it by.
            limit = applied if saturated or refused or usual is None or key in self._too_much else max(applied, reach)
            if limit < target or saturated or key in self._too_much:
                self._ceiling[key] = limit
            else:
                self._ceiling.pop(key, None)
        if partial:
            self._partial.add(key)
        self._last_used[key] = time.monotonic()
        self._loaded_by_run.add(key)
        self.switch_count += 1
        self.emit(
            "model.switch",
            {
                "phase": "ready",
                "to": key,
                "slot": slot_id,
                "unloaded": unloaded,
                "unload_seconds": round(unload_seconds, 2),
                "load_seconds": round(load_seconds or max(0.0, total - unload_seconds), 2),
                "total_seconds": round(total, 2),
                "already_loaded": False,
                "context_length": applied,
                "requested_context": target,
                # What the request itself needed. A context below ``requested_context`` and above this is enough.
                "needed_context": need,
                # What the free VRAM allowed on paper. Less than ``context_length`` when the load
                # went past it and its speed showed that it fits.
                "planned_context": paper,
                "tokens_per_second": round(speed, 1) if speed else None,
                # Set when the model was reloaded because it had overflowed into system RAM while working.
                "shrunk_from": shrink["from"] if shrink else None,
                "spilled_mib": shrink["spilled_mib"] if shrink else None,
                # True when the context was chosen for this request instead of read from the workflow.
                "auto": slot.auto_tune,
                "wide": wide,
                "attempts": attempts,
                "retuned": retuned,
                "saturated": saturated,
                "partial_offload": bool(profile and profile.partial_offload),
                "vram_free_mib": round(after.free_mib) if after else None,
                "vram_total_mib": round(after.total_mib) if after else None,
            },
        )
        return Lease(instance, key, applied, slot_id, wide)

    def _cloud_lease(self, slot: ModelSlot, slot_id: str) -> Lease:
        """A cloud model: nothing to load and no VRAM to find, only the size of its window."""
        key = slot.model_key
        cloud = getattr(self.runtime, "cloud", None)
        if cloud is None:
            raise ModelLoadError(f"{label_of(key)} is a cloud model, and cloud models are not available here")
        try:
            context = cloud.context_length(key)
        except Exception as exc:
            raise ModelLoadError(f"{label_of(key)}: {exc}") from exc
        with self._lock:
            first = key not in self._granted
            self._granted[key] = context
        if first:
            self.emit("model.switch", {"phase": "ready", "to": key, "slot": slot_id, "unloaded": [], "unload_seconds": 0,
                                       "load_seconds": 0, "total_seconds": 0, "already_loaded": False,
                                       "context_length": context, "cloud": True})
        return Lease(key, key, context, slot_id)

    # -- public API ---------------------------------------------------------
    def acquire(self, slot_id: str, need_context: int | None = None) -> Lease:
        """Put the slot's model in memory and return what to call.

        ``need_context`` is the context this request needs. It decides whether a model
        that is already loaded with a small context is enough, and whether the slot's
        wide-context variant should serve instead.
        """
        slot = self._slot(slot_id)
        need = max(0, need_context or 0)
        if is_cloud(slot.model_key):
            return self._cloud_lease(slot, slot_id)
        with self._lock:
            loaded = self._loaded_now()
            self._watch_spill(slot, loaded)
            lease = self._reuse(slot, slot_id, loaded, need)
            if lease is not None:
                return lease
            return self._swap(slot, slot_id, loaded, need)

    def release(self, slot_id: str) -> None:
        """Called when an agent finishes; honours ``unload_after_use``."""
        slot = self.slots.get(slot_id)
        if slot is None or not slot.model_key:
            return
        if slot.loading_policy == LoadingPolicy.UNLOAD_AFTER_USE and not slot.auto_tune:
            for key in {slot.model_key, slot.wide_model_key or slot.model_key}:
                self._unload(key)

    def _unload(self, model_key: str) -> None:
        with self._lock:
            instance = self._instances.pop(model_key, None)
            self._granted.pop(model_key, None)
            if instance is None:
                return
            try:
                self.runtime.unload(instance)
                self.emit("model.switch", {"phase": "unloaded", "model": model_key})
            except Exception as exc:
                self.emit("model.switch", {"phase": "warning", "message": f"Could not unload {model_key}: {exc}"})

    def close(self) -> None:
        """End of run: unload what this run loaded, except models marked keep-loaded."""
        if self._keep_after_run:
            return
        with self._lock:
            self._loaded_now()
            for key in list(self._loaded_by_run):
                if self._policy_for(key) != LoadingPolicy.KEEP_LOADED:
                    self._unload(key)

    def loaded(self) -> list[str]:
        with self._lock:
            return list(self._instances)
