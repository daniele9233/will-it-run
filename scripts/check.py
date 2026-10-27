"""Run the checks that matter for what you changed, and only those. Exit code 1 when one fails.

    python scripts/check.py            # what differs from HEAD: staged, unstaged and new files
    python scripts/check.py --all      # everything CI runs
    python scripts/check.py --plan     # say what it would run, run nothing

The tests are chosen from the code, not guessed: a test runs when it imports a module you changed or a module that imports
one you changed (``scripts/build_context.py`` reads the imports). A change to shared test code, a migration, a translation
catalog or the project settings runs the whole backend suite. The map in ``docs/map/`` is rewritten first, so it never
fails the check by being old.

It is meant for whoever changes the software, human or model: run it before saying a change is done.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DESKTOP = ROOT / "desktop"
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
# Code outside backend/ that has tests of its own: path prefix -> the test files to run when it changes.
SCRIPT_TESTS = {
    "scripts/build_context.py": ["test_context_map.py"],
    "scripts/hooks/": ["test_agent_hooks.py"],
    ".githooks/": ["test_agent_hooks.py"],
    ".claude/settings.json": ["test_agent_hooks.py"],
}
RUN_ALL_BACKEND = ("backend/tests/conftest.py", "backend/pyproject.toml", "backend/src/agentfit/migrations/", "backend/src/agentfit/locales/")


def load_builder():
    spec = importlib.util.spec_from_file_location("build_context", ROOT / "scripts" / "build_context.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_context"] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    module.TRACKED = None  # choosing tests looks at the working tree, new files included; the written map stays tracked-only
    return module


def git_lines(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False).stdout
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def changed_files() -> list[str]:
    files = set(git_lines("diff", "--name-only", "HEAD")) | set(git_lines("ls-files", "--others", "--exclude-standard"))
    return sorted(f for f in files if not f.startswith(("docs/map/", ".codegraph/")))


def python_for_tests() -> list[str]:
    return [str(VENV_PY)] if VENV_PY.exists() else [sys.executable]


def tests_for(changed: list[str], builder) -> tuple[list[str], str]:
    """The test files to run, and why. An empty list with the reason 'all' means the whole suite."""
    py = [f for f in changed if f.startswith("backend/") and f.endswith(".py")]
    if any(f.startswith(RUN_ALL_BACKEND) or f.endswith("fakes.py") for f in changed):
        return [], "all"
    modules = builder.load_python()
    builder.load_tests(modules)
    reverse: dict[str, set[str]] = {}
    for module in modules.values():
        for target in module.imports:
            reverse.setdefault(target, set()).add(module.module)
    touched: set[str] = set()
    tests: set[str] = set()
    for f in py:
        path = ROOT / f
        if "/tests/" in f:
            if path.name.startswith("test_"):
                tests.add(path.name)
            continue
        try:
            name = builder.module_name(path)
        except ValueError:
            continue
        touched.add(name)
    # Everything that imports a changed module, and what imports that, and so on: a test that reaches the change only through
    # a third module can still fail because of it.
    queue = list(touched)
    while queue:
        for importer in reverse.get(queue.pop(), ()):
            if importer not in touched:
                touched.add(importer)
                queue.append(importer)
    for name in touched:
        if name in modules:
            tests |= modules[name].tests
    for f in changed:
        for prefix, names in SCRIPT_TESTS.items():
            if f.startswith(prefix):
                tests.update(names)
    return sorted(tests), f"{len(touched)} modules touched, directly or through what imports them"


def run(step: str, command: list[str], cwd: Path, results: list[tuple[str, bool, float]]) -> bool:
    started = time.time()
    print(f"\n== {step}\n   $ {' '.join(command)}", flush=True)
    proc = subprocess.run(command, cwd=cwd, check=False)
    ok = proc.returncode == 0
    results.append((step, ok, time.time() - started))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the checks that matter for what changed.")
    parser.add_argument("--all", action="store_true", help="Run everything CI runs.")
    parser.add_argument("--plan", action="store_true", help="Say what would run and stop.")
    args = parser.parse_args()
    builder = load_builder()
    changed = changed_files()
    backend_changed = args.all or any(f.startswith(("backend/", *SCRIPT_TESTS)) for f in changed)
    desktop_changed = args.all or any(f.startswith("desktop/") for f in changed)
    locale_changed = args.all or any(f.startswith("desktop/src/locales/it.txt") or f.startswith("desktop/src/") for f in changed)
    steps: list[tuple[str, list[str], Path]] = []

    py = [f for f in changed if f.startswith(("backend/", "scripts/")) and f.endswith(".py") and (ROOT / f).exists()]
    if args.all or py:  # the same files and the same settings as CI: the backend's, applied to scripts/ too
        ruff = [str(BACKEND / ".venv" / "Scripts" / "ruff.exe")] if (BACKEND / ".venv" / "Scripts" / "ruff.exe").exists() else [*python_for_tests(), "-m", "ruff"]
        # From backend/, where its settings (and the `src` they name) apply; scripts/ is checked with the same ones.
        paths = ["src", "tests", "../scripts"] if args.all else [os.path.relpath(ROOT / f, BACKEND) for f in py]
        steps.append(("ruff", [*ruff, "check", *paths], BACKEND))
    if backend_changed:
        lint_imports = BACKEND / ".venv" / "Scripts" / "lint-imports.exe"
        if lint_imports.exists():
            steps.append(("import contracts", [str(lint_imports), "--config", "backend/.importlinter"], ROOT))
        tests, why = ([], "all") if args.all else tests_for(changed, builder)
        if why == "all" or tests:
            target = [f"tests/{t}" for t in tests]
            steps.append((f"pytest ({'whole suite' if why == 'all' else f'{len(tests)} files; {why}'})", [*python_for_tests(), "-m", "pytest", "-q", "-p", "no:cacheprovider", *target], BACKEND))
        elif any(f.startswith("backend/src/") for f in changed):
            print("note: no test imports the modules you changed (or what imports them): consider adding one.")
    if desktop_changed:
        npm = shutil.which("npm") or "npm"
        steps.append(("typescript", [npm, "run", "typecheck"], DESKTOP))
        steps.append(("eslint", [npm, "run", "lint"], DESKTOP))
        steps.append(("vitest", [npm, "test"], DESKTOP))
    if locale_changed:
        steps.append(("italian catalog", [sys.executable, "-X", "utf8", "scripts/build_locale.py"], ROOT))

    if args.plan:
        print("changed files:", len(changed))
        for name, command, cwd in steps:
            print(f"- {name}: {' '.join(command)}  (in {cwd.relative_to(ROOT).as_posix() or '.'})")
        return 0

    results: list[tuple[str, bool, float]] = []
    builder_ok = subprocess.run([sys.executable, "-X", "utf8", "scripts/build_context.py", "--quiet"], cwd=ROOT, check=False).returncode == 0
    results.append(("map of the code (rewritten)", builder_ok, 0.0))
    for name, command, cwd in steps:
        run(name, command, cwd, results)
    print("\n== summary")
    for name, ok, seconds in results:
        print(f"   {'ok  ' if ok else 'FAIL'} {name}" + (f" ({seconds:.0f}s)" if seconds else ""))
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print(f"\n{len(failed)} check(s) failed. Fix them before calling the change done.")
        return 1
    print("\nAll checks passed." if steps else "\nNothing to check: no source file changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
