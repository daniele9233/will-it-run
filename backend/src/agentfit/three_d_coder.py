"""Local Three.js code generation through the existing model gateway; no tool execution."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from html import unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agentfit import paths
from agentfit.i18n import tr
from agentfit.lmstudio import LMStudioRuntime
from agentfit.lmstudio.models import LoadResult
from agentfit.runtime import RuntimeUnavailable

PROMPT = """Create one complete standalone HTML document for an interactive Three.js 3D scene.
Three.js is already provided as the global THREE object. Use only core Three.js APIs.
Do not import modules, addons or external scripts. No external assets, network calls or tools.
Implement camera controls inside the document. Include responsive canvas sizing and lighting.
Return only the HTML document, without markdown fences or explanatory prose."""
PREVIEW_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
               "img-src data: blob:; connect-src 'none'; object-src 'none'; base-uri 'none'; "
               "form-action 'none'; frame-src 'none'; sandbox allow-scripts")

CONTINUE = """The HTML response was interrupted. The incomplete last line has been removed.
Continue exactly after the supplied assistant prefix, starting with the next missing line.
Return only the missing suffix. Do not repeat the prefix or restart the document.
Finish the actual 3D scene, render loop, camera controls and resizing, then close every script,
body and html element. Do not output markdown or explanations."""


def complete_document(content: str) -> bool:
    """Reject truncated documents before reporting a generation as completed."""
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.open_scripts = 0

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.open_scripts += 1

        def handle_endtag(self, tag):
            if tag == "script":
                self.open_scripts -= 1

    parser = Scripts()
    try:
        parser.feed(content)
    except ValueError:
        return False
    return bool(re.search(r"</body\s*>\s*</html\s*>\s*$", content, re.I) and parser.open_scripts == 0)


def clean_response(content: str) -> str:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    return re.sub(r"^```(?:html)?\s*|\s*```$", "", content).strip()


def continuation_prefix(content: str) -> str:
    return content.rsplit("\n", 1)[0] + "\n" if "\n" in content else content


def syntax_error(content: str) -> str:
    """Parse inline JavaScript without executing it, when local Node is available."""
    node = shutil.which("node")
    if not node:
        return ""
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script\s*>", content, re.I | re.S)
    for attrs, code in scripts:
        if re.search(r"\bsrc\s*=", attrs) or re.search(r'type\s*=\s*[\"\x27](?:application/ld\+json|importmap)', attrs, re.I):
            continue
        module = bool(re.search(r'type\s*=\s*[\"\x27]module', attrs, re.I))
        try:
            result = subprocess.run([node, "--check", "--input-type=" + ("module" if module else "commonjs")],
                input=code, capture_output=True, text=True, encoding="utf-8", timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if result.returncode:
            return result.stderr[:2000]
    return ""


class ThreeDCodeRuntime(LMStudioRuntime):
    """Require the CLI's explicit MTP-off flag, including when a preset enables it."""
    def load(self, model_key, *, context_length=None, gpu=None, ttl_seconds=None):
        if not self.cli.available:
            raise RuntimeUnavailable(tr("The 3D Coder needs the local LM Studio CLI to disable MTP explicitly."))
        args = ["load", model_key, "-y", "--no-speculative-draft-mtp", "--parallel", "1"]
        if context_length:
            args.extend(["-c", str(context_length)])
        if gpu:
            args.extend(["--gpu", gpu])
        if ttl_seconds:
            args.extend(["--ttl", str(ttl_seconds)])
        started = time.monotonic()
        code, out, err = self.cli.run(args, timeout=600)
        if code:
            raise RuntimeUnavailable(err or out)
        instance = next((i for i in self.list_loaded() if i.model_key == model_key), None)
        if instance is None:
            raise RuntimeUnavailable(tr("LM Studio did not report the loaded 3D Coder model."))
        return LoadResult(instance_id=instance.instance_id, model_key=model_key,
                          load_time_seconds=time.monotonic() - started,
                          load_config={"context_length": instance.context_length or context_length})


