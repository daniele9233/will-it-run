"""Database, settings, logging redaction, paths, events and benchmark storage."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentfit import branding, paths
from agentfit.benchmark import BenchmarkEngine, BenchmarkError
from agentfit.config import AlignmentPreference, Settings, load_settings, save_settings, update_settings
from agentfit.db import Database
from agentfit.events import EventBus
from agentfit.lmstudio.models import InstalledModel
from agentfit.logging_setup import REDACTED, redact, redact_text
from fakes import FakeRuntime

# -- database -----------------------------------------------------------------


def test_migrations_create_every_entity(db):
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    expected = {"hardware_profile", "model_metadata", "installed_model", "benchmark", "download", "workflow", "team",
                "workflow_run", "run_event", "model_usage", "tool_call", "approval", "kv", "schema_migrations",
                "brain_project", "brain_memory", "brain_entity", "brain_edge", "arena_run", "arena_question", "arena_model", "arena_result",
                "image_generation", "image_component"}
    assert expected <= tables
    assert db.schema_version() == 6


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "x.sqlite3"
    first = Database(path)
    first.kv_set("ns", "k", {"a": 1})
    first.close()
    second = Database(path)
    assert second.migrate() == 6 and second.kv_get("ns", "k") == {"a": 1}
    second.close()


def test_kv_store_round_trip_and_age(db):
    db.kv_set("cache", "one", [1, 2, 3])
    db.kv_set("cache", "one", {"replaced": True})
    assert db.kv_get("cache", "one") == {"replaced": True}
    value, age = db.kv_get_with_age("cache", "one")
    assert value == {"replaced": True} and 0 <= age < 0.1
    assert db.kv_list("cache") == {"one": {"replaced": True}}
    db.kv_delete("cache", "one")
    assert db.kv_get("cache", "one") is None


def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO kv(namespace, key, value, updated_at) VALUES ('t', 'k', '1', 'now')")
        raise RuntimeError("boom")
    assert db.kv_get("t", "k") is None


def test_deleting_a_run_cascades(db):
    db.execute("INSERT INTO workflow_run(id, workflow_id, title, task, status, started_at) VALUES ('r1', 'w', 't', 'task', 'completed', 'now')")
    db.execute("INSERT INTO run_event(run_id, seq, type, payload, created_at) VALUES ('r1', 1, 'x', '{}', 'now')")
    db.execute("DELETE FROM workflow_run WHERE id = 'r1'")
    assert db.query("SELECT * FROM run_event") == []


# -- paths --------------------------------------------------------------------


def test_data_lives_under_windows_appdata_not_a_hardcoded_home(monkeypatch):
    monkeypatch.delenv("AGENTFIT_HOME")
    monkeypatch.setenv("LOCALAPPDATA", r"X:\Profiles\someone\AppData\Local")
    monkeypatch.setenv("APPDATA", r"X:\Profiles\someone\AppData\Roaming")
    # Data sits beside, not among, the per-user install's program files.
    assert paths.local_dir() == Path(r"X:\Profiles\someone\AppData\Local") / branding.APP_NAME / "Data"
    assert paths.roaming_dir() == Path(r"X:\Profiles\someone\AppData\Roaming") / branding.APP_NAME


def _old_profile(tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A Windows profile that still has the folders of the app's previous name."""
    local, roaming = tmp_path / "Local", tmp_path / "Roaming"
    monkeypatch.delenv("AGENTFIT_HOME")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setattr(paths, "_legacy_checked", False)
    old_data = local / branding.LEGACY_APP_NAME / "Data"
    (old_data / "logs").mkdir(parents=True)
    (old_data / "agentfit.sqlite3").write_bytes(b"benchmarks and teams")
    (old_data / "logs" / "app.log").write_text("history", encoding="utf-8")
    old_roaming = roaming / branding.LEGACY_APP_NAME
    old_roaming.mkdir(parents=True)
    (old_roaming / "settings.yaml").write_text("ui:\n  language: it\n", encoding="utf-8")
    return old_data, old_roaming


