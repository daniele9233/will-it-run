"""The Will It Run core in an isolated home, talking to the stand-in LM Studio.

For trying the interface end to end while the real LM Studio is busy or absent: nothing
here reaches it. The ``lms`` CLI is switched off, or loads would go to the real program,
and the runtime cannot be swapped back by a settings change.

    python scripts/dev/lmstudio_stub.py 12399
    python scripts/dev/core_with_stub.py [port] [stub port]     # defaults 8791 and 12399
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("AGENTFIT_HOME", str(Path(tempfile.gettempdir()) / "willitrun-stub-home"))

import uvicorn

from agentfit.api.server import create_app
from agentfit.app import AppServices
from agentfit.lmstudio.cli import LmsCli
from agentfit.lmstudio.client import LMStudioClient
from agentfit.lmstudio.runtime import LMStudioRuntime
from agentfit.workflows.runner import RunManager

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
stub = int(sys.argv[2]) if len(sys.argv) > 2 else 12399
STUB_URL = f"http://127.0.0.1:{stub}"


class StubServices(AppServices):
    """Services whose runtime is always the stand-in.

    ``apply_settings`` drops the runtime so that the next use rebuilds it from the settings,
    which would be the real LM Studio. Here the property cannot return anything else.
    """

    _stub: LMStudioRuntime | None = None

    @property
    def runtime(self) -> LMStudioRuntime:  # type: ignore[override]
        if StubServices._stub is None:
            StubServices._stub = LMStudioRuntime(settings=self.settings, client=LMStudioClient(STUB_URL), cli=LmsCli(None))
        return StubServices._stub


services = StubServices()
if not services.settings.ui.first_run_complete:
    services.apply_settings({"ui": {"first_run_complete": True}})
# No VRAM probe: the stand-in holds no memory, and the real card may be full of someone else's model.
# Cloud models and the site cloner as in the app: the stand-in only replaces LM Studio.
services._runs = RunManager(
    services.db, services.models_runtime, bus=services.bus, require_approval=True, brain=services.brain,
    clone_session=lambda run_id, workspace, cancelled, emit: services.clone_sessions.session(run_id, workspace, cancelled, emit),
    clone_session_close=lambda run_id: services.clone_sessions.close(run_id),
)
_ = services.kits
assert services.runtime.client.base_url == STUB_URL and not services.runtime.cli.available
assert services.runs.runtime.local is services.runtime
sys.stdout.write(f"core on {port}, stand-in LM Studio on {stub}, home {os.environ['AGENTFIT_HOME']}\n")
sys.stdout.flush()
uvicorn.run(create_app(services), host="127.0.0.1", port=port, log_level="warning", access_log=False)
