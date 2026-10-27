"""The generated map (docs/map/) and the code it describes: it lists the routes the app really serves, it is the same every time,
and it is small enough for a model with a short window to read."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.routing import APIRoute

from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.config import Settings
from agentfit.db import Database
from agentfit.events import EventBus

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_context.py"
INDEX_LIMIT = 8_000  # characters: about 2,000 tokens, what a model is asked to read first


@pytest.fixture(scope="module")
def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_context", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_context"] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def files(builder) -> dict[str, str]:
    return builder.build_files()


def test_the_routes_in_the_map_are_the_routes_the_app_serves(builder):
    app = create_app(AppServices(db=Database(":memory:"), settings=Settings(), event_bus=EventBus()))
    served = {(method, route.path) for route in app.routes if isinstance(route, APIRoute) for method in route.methods - {"HEAD", "OPTIONS"}}
    listed = {(route.method.upper(), route.path) for route in builder.load_routes()}

    assert served == listed, (
        f"served but not in the map: {sorted(served - listed)}; in the map but not served: {sorted(listed - served)}. "
        "A route was added in a way scripts/build_context.py does not read (a router, a computed path): teach it, or write the route as `@app.get(\"/api/...\")`."
    )


def test_the_map_is_the_same_every_time(builder, files):
    assert builder.build_files() == files


def test_every_card_says_it_is_generated_and_fits_a_small_window(builder, files):
    for name, text in files.items():
        assert text.startswith(builder.HEADER), f"{name} does not start with the generated-file header"
        assert len(text) <= builder.CARD_LIMIT, f"{name} is {len(text)} characters: split it by sub-package in scripts/build_context.py"


def test_the_index_is_short_and_leads_to_every_other_card(files):
    index = files["INDEX.md"]

    assert len(index) <= INDEX_LIMIT
    assert [name for name in files if name != "INDEX.md" and name not in index] == []


def test_the_map_knows_the_parts_of_the_product_people_ask_about(files):
    everything = "\n".join(files.values())

    for part in ("arena", "imagegen", "brain", "workflows", "/api/arena", "image_generation"):
        assert part in everything, f"{part} is missing from docs/map/"
