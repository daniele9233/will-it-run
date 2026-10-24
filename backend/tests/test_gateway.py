"""The model gateway: several models in one workflow, swapped by measuring VRAM.

The GPU here is simulated: 16376 MiB, a desktop that holds some of it, and models whose
real cost is a fixed part plus a part per 1000 tokens of context. When more is asked
than the card has, the reading pegs just under the total, as it does on Windows when
the driver starts spilling into system RAM.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentfit.lmstudio.models import ResourceEstimate
from agentfit.workflows.compiler import build_messages, prompt_demand
from agentfit.workflows.dsl import LoadingPolicy, ModelSlot, load_yaml
from agentfit.workflows.model_manager import (
    GatewayOptions,
    MemoryProfile,
    MemoryProfileStore,
    ModelLoadError,
    ModelManager,
    VramReading,
)
from fakes import FakeRuntime

TOTAL = 16376
# model -> (fixed MiB, MiB per 1000 tokens of context): what a load really takes.
COSTS = {
    "qwen-27b": (12670, 38),
    "qwen-27b-wide": (10970, 38),
    "spark-4b": (4200, 55),
    "gemma-12b": (7000, 45),
    "giant-70b": (40000, 60),
    "qwen-real": (14258, 39.9),
    # A 14 GB file on the 16 GB card: it nearly fills it before any context is added.
    "qwen-q3": (14320, 24),
}
# What nvidia-smi keeps showing as free on Windows once the card is under pressure.
PEGGED_FREE = 560


class GpuRuntime(FakeRuntime):
    """FakeRuntime with a GPU behind it and LM Studio's ``--estimate-only``.

    ``squeeze`` turns on what Windows was measured to do: under pressure it takes up to
    that many MiB back from idle desktop programs, and the free figure stays at
    ``PEGGED_FREE`` whether the model still fits or already spills into system RAM. Only
    the speed tells the two apart.
    """

    def __init__(
        self,
        *,
        desktop: float = 1000,
        estimate_error: dict[str, float] | None = None,
        estimate_slope: dict[str, float] | None = None,
        squeeze: float | None = None,
        speed: float | None = 60.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.desktop = desktop
        # model -> MiB by which the estimator under-reports the fixed cost (negative: over-reports).
        self.estimate_error = estimate_error or {}
        # model -> factor by which the estimator overstates the cost per 1000 tokens.
        self.estimate_slope = estimate_slope or {}
        self.estimate_calls: list[tuple[str, int | None]] = []
        self.peak_models = 0
        self.squeeze = squeeze
        # Tokens per second of a model that fits. None: the runtime cannot measure speed.
        self.speed = speed
        self.measure_calls = 0

    def cost(self, key: str, context: int | None) -> float:
        fixed, per_1k = COSTS[key]
        return fixed + per_1k * (context or 4096) / 1000

    def _raw(self) -> float:
        return self.desktop + sum(self.cost(k, self.contexts.get(k)) for k in self.loaded)

    def spilling(self) -> bool:
        return self._raw() > TOTAL - (PEGGED_FREE - self.squeeze if self.squeeze is not None else 0)

    def spill(self) -> float:
        """MiB of the models that sit in system RAM: what Windows reports per process."""
        return max(0.0, self._raw() - (TOTAL - (PEGGED_FREE - self.squeeze if self.squeeze is not None else 0)))

    def probe(self) -> VramReading:
        raw = self._raw()
        if self.squeeze is not None:
            return VramReading(total_mib=TOTAL, used_mib=min(raw, TOTAL - PEGGED_FREE))
        return VramReading(total_mib=TOTAL, used_mib=min(raw, TOTAL - 60))

    def measure(self, model: str, prompt: str, *, max_output_tokens: int = 256):  # type: ignore[override]
        self.measure_calls += 1
        if self.speed is None:
            raise RuntimeError("no statistics")
        result = super().measure(model, prompt, max_output_tokens=max_output_tokens)
        # A model that spills into system RAM keeps answering, five times slower.
        result.stats.tokens_per_second = self.speed / 5 if self.spilling() else self.speed
        return result

    def estimate(self, model_key: str, context_length: int | None = None) -> ResourceEstimate:
        self.estimate_calls.append((model_key, context_length))
        fixed, per_1k = COSTS[model_key]
        context = context_length or 4096
        believed = fixed - self.estimate_error.get(model_key, 0) + per_1k * self.estimate_slope.get(model_key, 1.0) * context / 1000
        # Like LM Studio's estimator, it reports what the model wants even when that is more than the card has.
        return ResourceEstimate(gpu_memory_gb=believed / 1024, total_memory_gb=believed / 1024, context_length=context_length)

    def load(self, model_key: str, **kwargs: Any):  # type: ignore[override]
        result = super().load(model_key, **kwargs)
        self.peak_models = max(self.peak_models, len(self.loaded))
        return result


def slots(**overrides: Any) -> dict[str, ModelSlot]:
    base = {
        "reason": ModelSlot(model_key="qwen-27b", context_length=16384),
        "logs": ModelSlot(model_key="spark-4b", context_length=32768),
    }
    base.update(overrides)
    return base


def gateway(runtime: GpuRuntime, model_slots: dict[str, ModelSlot] | None = None, **kwargs: Any) -> tuple[ModelManager, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    manager = ModelManager(
        runtime, model_slots or slots(), lambda t, p: events.append(p), vram_probe=runtime.probe,
        sleep=lambda seconds: None, **kwargs,
    )
    return manager, events


def phases(events: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("phase") == phase]


# -- loading with the context that fits ---------------------------------------------


def test_loads_at_full_context_when_there_is_room_and_sets_an_idle_timeout():
    runtime = GpuRuntime(desktop=1000)
    manager, events = gateway(runtime)
    lease = manager.acquire("reason")
    # 12670 + 38 * 16.384 = 13293 MiB; 15376 free: fits with room to spare.
    assert (lease.model_key, lease.context_length, lease.wide) == ("qwen-27b", 16384, False)
    assert runtime.load_options == [{"model": "qwen-27b", "context_length": 16384, "ttl_seconds": 600}]
    ready = phases(events, "ready")[0]
    assert ready["context_length"] == ready["requested_context"] == 16384 and ready["saturated"] is False
    assert ready["vram_free_mib"] == round(TOTAL - 1000 - 13292.6) and ready["attempts"] == 1


def test_context_shrinks_by_itself_when_the_desktop_holds_more_vram():
    # A browser with a few tabs: 2300 MiB gone before any model is loaded.
    # Free 14076 - margin 600 - fixed 12670 = 806 MiB -> room for 21K tokens: the full 16384 still fits.
    runtime = GpuRuntime(desktop=2300)
    manager, events = gateway(runtime)
    assert manager.acquire("reason").context_length == 16384

    # A browser full of tabs.
    tight = GpuRuntime(desktop=2500)
    manager, events = gateway(tight)
    lease = manager.acquire("reason")
    # Free 13876 - 600 - 12670 = 606 MiB -> 15947 tokens -> 15360 after rounding down to a step.
    assert lease.context_length == 15360
    assert tight.load_options == [{"model": "qwen-27b", "context_length": 15360, "ttl_seconds": 600}]
    ready = phases(events, "ready")[0]
    assert ready["requested_context"] == 16384 and ready["context_length"] == 15360 and ready["attempts"] == 1
    assert ready["vram_free_mib"] >= 600  # the margin really is left free


def test_a_model_that_was_limited_by_vram_is_not_reloaded_on_every_call():
    # The plan allows 5120 tokens and the request needs 30000. The larger context is tried once,
    # overflows the card, and the model goes back to what the plan allowed.
    runtime = GpuRuntime(desktop=2900)
    manager, events = gateway(runtime, {"reason": ModelSlot(model_key="qwen-27b", context_length=32768)})
    first = manager.acquire("reason", need_context=30000)
    assert [o["context_length"] for o in runtime.load_options] == [5120, 32768, 5120]
    assert phases(events, "saturated")[0]["context_length"] == 32768
    again = manager.acquire("reason", need_context=30000)  # wants more than it got, but no more fits
    assert first.context_length == again.context_length == 5120
    assert len(runtime.load_calls) == 3  # nothing was loaded again for the second request


def test_a_load_past_the_plan_is_judged_by_its_speed_alone():
    """Seen on the development PC: 35840 tokens loaded past the plan left 457 MiB free, under the
    480 that count as saturated, while the model answered at 54 of its usual 67 tokens per second.
    """
    runtime = GpuRuntime(desktop=2900)
    manager, events = gateway(runtime)
    lease = manager.acquire("reason", need_context=16000)
    # 16384 tokens leave 183 MiB free and the model is as fast as before: it stays.
    assert [o["context_length"] for o in runtime.load_options] == [5120, 16384] and lease.context_length == 16384
    ready = phases(events, "ready")[0]
    assert ready["vram_free_mib"] < 480 and ready["saturated"] is False and not phases(events, "saturated")
    assert (ready["planned_context"], ready["needed_context"]) == (5120, 16000)


# -- past the plan, judged by speed ---------------------------------------------------
#
# The case that made a Developer fail on the development PC: a 14 GB model on the 16 GB
# card. On paper not even the smallest context leaves the margin free, so the plan is 4096
# tokens, too little to write a file. In fact the same model was measured at full speed
# with 16384 and with 49152, because Windows hands it what idle desktop programs hold.


def edge_store(*, per_1k: float = 100.0, speed: float | None = None, verified: int | None = None) -> MemoryProfileStore:
    """The profile that PC had stored: one real load of 14093 MiB at 4096 tokens, and the estimator's 100 MiB per 1000 more."""
    store = MemoryProfileStore()
    store.put(
        "qwen-q3",
        MemoryProfile(
            fixed_mib=round(14093 - per_1k * 4.096, 1), per_1k_mib=per_1k, estimate_per_1k_mib=100.0, source="measured",
            points=[(4096, 14093.0)], tokens_per_second=speed, verified_context=verified,
        ),
    )
    return store