class ThreeDCodeStudio:
    def __init__(self, installed: Callable, gateway: Callable, start_task: Callable, directory: Path | None = None):
        self.installed = installed
        self.gateway = gateway
        self.start_task = start_task
        self.directory = directory or paths.local_dir() / "3d" / "scenes"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._server: ThreadingHTTPServer | None = None

    def models(self) -> list[dict[str, Any]]:
        choices = []
        for model in self.installed():
            name = f"{model.model_key} {model.display_name}".lower().replace(" ", "-").replace("_", "-")
            if "qwen3.5" in name and "3d-coder" in name:
                choices.append({"model_key": model.model_key, "name": model.display_name, "vision": model.vision})
        return choices

    def generate(self, model_key: str, prompt: str) -> dict[str, Any]:
        if model_key not in {m["model_key"] for m in self.models()}:
            raise ValueError(tr("Choose the installed Qwen 3D Coder model."))
        if not prompt.strip():
            raise ValueError(tr("Describe the 3D scene first."))

        return self._generate(model_key, prompt)

    def repair(self, identifier: str, error: str = "") -> dict[str, Any]:
        content = self.file(identifier).read_text(encoding="utf-8")
        metadata = json.loads((self.directory / f"{identifier}.json").read_text(encoding="utf-8"))
        model_key, prompt = metadata["model"], metadata["prompt"]
        if model_key not in {m["model_key"] for m in self.models()}:
            raise ValueError(tr("Choose the installed Qwen 3D Coder model."))
        return self._generate(model_key, prompt, content, identifier, error or syntax_error(content))

    def _generate(self, model_key: str, prompt: str, partial: str = "", parent_id: str | None = None, error: str = "") -> dict[str, Any]:

        def work(identifier, progress):
            progress({"percent": 5, "phase": "code_loading"})
            manager = self.gateway(model_key)
            try:
                lease = manager.acquire("scene", need_context=65536)
                content = partial
                problem = error
                attempts = []
                for attempt in range(3):
                    progress({"percent": 15 + attempt * 25, "phase": "code_continuing" if content else "code_writing"})
                    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": prompt}]
                    prefix = ""
                    if content:
                        if problem:
                            messages.extend([{"role": "assistant", "content": content}, {"role": "user", "content":
                                "Rewrite the entire HTML document correcting this error. Return only complete HTML. "
                                "Use the existing global THREE, no external scripts. Error:\n" + problem}])
                        else:
                            prefix = continuation_prefix(content)
                            messages.extend([{"role": "assistant", "content": prefix}, {"role": "user", "content": CONTINUE}])
                    # Leave conservative input headroom, including long code prefixes.
                    estimated_input = sum(len(m["content"]) for m in messages) // 2 + 2048
                    budget = min(32768, lease.context_length - estimated_input)
                    if budget < 4096:
                        raise ValueError(tr("The scene is too large to complete in the available context."))
                    response = manager.runtime.complete(lease.instance_id, messages,
                        temperature=0.7, max_tokens=budget,
                        sampling={"top_p": 0.95, "top_k": 20, "repeat_penalty": 1.0})
                    choice = response.get("choices", [{}])[0]
                    answer = clean_response(str(choice.get("message", {}).get("content") or ""))
                    if not answer:
                        raise ValueError(tr("The coder did not return an HTML scene."))
                    content = answer if re.match(r"\s*(?:<!doctype|<html)", answer, re.I) else prefix + answer
                    attempts.append({"finish_reason": choice.get("finish_reason"), "usage": response.get("usage")})
                    problem = syntax_error(content) if complete_document(content) else ""
                    if complete_document(content) and not problem:
                        break
                else:
                    (self.directory / f"{identifier}.partial.html").write_text(content, encoding="utf-8")
                    raise ValueError(tr("The scene is incomplete. The code was saved for recovery; generation was not marked completed."))
                self.file(identifier, must_exist=False).write_text(content, encoding="utf-8")
                (self.directory / f"{identifier}.json").write_text(json.dumps({"id": identifier,
                    "model": model_key, "prompt": prompt, "parent_id": parent_id,
                    "complete": True, "attempts": attempts}), encoding="utf-8")
                progress({"percent": 98, "phase": "code_ready"})
            finally:
                manager.close()

        return self.start_task("code", work)

    def file(self, identifier: str, must_exist: bool = True) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise KeyError(identifier)
        path = self.directory / f"{identifier}.html"
        if must_exist and not path.is_file():
            raise KeyError(identifier)
        return path

    def result(self, identifier: str) -> dict[str, str]:
        return {"id": identifier, "html": self.file(identifier).read_text(encoding="utf-8")}

    def forget(self, identifier: str) -> None:
        """Remove only this scene's owned files; exports elsewhere are untouched."""
        self.file(identifier).unlink()
        for suffix in (".json", ".partial.html"):
            (self.directory / f"{identifier}{suffix}").unlink(missing_ok=True)

    def gallery(self) -> list[dict[str, Any]]:
        results = []
        for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                content = self.file(path.stem).read_text(encoding="utf-8")
                metadata = json.loads(path.read_text(encoding="utf-8"))
                metadata["complete"] = complete_document(content)
                title = re.search(r"<title[^>]*>(.*?)</title>", content, re.I | re.S)
                metadata["title"] = unescape(title[1].strip()) if title else ""
                results.append(metadata)
            except (KeyError, ValueError, OSError):
                continue
        return results

    def standalone(self, identifier: str) -> str:
        runtime = Path(__file__).with_name("data") / "three_runtime.js"
        if not runtime.is_file():
            raise ValueError(tr("The local Three.js runtime is missing. Reinstall the updated app."))
        html = self.file(identifier).read_text(encoding="utf-8")
        if not complete_document(html):
            raise ValueError(tr("This scene is incomplete. Choose Complete scene to recover it."))
        # Some generations ignore the offline contract and insert a CDN script.
        # THREE is already embedded below. Remove only redundant Three.js core
        # loaders, so a blocked duplicate cannot break an otherwise local scene.
        html = re.sub(
            r'<script\b[^>]*\bsrc\s*=\s*([\"\x27])[^\"\x27]*'
            r'/three(?:\.module)?(?:\.min)?\.js(?:\?[^\"\x27]*)?\1[^>]*>\s*</script\s*>',
            "", html, flags=re.I,
        )
        # No API credentials are ever placed in this document or its preview URL.
        script = runtime.read_text(encoding="utf-8").replace("</script", "<\\/script")
        policy = PREVIEW_CSP.split("; sandbox")[0]
        bridge = """(()=>{const send=(status,message='')=>{if(parent!==window)parent.postMessage({type:'wir-scene-preview',status,message},'*')};
window.addEventListener('error',e=>send('error',e.message));
window.addEventListener('unhandledrejection',e=>send('error',String(e.reason?.message||e.reason)));
window.addEventListener('securitypolicyviolation',()=>send('error','A scene resource was blocked. Use the local Three.js runtime only.'));
window.addEventListener('DOMContentLoaded',()=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
if(document.querySelector('canvas'))send('ready');else send('error','The scene did not create a canvas.');})));})();"""
        prefix = '<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + policy + '"><script>' + bridge + script + "</script>"
        index = re.search(r"<head[^>]*>", html, flags=re.I)
        return html[:index.end()] + prefix + html[index.end():] if index else prefix + html

    def preview(self, identifier: str) -> str:
        if not complete_document(self.file(identifier).read_text(encoding="utf-8")):
            raise ValueError(tr("This scene is incomplete. Choose Complete scene to recover it."))
        if self._server is None:
            studio = self

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    key = urlparse(self.path).path.strip("/")
                    try:
                        data = studio.standalone(key).encode("utf-8")
                    except (KeyError, ValueError, OSError):
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Security-Policy", PREVIEW_CSP)
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)

                def log_message(self, *args):
                    pass

            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_port}/{identifier}/"
