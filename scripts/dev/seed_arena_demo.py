"""Put a finished Arena run, made by made-up models, into the stub core's home, to look at the Arena screens with.

Nothing here reaches LM Studio: the runtime is the test fake, with each model answering right a different share of the time
and at a different speed. The numbers it produces are invented: they are for looking at the layout, never for a screenshot
in the documentation. It takes about four minutes. Stop the stub core first (a core that starts while the run works marks it
as interrupted), then start it again.

    backend\\.venv\\Scripts\\python.exe scripts\\dev\\seed_arena_demo.py
"""

import os
import sys
import tempfile
import time
import zlib
from pathlib import Path

os.environ.setdefault("AGENTFIT_HOME", str(Path(tempfile.gettempdir()) / "willitrun-stub-home"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "tests"))

from arena_fakes import ArenaRuntime, installed

from agentfit.arena.runner import ArenaService, RunRequest
from agentfit.arena.store import ArenaStore
from agentfit.db import get_db
from agentfit.events import EventBus
from agentfit.workflows.model_manager import ModelManager

# key -> (name, share of right answers, tokens per second, sees pictures, parameters)
MODELS = {
    "qwen3.8-27b-gsq": ("Qwen3.8 27B GSQ", 0.90, 66, False, "27B"),
    "gemma-4-12b-qat": ("Gemma 4 12B QAT", 0.82, 58, True, "12B"),
    "spark-x2.5-4b": ("Spark X2.5 4B", 0.62, 112, False, "4.1B"),
    "medgemma-1.5-4b": ("MedGemma 1.5 4B", 0.48, 31, True, "4B"),
    "ui-tars-7b": ("UI-TARS 7B SFT", 0.40, 44, True, "7B"),
}


def chance(model: str, question_id: str) -> float:
    return (zlib.crc32(f"{model}:{question_id}".encode()) % 1000) / 1000


class Demo(ArenaRuntime):
    def complete(self, model, messages, **kwargs):
        question = self.question_of(messages)
        right = chance(model, question.id) < MODELS[model][1]
        self.behaviour[model] = "right" if right else "wrong"
        if model == "spark-x2.5-4b" and question.id in ("general.003", "math.002"):
            raise RuntimeError("the server closed the connection")
        reply = super().complete(model, messages, **kwargs)
        reply["usage"] = {"prompt_tokens": 120, "completion_tokens": 24}
        time.sleep(24 / MODELS[model][2])
        return reply


def main() -> None:
    db = get_db()
    runtime = Demo()
    runtime.models = [installed(k, vision=v[3], name=v[0]) for k, v in MODELS.items()]
    for model in runtime.models:
        model.params_string = MODELS[model.model_key][4]
    service = ArenaService(ArenaStore(db), runtime, EventBus(), installed=lambda: runtime.models, gateway=lambda slots, emit: ModelManager(runtime, slots, emit))
    run_id = service.start(RunRequest(models=list(MODELS), preset="standard", title="Prova dimostrativa (dati inventati)"))
    print("started", run_id, flush=True)
    service.join(run_id, 900)
    print("done", ArenaStore(db).get_run(run_id)["status"], flush=True)


main()