def edge(store: MemoryProfileStore, **runtime_kwargs: Any) -> tuple[GpuRuntime, ModelManager, list[dict[str, Any]]]:
    runtime = GpuRuntime(desktop=1713, squeeze=1000, **runtime_kwargs)
    manager, events = gateway(runtime, {"work": ModelSlot(model_key="qwen-q3", auto_tune=True)}, profiles=store)
    return runtime, manager, events


def test_a_request_that_needs_more_than_the_plan_gets_it_when_the_speed_holds():
    store = edge_store()
    runtime, manager, events = edge(store)
    # A small request: the plan's 4096 tokens are enough. Its speed is noted, for later.
    assert manager.acquire("work", need_context=1200).context_length == 4096
    assert runtime.measure_calls == 1 and store.get("qwen-q3").tokens_per_second == 60
    # An agent with tools needs more. The larger context is loaded and kept, because the model still answers at 60.
    lease = manager.acquire("work", need_context=5200)
    assert lease.context_length == 16384
    assert [o["context_length"] for o in runtime.load_options] == [4096, 16384]
    ready = phases(events, "ready")[-1]
    assert (ready["planned_context"], ready["context_length"], ready["saturated"]) == (4096, 16384, False)
    assert ready["tokens_per_second"] == 60 and ready["attempts"] == 1
    assert not phases(events, "saturated")
    # It stays: the same request again loads nothing.
    assert manager.acquire("work", need_context=5200).context_length == 16384 and len(runtime.load_calls) == 2
    # A load made with the card full says nothing about what the model costs: the memory figures are as they were.
    profile = store.get("qwen-q3")
    assert profile.verified_context == 16384 and [c for c, _ in profile.points] == [4096] and profile.per_1k_mib == 100

    # The next run starts where this one arrived: one load, at the context that was proved.
    later_runtime, later, _ = edge(store)
    assert later.acquire("work", need_context=1200).context_length == 16384
    assert [o["context_length"] for o in later_runtime.load_options] == [16384]


