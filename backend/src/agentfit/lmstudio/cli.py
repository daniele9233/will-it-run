"""Wrapper around the official ``lms`` CLI.

Only documented commands are used: ``ls``, ``ps``, ``load``, ``unload``, ``get``,
``server start|stop|status``, ``runtime ls`` and ``version``.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from agentfit.lmstudio.models import ResourceEstimate

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class LmsCliError(Exception):
    pass


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _number(text: str) -> float | None:
    """Parse a number that may use a locale thousands or decimal separator."""
    cleaned = text.strip()
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", cleaned):
        cleaned = re.sub(r"[.,]", "", cleaned)
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_estimate(text: str) -> ResourceEstimate:
    """Parse ``lms load <key> --estimate-only`` output."""
    clean = strip_ansi(text)
    gpu = re.search(r"Estimated GPU Memory:\s*([\d.,]+)\s*GiB", clean)
    total = re.search(r"Estimated Total Memory:\s*([\d.,]+)\s*GiB", clean)
    ctx = re.search(r"Context Length:\s*([\d.,]+)", clean)
    ctx_value = _number(ctx.group(1)) if ctx else None
    return ResourceEstimate(
        gpu_memory_gb=_number(gpu.group(1)) if gpu else None,
        total_memory_gb=_number(total.group(1)) if total else None,
        context_length=int(ctx_value) if ctx_value else None,
        raw=clean.strip(),
    )


def parse_runtime_ls(text: str) -> list[str]:
    """Return the selected engines from ``lms runtime ls``."""
    engines: list[str] = []
    for line in strip_ansi(text).splitlines():
        parts = line.split()
        if len(parts) >= 3 and "@" in parts[0] and ("✓" in parts or "✓" in line):
            engines.append(parts[0])
    return engines


class LmsCli:
    def __init__(self, exe: str | None) -> None:
        self.exe = exe

    @property
    def available(self) -> bool:
        return bool(self.exe)

    def run(self, args: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
        if not self.exe:
            raise LmsCliError("lms CLI is not installed")
        try:
            proc = subprocess.run(
                [self.exe, *args],
                capture_output=True,
                timeout=timeout,
                creationflags=NO_WINDOW,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise LmsCliError(f"lms {' '.join(args)} timed out after {timeout:.0f}s") from exc
        except OSError as exc:
            raise LmsCliError(str(exc)) from exc
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        return proc.returncode, out, err

    def _json(self, args: list[str], timeout: float = 60.0) -> Any:
        code, out, err = self.run(args, timeout)
        if code != 0:
            raise LmsCliError(strip_ansi(err or out).strip() or f"lms exited with code {code}")
        start = min((i for i in (out.find("["), out.find("{")) if i >= 0), default=-1)
        if start < 0:
            raise LmsCliError("lms returned no JSON")
        return json.loads(out[start:])

    def version(self) -> str | None:
        try:
            return str(self._json(["version", "--json"], timeout=15).get("version"))
        except (LmsCliError, json.JSONDecodeError, AttributeError):
            return None

    def server_status(self) -> dict[str, Any]:
        try:
            data = self._json(["server", "status", "--json"], timeout=15)
        except (LmsCliError, json.JSONDecodeError):
            return {"running": False}
        return data if isinstance(data, dict) else {"running": False}

    def server_start(self) -> bool:
        code, _, _ = self.run(["server", "start"], timeout=60)
        return code == 0

    def ls(self) -> list[dict[str, Any]]:
        data = self._json(["ls", "--json"])
        return data if isinstance(data, list) else []

    def ps(self) -> list[dict[str, Any]]:
        data = self._json(["ps", "--json"], timeout=30)
        return data if isinstance(data, list) else []

    def load(
        self,
        model_key: str,
        *,
        context_length: int | None = None,
        gpu: str | None = None,
        identifier: str | None = None,
        ttl_seconds: int | None = None,
        parallel: int | None = None,
        mtp_draft_tokens: int | None = None,
        timeout: float = 600.0,
    ) -> None:
        """``lms load``. ``mtp_draft_tokens`` turns on speculative decoding with the model's own MTP head.

        The probability that keeps a draft going is set to 0 so every draft token is tried: a higher threshold made
        prose slower in the tests (48 against 60 tok/s), since the head's confidence is a poor guide.
        """
        args = ["load", model_key, "-y"]
        if context_length:
            args += ["-c", str(context_length)]
        if gpu:
            args += ["--gpu", gpu]
        if identifier:
            args += ["--identifier", identifier]
        if ttl_seconds:
            args += ["--ttl", str(ttl_seconds)]
        if parallel:
            args += ["--parallel", str(parallel)]
        if mtp_draft_tokens:
            args += ["--speculative-draft-mtp", "--speculative-draft-max-tokens", str(mtp_draft_tokens),
                     "--speculative-draft-min-continue-probability", "0"]
        code, out, err = self.run(args, timeout)
        if code != 0:
            raise LmsCliError(strip_ansi(err or out).strip() or "lms load failed")

    def unload(self, identifier: str | None = None, *, all_models: bool = False) -> None:
        args = ["unload", "--all"] if all_models else ["unload", identifier or ""]
        code, out, err = self.run(args, timeout=120)
        if code != 0:
            raise LmsCliError(strip_ansi(err or out).strip() or "lms unload failed")

    def estimate(self, model_key: str, context_length: int | None = None, gpu: str | None = None) -> ResourceEstimate:
        args = ["load", model_key, "--estimate-only", "-y"]
        if context_length:
            args += ["-c", str(context_length)]
        if gpu:
            args += ["--gpu", gpu]
        _, out, err = self.run(args, timeout=60)
        return parse_estimate(out + "\n" + err)

    def runtime_engines(self) -> list[str]:
        try:
            _, out, _ = self.run(["runtime", "ls"], timeout=30)
        except LmsCliError:
            return []
        return parse_runtime_ls(out)

    def get(self, model: str, *, gguf: bool = True, timeout: float = 6 * 3600) -> None:
        """Blocking download through LM Studio (``lms get <model> -y``)."""
        args = ["get", model, "-y"]
        if gguf:
            args.append("--gguf")
        code, out, err = self.run(args, timeout)
        if code != 0:
            raise LmsCliError(strip_ansi(err or out).strip() or "lms get failed")
