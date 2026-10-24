"""Running what a model wrote: in a separate process, in a folder of its own, with a time limit and (on Windows) a memory limit.

This is **not a security boundary**. It keeps an honest mistake from doing damage (an endless loop, a delete, a request to the
network) and nothing more; code written to escape it would. The code under test comes from the model the user chose to run.
Everything the process needs is written into the folder first, and the folder is removed afterwards.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_OUTPUT = 200_000
SENTINEL = "@@ARENA-RESULT@@"


@dataclass
class RunResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    seconds: float


def python_exe() -> str:
    # A frozen build has no interpreter to hand out: the PC's own, if it has one.
    if getattr(sys, "frozen", False):
        return shutil.which("python") or shutil.which("py") or ""
    return sys.executable


class _Job:
    """A Windows job object that caps a process's memory and kills everything in it when it is closed."""

    def __init__(self, memory_mb: int) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wintypes = ctypes, wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k = kernel32

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("Basic", BasicLimit), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError("no job object")
        info = ExtendedLimit()
        info.Basic.LimitFlags = 0x00000100 | 0x00002000  # a memory limit per process, and kill everything when the job closes
        info.ProcessMemoryLimit = memory_mb * 1024 * 1024
        if not kernel32.SetInformationJobObject(wintypes.HANDLE(self._handle), 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise OSError("the job object could not be configured")

    def assign(self, proc: subprocess.Popen[bytes]) -> None:
        self._k.AssignProcessToJobObject(self._wintypes.HANDLE(self._handle), self._wintypes.HANDLE(int(proc._handle)))  # type: ignore[attr-defined]

    def close(self) -> None:
        if self._handle:
            self._k.CloseHandle(self._wintypes.HANDLE(self._handle))
            self._handle = None


def _make_job(memory_mb: int) -> _Job | None:
    if os.name != "nt":
        return None
    try:
        return _Job(memory_mb)
    except Exception:
        return None


def run_process(cmd: list[str], cwd: Path, *, timeout: float, env: dict[str, str] | None = None, memory_mb: int = 1024) -> RunResult:
    started = time.perf_counter()
    job = _make_job(memory_mb)
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env if env is not None else _clean_env(), creationflags=NO_WINDOW,
        )
    except OSError as exc:
        if job is not None:
            job.close()
        return RunResult(None, "", f"{type(exc).__name__}: {exc}", False, time.perf_counter() - started)
    if job is not None:
        with contextlib.suppress(Exception):
            job.assign(proc)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        out, err = proc.communicate()
    finally:
        if job is not None:
            job.close()
    return RunResult(
        proc.returncode, out[:MAX_OUTPUT].decode("utf-8", errors="replace"), err[:MAX_OUTPUT].decode("utf-8", errors="replace"),
        timed_out, time.perf_counter() - started,
    )


def _clean_env() -> dict[str, str]:
    keep = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep or k.upper() == "PATH"}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"})
    return env


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = _clean_env()
    env.update(extra or {})
    return env


# -- the programs that run the cases ------------------------------------------------------------------