def test_without_a_known_speed_the_plan_is_loaded_first_to_have_something_to_compare_with():
    runtime, manager, events = edge(edge_store())
    lease = manager.acquire("work", need_context=5200)
    assert lease.context_length == 16384
    assert [o["context_length"] for o in runtime.load_options] == [4096, 16384]
    assert phases(events, "retuning")[0]["context_length"] == 16384
    assert phases(events, "ready")[0]["attempts"] == 2


def test_a_context_that_overflows_is_refused_by_its_speed_when_the_vram_figure_says_nothing():
    store = edge_store(per_1k=24, speed=60, verified=16384)
    runtime, manager, events = edge(store)
    # 78000 tokens of need. As far past the plan as is allowed is 69632 tokens: (14663 free + 1024 - 13995) / 24 per 1000.
    # Loaded, the free figure stays at 560 MiB, as it does when the model fits. The speed drops from 60 to 12.
    lease = manager.acquire("work", need_context=78000)
    refused = phases(events, "saturated")[0]
    assert (refused["context_length"], refused["vram_free_mib"], refused["tokens_per_second"]) == (69632, PEGGED_FREE, 12)
    # Back to the largest context that has loaded well before, not all the way down to the plan.
    assert [o["context_length"] for o in runtime.load_options] == [69632, 16384] and lease.context_length == 16384
    assert phases(events, "ready")[0]["saturated"] is False and not runtime.spilling()
    # What was too much is not tried again in this run, by this request or after the model was out of memory.
    assert manager.acquire("work", need_context=78000).context_length == 16384 and len(runtime.load_calls) == 2
    manager._unload("qwen-q3")
    assert manager.acquire("work", need_context=78000).context_length == 16384
    assert [o["context_length"] for o in runtime.load_options][2:] == [16384]


def test_with_nothing_proved_a_refused_context_ends_at_the_plan():
    store = edge_store(per_1k=24, speed=60)
    runtime, manager, events = edge(store)
    lease = manager.acquire("work", need_context=78000)
    # 69632 and halfway back both spill; the third and last load is the one the plan vouches for.
    assert [o["context_length"] for o in runtime.load_options] == [69632, 36864, 4096]
    assert lease.context_length == 4096 and phases(events, "ready")[0]["saturated"] is False
    assert store.get("qwen-q3").verified_context is None