def test_data_of_the_previous_product_name_comes_along(tmp_path, monkeypatch):
    old_data, old_roaming = _old_profile(tmp_path, monkeypatch)
    assert branding.APP_NAME == "Will It Run" and branding.LEGACY_APP_NAME == "AgentFit"

    new_data = paths.local_dir()
    assert new_data == tmp_path / "Local" / "Will It Run" / "Data"
    assert (new_data / "agentfit.sqlite3").read_bytes() == b"benchmarks and teams"
    assert (new_data / "logs" / "app.log").read_text(encoding="utf-8") == "history"
    assert load_settings().ui.language == "it"  # read from the copied settings file
    # The old folders are a backup: nothing is moved or deleted.
    assert (old_data / "agentfit.sqlite3").exists() and (old_roaming / "settings.yaml").exists()


def test_existing_data_is_never_overwritten_by_the_old_folders(tmp_path, monkeypatch):
    _old_profile(tmp_path, monkeypatch)
    current = tmp_path / "Local" / "Will It Run" / "Data"
    current.mkdir(parents=True)
    (current / "agentfit.sqlite3").write_bytes(b"newer data")
    assert (paths.local_dir() / "agentfit.sqlite3").read_bytes() == b"newer data"
    assert not (current / "logs").exists()


def test_a_fresh_pc_has_nothing_to_carry_over(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTFIT_HOME")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(paths, "_legacy_checked", False)
    assert paths.adopt_legacy_data() == []
    assert not (tmp_path / "Local" / "Will It Run").exists()


def test_source_never_hardcodes_a_user_profile():
    root = Path(__file__).resolve().parents[1] / "src" / "agentfit"
    offenders = [p.name for p in root.rglob("*.py") if "C:\\Users\\" in p.read_text(encoding="utf-8") or "C:/Users/" in p.read_text(encoding="utf-8")]
    assert offenders == []


# -- settings -----------------------------------------------------------------


def test_default_settings_match_documented_values():
    s = Settings()
    assert s.runtime.primary == "lmstudio" and s.lmstudio_base_url == "http://127.0.0.1:1234"
    assert s.models.alignment_preference == AlignmentPreference.ANY
    assert s.performance.target_tokens_per_second == 20
    assert s.workflow.max_retries == 3 and s.workflow.require_approval_for_high_risk_actions
    assert s.storage.reserve_percent == 15 and s.ui.theme == "dark"


def test_settings_persist_and_merge():
    save_settings(Settings())
    updated = update_settings({"models": {"alignment_preference": "uncensored"}, "hardware": {"vram_os_reserve_gb": 2.0}})
    assert updated.models.alignment_preference == AlignmentPreference.UNCENSORED
    reloaded = load_settings()
    assert reloaded.hardware.vram_os_reserve_gb == 2.0 and reloaded.hardware.vram_reserve_percent == 5.0
    assert reloaded.storage.reserve_percent == 15  # untouched sections survive
    paths.settings_path().unlink()


def test_corrupt_settings_fall_back_to_defaults():
    paths.settings_path().write_text("{{{ not yaml", encoding="utf-8")
    assert load_settings() == Settings()
    paths.settings_path().unlink()


def test_invalid_settings_are_rejected():
    with pytest.raises(ValueError):
        update_settings({"models": {"alignment_preference": "forbidden"}})


# -- redaction ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdef1234567890XYZ",
        "password=hunter2secret",
        "api_key: sk-proj-abcdefghijklmnopqrstuvwx",
        'token="hf_abcdefghijklmnopqrstuvwxyz012345"',
        "cookie=sessionid=abc123def456; path=/",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
        "export GITHUB=ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    ],
)
def test_secrets_are_redacted_from_text(text):
    cleaned = redact_text(text)
    assert REDACTED in cleaned
    for secret in ("hunter2secret", "abcdef1234567890XYZ", "sk-proj-abcdefghijklmnopqrstuvwx", "hf_abcdefghijklmnopqrstuvwxyz012345", "MIIEpAIBAAKCAQEA", "ghp_abcdefghijklmnopqrstuvwxyz0123456789", "abc123def456"):
        assert secret not in cleaned


