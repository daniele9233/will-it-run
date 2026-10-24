from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Keep every test away from the user's real Will It Run data.
_HOME = tempfile.mkdtemp(prefix="agentfit-tests-")
os.environ["AGENTFIT_HOME"] = _HOME
os.environ.pop("AGENTFIT_TOKEN", None)
sys.path.insert(0, str(Path(__file__).parent))

from agentfit.config import Settings  # noqa: E402
from agentfit.db import Database  # noqa: E402
from agentfit.hardware import compute_budget  # noqa: E402
from agentfit.hardware import nvidia as nvidia_mod  # noqa: E402
from agentfit.hardware.budget import ResourceBudget  # noqa: E402
from agentfit.hardware.detect import build_profile  # noqa: E402
from agentfit.hardware.models import HardwareProfile  # noqa: E402


@pytest.fixture(autouse=True)
def _no_github_cli(monkeypatch):
    """No test reaches the GitHub CLI of the PC it runs on, or the account signed in there."""
    monkeypatch.setattr("agentfit.github.cli_path", lambda: None)


@pytest.fixture(autouse=True)
def _no_kit_downloads(monkeypatch):
    """No test fetches a cloning kit from GitHub or starts npm: a test that wants a kit builds its own store."""

    def offline(args, cwd, timeout):
        raise OSError("tests do not run git against GitHub")

    monkeypatch.setattr("agentfit.clone.kits._git", offline)


FIXTURES = Path(__file__).parent / "fixtures" / "hardware"
AVX2 = {"sse4_1": True, "sse4_2": True, "avx": True, "avx2": True, "avx512f": False}


def load_profile(name: str) -> HardwareProfile:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    smi = nvidia_mod.parse_query_csv(data["nvidia_smi"])
    return build_profile(data["probe"], smi, AVX2)


def budget_for(name: str, settings: Settings | None = None) -> ResourceBudget:
    return compute_budget(load_profile(name), (settings or Settings()).hardware)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def db() -> Database:
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def budget_4080s() -> ResourceBudget:
    return budget_for("rtx4080super_16gb_32gb")


@pytest.fixture(autouse=True)
def _english_core(monkeypatch):
    """Tests assert on English text whatever the language of the machine running them."""
    from agentfit import i18n

    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    i18n.set_default_language("en")