def test_a_context_that_worked_before_and_does_not_today_is_forgotten():
    store = edge_store(speed=60, verified=16384)
    runtime, manager, _ = edge(store)
    runtime.desktop = 2300  # a game, a second browser: 600 MiB more of the card are taken today
    lease = manager.acquire("work", need_context=1200)
    # With less free, "as far past the plan as is allowed" is 13312 tokens. It spills, and so does halfway back.
    assert [o["context_length"] for o in runtime.load_options] == [13312, 8192, 4096] and lease.context_length == 4096
    assert store.get("qwen-q3").verified_context is None


def test_a_runtime_that_cannot_measure_speed_stays_with_the_plan():
    runtime, manager, _ = edge(edge_store(), speed=None)
    assert manager.acquire("work", need_context=5200).context_length == 4096
    # No speed, no way to judge a larger load: it is not tried, and not asked for again on every call.
    assert manager.acquire("work", need_context=5200).context_length == 4096
    assert [o["context_length"] for o in runtime.load_options] == [4096]


# -- the overflow itself, as Windows counts it ------------------------------------------
#
# Seen on the development PC during a real run: a 27B model loaded with 37888 tokens held
# its speed when it was loaded (60 tok/s, 530 MiB free). An hour later the desktop had
# taken 1.5 GB more of the card, 858 MiB of the model were in system RAM and it answered
# at 8 tok/s. Neither the free figure nor a speed taken at load time could have said so.


def watching(runtime: GpuRuntime, store: MemoryProfileStore) -> tuple[ModelManager, list[dict[str, Any]]]:
    """A gateway that can read the overflow and looks at it on every request."""
    events: list[dict[str, Any]] = []
    manager = ModelManager(
        runtime, {"work": ModelSlot(model_key="qwen-q3", auto_tune=True)}, lambda t, p: events.append(p), vram_probe=runtime.probe,
        profiles=store, sleep=lambda seconds: None, spill_probe=runtime.spill, options=GatewayOptions(spill_check_seconds=0),
    )
    return manager, events


def test_a_load_that_overflows_is_refused_even_when_its_first_words_come_fast():
    class FastAtFirst(GpuRuntime):
        """The part that does not fit is the far end of the context: a short answer never touches it."""

        def spilling(self) -> bool:
            return False

    runtime = FastAtFirst(desktop=1713, squeeze=1000)
    manager, events = watching(runtime, edge_store(per_1k=24, speed=60))
    lease = manager.acquire("work", need_context=78000)
    refused = phases(events, "saturated")[0]
    # 69632 tokens answer at full speed, and 888 MiB of them are in system RAM.
    assert (refused["context_length"], refused["tokens_per_second"], refused["spilled_mib"]) == (69632, 60, 888)
    assert [o["context_length"] for o in runtime.load_options] == [69632, 36864] and lease.context_length == 36864
    assert runtime.spill() <= 300 and phases(events, "ready")[0]["saturated"] is False


def test_a_model_that_starts_to_overflow_while_it_works_is_reloaded_with_half_the_context():
    store = edge_store(per_1k=24, speed=60)
    runtime = GpuRuntime(desktop=1200, squeeze=1000)
    manager, events = watching(runtime, store)
    assert manager.acquire("work", need_context=30000).context_length == 37888
    assert store.get("qwen-q3").verified_context == 37888 and runtime.spill() == 0

    runtime.desktop = 2000  # other programs take 800 MiB more of the card
    assert round(runtime.spill()) == 413
    # Seen once, nothing happens: it could be a moment. Seen again, the model is reloaded smaller.
    assert manager.acquire("work", need_context=30000).context_length == 37888 and len(runtime.load_calls) == 1
    lease = manager.acquire("work", need_context=30000)
    assert lease.context_length == 18432 and [o["context_length"] for o in runtime.load_options] == [37888, 18432]
    assert runtime.spill() == 0
    noticed = phases(events, "spilling")[0]
    assert (noticed["context_length"], noticed["next_context"], noticed["spilled_mib"]) == (37888, 18432, 413)
    ready = phases(events, "ready")[-1]
    assert (ready["shrunk_from"], ready["spilled_mib"], ready["context_length"]) == (37888, 413, 18432)
    # The request still asks for more than it has. It is not reloaded for that: more was measured not to fit.
    assert manager.acquire("work", need_context=30000).context_length == 18432
    assert manager.acquire("work", need_context=30000).context_length == 18432 and len(runtime.load_calls) == 2
    # And the next run does not start from a context that stopped fitting.
    assert store.get("qwen-q3").verified_context == 18432