def test_structured_data_is_redacted_by_key():
    data = {"user": "kim", "password": "p4ss", "nested": {"api_key": "k", "note": "token=abc123xyz"}, "list": [{"Authorization": "Bearer zzzzzzzzzzzz"}]}
    cleaned = redact(data)
    assert cleaned["user"] == "kim" and cleaned["password"] == REDACTED
    assert cleaned["nested"]["api_key"] == REDACTED and "abc123xyz" not in cleaned["nested"]["note"]
    assert cleaned["list"][0]["Authorization"] == REDACTED


def test_ordinary_text_is_left_alone():
    text = "Loaded qwen3-14b in 3.6s using 11.9 GB VRAM at 47.8 tok/s"
    assert redact_text(text) == text


# -- events -------------------------------------------------------------------


def test_event_bus_delivers_to_subscribers_and_keeps_a_backlog():
    bus = EventBus(backlog=3)
    sub = bus.subscribe()
    for i in range(5):
        bus.publish("download.update", {"i": i})
    bus.publish("notify", {"title": "done"})
    assert [sub.get_nowait()["data"].get("i") for _ in range(5)] == [0, 1, 2, 3, 4]
    assert len(bus.recent()) == 3 and [e["topic"] for e in bus.recent("notify")] == ["notify"]
    bus.unsubscribe(sub)
    bus.publish("x")
    assert sub.qsize() == 1  # only the earlier notify remains; nothing new arrives


# -- benchmark ----------------------------------------------------------------


def test_benchmark_measures_stores_and_restores_state(db):
    runtime = FakeRuntime()
    runtime.installed = [InstalledModel(model_key="spark", display_name="Spark 4B", quant="Q8_0")]
    runtime.loaded = {"other-model": "other-model"}
    stages: list[str] = []
    engine = BenchmarkEngine(db, runtime, bus=EventBus())
    result = engine.run("spark", max_output_tokens=128, progress=lambda stage, data: stages.append(stage))
    assert stages == ["unloading", "loading", "warmup", "generating", "done"]
    assert result.generation_tps == 42.0 and result.ttft_ms == 200.0 and result.output_tokens == 128
    assert result.prompt_tps == pytest.approx(500.0) and any("approximate" in n for n in result.notes)
    assert result.quant == "Q8_0" and result.load_time_s == 0.01
    assert runtime.unload_calls == ["other-model", "spark"]  # others unloaded first, model unloaded after
    history = engine.history("spark")
    assert len(history) == 1 and history[0].generation_tps == 42.0 and history[0].id == result.id


def test_benchmark_keeps_an_already_loaded_model_loaded(db):
    runtime = FakeRuntime()
    runtime.installed = [InstalledModel(model_key="spark", display_name="Spark 4B")]
    runtime.loaded = {"spark": "spark"}
    result = BenchmarkEngine(db, runtime, bus=EventBus()).run("spark")
    assert result.load_time_s is None and result.vram_used_mb is None
    assert any("already loaded" in n for n in result.notes)
    assert runtime.unload_calls == [] and "spark" in runtime.loaded


def test_benchmark_errors_are_explained(db):
    runtime = FakeRuntime()
    with pytest.raises(BenchmarkError, match="not installed"):
        BenchmarkEngine(db, runtime, bus=EventBus()).run("missing")
    runtime.is_available = lambda: False  # type: ignore[method-assign]
    with pytest.raises(BenchmarkError, match="not running"):
        BenchmarkEngine(db, runtime, bus=EventBus()).run("missing")


def test_tests_never_touch_real_user_data():
    assert os.environ["AGENTFIT_HOME"] and "agentfit-tests-" in str(paths.local_dir())