PY_HARNESS = r'''
import builtins, json, os, sys, threading, traceback
SENTINEL = "@@ARENA-RESULT@@"
_real_open = builtins.open
with _real_open("cases.json", encoding="utf-8") as _f:
    spec = json.load(_f)
with _real_open("solution.py", encoding="utf-8") as _f:
    source = _f.read()

def _blocked(*a, **k):
    raise PermissionError("blocked by the Arena sandbox")

import socket, shutil, subprocess
socket.socket = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
subprocess.Popen = _blocked
os.system = _blocked
os.popen = _blocked
for _name in ("remove", "unlink", "rmdir", "removedirs", "rename", "replace", "truncate", "chmod", "chown", "symlink", "link", "startfile"):
    if hasattr(os, _name):
        setattr(os, _name, _blocked)
shutil.rmtree = _blocked
shutil.move = _blocked
_cwd = os.getcwd()
def _open(file, mode="r", *a, **k):
    if any(c in mode for c in "wax+") and isinstance(file, (str, bytes, os.PathLike)):
        p = os.path.abspath(os.fsdecode(file))
        if not (p == _cwd or p.startswith(_cwd + os.sep)):
            raise PermissionError("blocked by the Arena sandbox")
    return _real_open(file, mode, *a, **k)
builtins.open = _open

def emit(payload):
    sys.stdout.write("\n" + SENTINEL + json.dumps(payload) + "\n")
    sys.stdout.flush()

def norm(value):
    def conv(v):
        if isinstance(v, (set, frozenset)):
            return sorted((conv(x) for x in v), key=lambda x: json.dumps(x, sort_keys=True, default=str))
        if isinstance(v, dict):
            return {str(k): conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [conv(x) for x in v]
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            return repr(v)
        if isinstance(v, (int, float, str, bool)) or v is None:
            return v
        return repr(v)
    return conv(value)

def same(a, b, tol, unordered):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= max(tol, 0.0) if tol else a == b
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        if unordered:
            ka = sorted(json.dumps(x, sort_keys=True) for x in a)
            kb = sorted(json.dumps(x, sort_keys=True) for x in b)
            return ka == kb
        return all(same(x, y, tol, unordered) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], tol, unordered) for k in a)
    return a == b

ns = {"__name__": "solution", "__builtins__": builtins}
try:
    exec(compile(source, "solution.py", "exec"), ns)
except SyntaxError as e:
    emit({"phase": "load", "syntax": True, "error": f"{e.msg} (line {e.lineno})"})
    sys.exit(0)
except BaseException as e:
    emit({"phase": "load", "error": f"{type(e).__name__}: {e}"})
    sys.exit(0)
if spec["entry"] not in ns:
    emit({"phase": "load", "missing": spec["entry"]})
    sys.exit(0)

results = []
for index, case in enumerate(spec["cases"]):
    box = {}
    def target():
        try:
            if "call" in case:
                fn = ns[case["call"]]
                box["got"] = norm(fn(*case.get("args", []), **case.get("kwargs", {})))
            else:
                exec(compile(case["script"], "case", "exec"), dict(ns))
        except BaseException as e:
            box["error"] = f"{type(e).__name__}: {e}"
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(spec.get("case_timeout", 2.0))
    if t.is_alive():
        results.append({"i": index, "ok": False, "timeout": True})
        results.extend({"i": j, "ok": False, "notrun": True} for j in range(index + 1, len(spec["cases"])))
        emit({"phase": "run", "results": results})
        os._exit(0)
    if "error" in box:
        results.append({"i": index, "ok": False, "error": box["error"][:400]})
    elif "call" in case:
        ok = same(box["got"], case["expected"], case.get("tolerance", 0), case.get("unordered", False))
        entry = {"i": index, "ok": ok}
        if not ok:
            entry["got"] = json.dumps(box["got"], ensure_ascii=False)[:300]
        results.append(entry)
    else:
        results.append({"i": index, "ok": True})
emit({"phase": "run", "results": results})
'''

JS_HARNESS = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const SENTINEL = "@@ARENA-RESULT@@";
const spec = JSON.parse(fs.readFileSync("cases.json", "utf8"));
const source = fs.readFileSync("solution.js", "utf8");
const emit = (payload) => { process.stdout.write("\n" + SENTINEL + JSON.stringify(payload) + "\n"); };
const quiet = { log() {}, error() {}, warn() {}, info() {}, debug() {} };
const ctx = vm.createContext({ console: quiet, assert });
const same = (a, b, tol, unordered) => {
  if (typeof a === "boolean" || typeof b === "boolean") return a === b;
  if (typeof a === "number" && typeof b === "number") return tol ? Math.abs(a - b) <= tol : a === b;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    if (unordered) { const k = (x) => JSON.stringify(x); return JSON.stringify(a.map(k).sort()) === JSON.stringify(b.map(k).sort()); }
    return a.every((x, i) => same(x, b[i], tol, unordered));
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
    return JSON.stringify(ka) === JSON.stringify(kb) && ka.every((k) => same(a[k], b[k], tol, unordered));
  }
  return a === b;
};
try { vm.runInContext(source, ctx, { timeout: 3000 }); }
catch (e) { emit({ phase: "load", syntax: e instanceof SyntaxError, error: `${e && e.name}: ${e && e.message}` }); process.exit(0); }
let defined = false;
try { defined = vm.runInContext(`typeof ${spec.entry} !== "undefined"`, ctx); } catch (e) { defined = false; }
if (!defined) { emit({ phase: "load", missing: spec.entry }); process.exit(0); }
const results = [];
spec.cases.forEach((c, i) => {
  try {
    if (c.call) {
      ctx.__fn = vm.runInContext(c.call, ctx); ctx.__args = c.args || [];
      const raw = vm.runInContext("JSON.stringify(__fn(...__args))", ctx, { timeout: 2000 });
      const got = raw === undefined ? null : JSON.parse(raw);
      const ok = same(got, c.expected, c.tolerance || 0, !!c.unordered);
      results.push(ok ? { i, ok } : { i, ok, got: JSON.stringify(got).slice(0, 300) });
    } else {
      vm.runInContext(c.script, ctx, { timeout: 2000 });
      results.push({ i, ok: true });
    }
  } catch (e) {
    const timeout = e && e.code === "ERR_SCRIPT_EXECUTION_TIMEOUT";
    results.push(timeout ? { i, ok: false, timeout: true } : { i, ok: false, error: `${e && e.name}: ${e && e.message}`.slice(0, 400) });
  }
});
emit({ phase: "run", results });
'''


def make_workdir(base: Path | None = None) -> Path:
    import tempfile

    return Path(tempfile.mkdtemp(prefix="arena-", dir=str(base) if base else None))


def remove_workdir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