def test_an_overflow_at_the_smallest_context_is_said_once_and_changes_nothing():
    runtime = GpuRuntime(desktop=1713, squeeze=1000)
    manager, events = watching(runtime, edge_store(speed=60))
    assert manager.acquire("work", need_context=1200).context_length == 4096
    runtime.desktop = 3200
    for _ in range(4):
        assert manager.acquire("work", need_context=1200).context_length == 4096
    told = phases(events, "spilling")
    assert len(told) == 1 and told[0]["next_context"] is None and told[0]["spilled_mib"] == 802
    assert len(runtime.load_calls) == 1


def test_speed_is_not_measured_for_a_model_that_fits_as_planned():
    runtime = GpuRuntime(desktop=1000)
    manager, _ = gateway(runtime)
    manager.acquire("reason")
    assert runtime.measure_calls == 0


def test_never_goes_below_the_minimum_context():
    runtime = GpuRuntime(desktop=3000)  # not even 4096 tokens fit with the margin
    manager, events = gateway(runtime)
    lease = manager.acquire("reason")
    assert lease.context_length == 4096
    ready = phases(events, "ready")[0]
    assert ready["context_length"] == 4096 and ready["saturated"] is False  # 550 MiB still free


# -- one model at a time, unless two measurably fit -------------------------------------


def test_swaps_models_so_two_big_ones_are_never_in_vram_together():
    runtime = GpuRuntime()
    manager, events = gateway(runtime)
    manager.acquire("reason")
    manager.acquire("logs")  # 4200 + 55 * 32.768 = 6002 MiB does not fit next to the 27B
    assert runtime.loaded.keys() == {"spark-4b"} and runtime.unload_calls == ["qwen-27b"]
    assert runtime.peak_models == 1
    manager.acquire("reason")
    assert runtime.loaded.keys() == {"qwen-27b"} and runtime.peak_models == 1
    assert manager.switch_count == 3
    ready = phases(events, "ready")
    assert [e["to"] for e in ready] == ["qwen-27b", "spark-4b", "qwen-27b"]
    assert ready[1]["unloaded"] == ["qwen-27b"] and ready[1]["context_length"] == 32768


def test_two_small_models_stay_loaded_together_when_they_fit():
    runtime = GpuRuntime()
    two = {"a": ModelSlot(model_key="spark-4b", context_length=8192), "b": ModelSlot(model_key="gemma-12b", context_length=8192)}
    manager, _ = gateway(runtime, two)
    manager.acquire("a")
    manager.acquire("b")  # 4651 + 7369 + desktop 1000 = 13020 of 16376
    manager.acquire("a")
    assert runtime.loaded.keys() == {"spark-4b", "gemma-12b"} and runtime.unload_calls == []
    assert runtime.load_calls == ["spark-4b", "gemma-12b"]


def test_a_neighbour_is_unloaded_rather_than_shrinking_the_context():
    runtime = GpuRuntime()
    pair = {"a": ModelSlot(model_key="gemma-12b", context_length=8192), "b": ModelSlot(model_key="qwen-27b", context_length=16384)}
    manager, _ = gateway(runtime, pair)
    manager.acquire("a")
    lease = manager.acquire("b")
    assert lease.context_length == 16384 and runtime.loaded.keys() == {"qwen-27b"}


def test_keep_loaded_models_are_the_last_to_go():
    runtime = GpuRuntime()
    trio = {
        "keep": ModelSlot(model_key="spark-4b", context_length=4096, loading_policy=LoadingPolicy.KEEP_LOADED),
        "other": ModelSlot(model_key="gemma-12b", context_length=4096),
        "new": ModelSlot(model_key="gemma-12b", context_length=4096),
    }
    trio["new"] = ModelSlot(model_key="qwen-27b-wide", context_length=4096)  # 11126 MiB
    manager, _ = gateway(runtime, trio)
    manager.acquire("keep")   # 4425
    manager.acquire("other")  # + 7184 = 12609 with the desktop
    manager.acquire("new")    # needs 11126 + 350: evicting the on-demand model is not enough, both go
    assert runtime.unload_calls == ["gemma-12b", "spark-4b"]

    runtime = GpuRuntime()
    trio["new"] = ModelSlot(model_key="gemma-12b", context_length=8192)
    trio["other"] = ModelSlot(model_key="qwen-27b-wide", context_length=4096)
    manager, _ = gateway(runtime, trio)
    manager.acquire("keep")
    manager.acquire("other")  # 4425 + 11126 + 1000 = 16551: does not fit together, keep-loaded has to yield
    assert runtime.unload_calls == ["spark-4b"]


# -- the check after the load ---------------------------------------------------------------


def test_a_saturated_load_is_undone_and_retried_with_less_context():
    # The estimator is 900 MiB too optimistic for this model, so the first choice overflows.
    runtime = GpuRuntime(desktop=2700, estimate_error={"qwen-27b": 900})
    manager, events = gateway(runtime)
    lease = manager.acquire("reason")
    contexts = [o["context_length"] for o in runtime.load_options]
    assert contexts[0] == 16384 and contexts[1] == 12288  # 3/4 of the first try
    assert lease.context_length == contexts[-1]
    saturated = phases(events, "saturated")
    assert saturated and saturated[0]["vram_free_mib"] < 480
    ready = phases(events, "ready")[0]
    assert ready["attempts"] == len(contexts) and ready["saturated"] is False
    assert runtime.probe().free_mib >= 480


def test_saturation_first_evicts_a_neighbour_before_giving_up_context():
    runtime = GpuRuntime(desktop=900, estimate_error={"qwen-27b-wide": 5000})
    pair = {"a": ModelSlot(model_key="gemma-12b", context_length=8192), "b": ModelSlot(model_key="qwen-27b-wide", context_length=4096)}
    manager, events = gateway(runtime, pair)
    manager.acquire("a")
    lease = manager.acquire("b")  # by the estimate it fits next to the 12B; it does not
    assert phases(events, "saturated")
    assert runtime.loaded.keys() == {"qwen-27b-wide"} and lease.context_length == 4096
    assert phases(events, "ready")[-1]["unloaded"] == ["gemma-12b"]


def test_gives_up_shrinking_after_the_attempt_limit_and_says_it_is_saturated():
    runtime = GpuRuntime(desktop=3300, estimate_error={"qwen-27b": 4000})
    manager, events = gateway(runtime, options=GatewayOptions(max_attempts=2))
    lease = manager.acquire("reason")
    ready = phases(events, "ready")[0]
    assert ready["attempts"] == 2 and ready["saturated"] is True
    assert lease.model_key == "qwen-27b"  # the run goes on; the session shows the warning


def test_a_real_load_corrects_the_estimate_for_next_time():
    store = MemoryProfileStore()
    runtime = GpuRuntime(estimate_error={"spark-4b": 300})
    manager, _ = gateway(runtime, profiles=store)
    manager.acquire("logs")
    profile = store.get("spark-4b")
    assert profile is not None and profile.source == "measured"
    assert profile.fixed_mib == pytest.approx(4200, abs=5) and profile.per_1k_mib == pytest.approx(55, abs=0.5)
    # The estimator is asked only once per model: the profile is remembered.
    asked = len(runtime.estimate_calls)
    manager._unload("spark-4b")
    manager.acquire("logs")
    assert len(runtime.estimate_calls) == asked


def test_a_pessimistic_estimate_is_replaced_by_two_real_loads():
    """The numbers measured on the development PC for a 27B model.

    LM Studio estimated 15258 MiB at 4096 tokens and about 100 MiB per 1000 tokens more.
    Real loads took 14422 MiB at 4096 and 14583 at 8192: 14258 MiB fixed, 40 per 1000.
    """
    store = MemoryProfileStore()
    runtime = GpuRuntime(desktop=838, estimate_error={"qwen-real": -590}, estimate_slope={"qwen-real": 2.5})
    one = {"big": ModelSlot(model_key="qwen-real", context_length=32768)}
    manager, events = gateway(runtime, one, profiles=store)
    lease = manager.acquire("big")
    # By the estimate only 4096 tokens fit. The real load shows room for more: one reload, more than double.
    assert [o["context_length"] for o in runtime.load_options] == [4096, 9216] and lease.context_length == 9216
    retuning = phases(events, "retuning")[0]
    assert (retuning["from_context"], retuning["context_length"]) == (4096, 9216)
    ready = phases(events, "ready")[0]
    assert ready["retuned"] is True and ready["attempts"] == 2 and ready["saturated"] is False
    profile = store.get("qwen-real")
    assert profile is not None and profile.source == "measured" and len(profile.points) == 2
    assert profile.fixed_mib == pytest.approx(14258, abs=3) and profile.per_1k_mib == pytest.approx(39.9, abs=0.2)

    # Next run: no estimate, no second load, and the context the card really allows.
    runtime.unload("qwen-real")
    later, events = gateway(runtime, one, profiles=store)
    asked = len(runtime.estimate_calls)
    lease = later.acquire("big")
    # 15538 free - 600 - 14258 = 680 MiB at 39.9 per 1000 tokens -> 17K -> 16384 after rounding.
    assert lease.context_length == 16384 and len(runtime.estimate_calls) == asked
    assert not phases(events, "retuning") and phases(events, "ready")[0]["attempts"] == 1
    assert runtime.probe().free_mib >= 600


def test_a_reading_disturbed_by_another_program_is_not_learned():
    profile = MemoryProfile(fixed_mib=12000, per_1k_mib=40, estimate_per_1k_mib=40)
    learned = profile.with_load(8192, 12330).with_load(16384, 12650)
    assert learned.per_1k_mib == pytest.approx(39.1, abs=0.2) and learned.source == "measured"
    # A second point that implies a negative or absurd slope keeps the last good slope.
    odd = profile.with_load(8192, 12330).with_load(16384, 11000)
    assert odd.per_1k_mib == 40
    wild = profile.with_load(8192, 12330).with_load(12288, 13500)
    assert wild.per_1k_mib == 40


def test_profiles_survive_in_the_database(db):
    store = MemoryProfileStore(db)
    store.put("m", MemoryProfile(fixed_mib=1000, per_1k_mib=40, source="measured"))
    again = MemoryProfileStore(db).get("m")
    assert again is not None and again.need_mib(8000) == 1320 and again.source == "measured"


# -- wide-context variant ---------------------------------------------------------------------


def wide_slots() -> dict[str, ModelSlot]:
    return {
        "reason": ModelSlot(model_key="qwen-27b", context_length=16384, wide_model_key="qwen-27b-wide", wide_context_length=49152),
        "logs": ModelSlot(model_key="spark-4b", context_length=32768),
    }


def test_a_request_too_large_for_the_model_goes_to_its_wide_context_variant():
    runtime = GpuRuntime()
    manager, events = gateway(runtime, wide_slots())
    small = manager.acquire("reason", need_context=6000)
    assert (small.model_key, small.wide) == ("qwen-27b", False)

    big = manager.acquire("reason", need_context=40000)
    assert (big.model_key, big.wide) == ("qwen-27b-wide", True)
    # 1.7 GB less weight became context: 49152 fits (10970 + 38 * 49.152 = 12838 MiB).
    assert big.context_length == 49152 and runtime.loaded.keys() == {"qwen-27b-wide"}
    assert phases(events, "ready")[-1]["wide"] is True

    # While it is in memory, another large request reuses it...
    manager.acquire("reason", need_context=30000)
    assert runtime.load_calls == ["qwen-27b", "qwen-27b-wide"]
    # ...and an ordinary one goes back to the better model.
    back = manager.acquire("reason", need_context=6000)
    assert back.model_key == "qwen-27b" and runtime.loaded.keys() == {"qwen-27b"}


def test_wide_variant_keeps_serving_while_the_main_model_cannot_get_enough_context():
    # A busy desktop: the main model fits only 4096 tokens, less than this agent's 6000.
    runtime = GpuRuntime(desktop=3000)
    manager, _ = gateway(runtime, wide_slots())
    first = manager.acquire("reason", need_context=6000)
    assert first.wide and first.context_length >= 6000
    # The same kind of request again: the variant in memory is enough, nothing is reloaded.
    again = manager.acquire("reason", need_context=6000)
    assert again.wide and runtime.load_calls == ["qwen-27b-wide"] and runtime.unload_calls == []


def test_without_a_wide_variant_the_model_keeps_serving_with_what_it_has():
    runtime = GpuRuntime()
    manager, _ = gateway(runtime)
    lease = manager.acquire("reason", need_context=40000)
    assert lease.model_key == "qwen-27b" and lease.context_length == 16384


# -- the runtime is the truth ---------------------------------------------------------------------


def test_a_model_unloaded_behind_our_back_is_loaded_again():
    runtime = GpuRuntime()
    manager, _ = gateway(runtime)
    manager.acquire("reason")
    runtime.unload("qwen-27b")  # idle timeout, or the user pressed Eject in LM Studio
    lease = manager.acquire("reason")
    assert lease.model_key == "qwen-27b" and runtime.load_calls == ["qwen-27b", "qwen-27b"]


def test_an_instance_someone_else_loaded_is_used_if_its_context_is_enough():
    runtime = GpuRuntime()
    runtime.loaded["qwen-27b"] = "qwen-27b"
    runtime.contexts["qwen-27b"] = 8192
    manager, events = gateway(runtime)
    lease = manager.acquire("reason", need_context=5000)
    assert lease.context_length == 8192 and runtime.load_calls == [] and not events

    bigger = manager.acquire("reason", need_context=14000)  # now it is too small: reload with room
    assert bigger.context_length == 16384 and runtime.unload_calls == ["qwen-27b"]
    manager.close()
    assert "qwen-27b" not in runtime.loaded  # this run loaded it, so this run cleans up


def test_close_leaves_models_it_did_not_load_and_keep_loaded_ones():
    runtime = GpuRuntime()
    runtime.loaded["gemma-12b"] = "gemma-12b"
    runtime.contexts["gemma-12b"] = 4096
    pair = {
        "a": ModelSlot(model_key="gemma-12b", context_length=4096),
        "b": ModelSlot(model_key="spark-4b", context_length=4096, loading_policy=LoadingPolicy.KEEP_LOADED),
    }
    manager, _ = gateway(runtime, pair)
    manager.acquire("a")
    manager.acquire("b")
    manager.close()
    assert runtime.loaded.keys() == {"gemma-12b", "spark-4b"} and runtime.unload_calls == []


# -- models that do not fit the card at all ---------------------------------------------------------


def test_a_model_larger_than_the_card_loads_as_planned_without_context_games():
    runtime = GpuRuntime()
    big = {"x": ModelSlot(model_key="giant-70b", context_length=8192), "logs": ModelSlot(model_key="spark-4b", context_length=4096)}
    manager, events = gateway(runtime, big)
    manager.acquire("logs")
    lease = manager.acquire("x")
    ready = phases(events, "ready")[-1]
    # The runtime offloads part of it to system RAM by design: no retries, everything else unloaded.
    assert lease.context_length == 8192 and ready["partial_offload"] is True and ready["attempts"] == 1
    assert runtime.loaded.keys() == {"giant-70b"}


def test_load_failure_is_reported():
    runtime = GpuRuntime(fail_load={"qwen-27b"})
    manager, events = gateway(runtime)
    with pytest.raises(ModelLoadError, match="qwen-27b"):
        manager.acquire("reason")
    assert phases(events, "failed")


def test_unknown_or_empty_slot():
    runtime = GpuRuntime()
    manager, _ = gateway(runtime, {"empty": ModelSlot()})
    with pytest.raises(ModelLoadError, match="no model assigned"):
        manager.acquire("empty")
    with pytest.raises(ModelLoadError, match="Unknown model slot"):
        manager.acquire("nope")


# -- inside a workflow run -----------------------------------------------------------------------------


WORKFLOW = """
workflow: {id: multi, name: Multi model, max_steps: 20}
models:
  reason: {model_key: qwen-27b, context_length: 16384, vram_gb: 13.3}
  logs: {model_key: spark-4b, context_length: 32768, vram_gb: 6.0}
agents:
  - {id: planner, role: Planner, goal: plan, model: reason, output_schema: GenericOutput, system_prompt: "Plan the work in a few clear steps and name the risks."}
  - {id: logreader, role: Log Reader, goal: read logs, model: logs, output_schema: GenericOutput, system_prompt: "Read the log and report every error with its line."}
  - {id: reviewer, role: Reviewer, goal: review, model: reason, output_schema: GenericOutput, system_prompt: "Review the findings and say what is still missing."}
edges:
  - {from: START, to: planner}
  - {from: planner, to: logreader}
  - {from: logreader, to: reviewer}
  - {from: reviewer, to: END}
"""


class CheckedRuntime(GpuRuntime):
    """Fails the test the moment a request is sent to a model that is not the only one in memory."""

    def complete(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        assert model in self.loaded.values(), f"request sent to {model}, which is not loaded: {list(self.loaded)}"
        assert len(self.loaded) == 1, f"two models in VRAM during a request: {list(self.loaded)}"
        return super().complete(model, messages, **kwargs)


def test_each_agent_is_served_by_its_own_model_one_in_memory_at_a_time(db):
    from agentfit.events import EventBus
    from agentfit.workflows.runner import RunManager

    runtime = CheckedRuntime()
    runs = RunManager(db, runtime, bus=EventBus(), checkpoint_path=":memory:", vram_probe=runtime.probe)
    record = runs.start(load_yaml(WORKFLOW), "find the errors", background=False)
    assert record.status == "completed", record.error
    assert [c["model"] for c in runtime.calls] == ["qwen-27b", "spark-4b", "qwen-27b"]
    assert runtime.load_calls == ["qwen-27b", "spark-4b", "qwen-27b"] and runtime.peak_models == 1
    assert record.result["model_switches"] == 3
    assert runtime.loaded == {}  # end of run: everything this run loaded is unloaded
    switches = [e["payload"] for e in runs.events(record.id) if e["type"] == "model.switch" and e["payload"].get("phase") == "ready"]
    assert [s["context_length"] for s in switches] == [16384, 32768, 16384]


def test_the_prompt_is_built_for_the_context_the_model_really_got():
    definition = load_yaml(WORKFLOW)
    reviewer = definition.agent("reviewer")
    assert reviewer is not None
    state: dict[str, Any] = {
        "task": "summarize",
        "history": ["planner", "logreader"],
        "outputs": {"planner": {"summary": "p" * 30000}, "logreader": {"summary": "l" * 60000}},
    }
    demand = prompt_demand(reviewer, state, definition)  # type: ignore[arg-type]
    assert demand > 40000  # more than the slot's 16384: this is what asks for a wide variant
    full = build_messages(reviewer, state, definition, 16384)  # type: ignore[arg-type]
    reduced = build_messages(reviewer, state, definition, 4096)  # type: ignore[arg-type]
    assert len(reduced[1]["content"]) < len(full[1]["content"])
    assert len(reduced[1]["content"]) <= 4096 * 3.5 * 0.55 + 2000
