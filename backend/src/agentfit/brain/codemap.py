"""The project map: how the code is laid out, read from the files themselves.

No model is involved. The folder is walked, Python and JavaScript/TypeScript files are
parsed for what they import, and the result is the part of the Project Brain that cannot
be wrong about what exists: which files there are, which import which, where a program
starts. Other languages are counted and listed, but their imports are not read.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_FILES = 4000
MAX_PARSE_BYTES = 400_000
MAX_SECONDS = 8.0

SKIP_DIRS = frozenset(
    {
        "node_modules", "__pycache__", "venv", "env", "dist", "build", "target", "out", "bin", "obj", "vendor",
        "coverage", "site-packages", "bower_components", "Pods", "DerivedData",
    }
)
LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin",
    ".cs": "C#", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".cc": "C++", ".rb": "Ruby", ".php": "PHP",
    ".swift": "Swift", ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell", ".html": "HTML", ".css": "CSS",
    ".scss": "CSS", ".vue": "Vue", ".svelte": "Svelte", ".md": "Markdown", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML", ".json": "JSON", ".tf": "Terraform",
}
_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_CONFIG = (
    "pyproject.toml", "requirements.txt", "setup.py", "package.json", "tsconfig.json", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Makefile", "README.md", "vite.config.ts",
)
_ENTRY_NAMES = ("main", "__main__", "app", "index", "cli", "server", "manage")

_JS_IMPORT = re.compile(
    r"""(?:import|export)\s+(?:[^'"`;]*?\s+from\s+)?['"]([^'"\n]+)['"]|require\(\s*['"]([^'"\n]+)['"]\s*\)|import\(\s*['"]([^'"\n]+)['"]\s*\)"""
)


@dataclass
class FileInfo:
    path: str
    language: str
    size: int
    entry: bool = False


@dataclass
class CodeMap:
    files: list[FileInfo] = field(default_factory=list)
    # (importing file, imported file), both inside the project.
    imports: list[tuple[str, str]] = field(default_factory=list)
    # True when the walk stopped at ``MAX_FILES`` or ``MAX_SECONDS``.
    truncated: bool = False
    signature: str = ""


def _walk(root: Path, deadline: float) -> tuple[list[tuple[str, int, int]], bool]:
    """(relative path, size, mtime) of every file worth mapping."""
    found: list[tuple[str, int, int]] = []
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not name.startswith(".") and name not in SKIP_DIRS and not name.endswith(".egg-info"):
                        stack.append(Path(entry.path))
                    continue
                suffix = os.path.splitext(name)[1].lower()
                if suffix not in LANGUAGES and name not in _CONFIG:
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            found.append((Path(entry.path).relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns))
            if len(found) >= MAX_FILES or time.monotonic() > deadline:
                return found, True
    return found, False


def signature_of(root: Path) -> str:
    """Changes when a mapped file is added, removed or edited."""
    found, _ = _walk(root, time.monotonic() + MAX_SECONDS)
    digest = hashlib.sha256()
    for path, size, mtime in sorted(found):
        digest.update(f"{path}|{size}|{mtime}\n".encode())
    return digest.hexdigest()[:24]


def _python_modules(paths: list[str]) -> dict[str, str]:
    """Dotted name -> file, for every way a Python file can be imported from inside the project.

    ``backend/src/pkg/db.py`` answers to ``pkg.db``, ``src.pkg.db`` and ``backend.src.pkg.db``:
    which folder is the import root is not known, so each one is offered. The shortest path
    wins a tie, and a bare one-word name is left to ``_resolve_python``.
    """
    index: dict[str, str] = {}
    for path in sorted(paths, key=len):
        parts = list(PurePosixPath(path).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for start in range(len(parts)):
            dotted = ".".join(parts[start:])
            if dotted and "." in dotted:
                index.setdefault(dotted, path)
    return index


def _resolve_python(module: str, level: int, importer: str, modules: dict[str, str], known: set[str]) -> str | None:
    folder = PurePosixPath(importer).parent
    if level:
        base = folder
        for _ in range(level - 1):
            base = base.parent
        target = base.joinpath(*module.split(".")) if module else base
        for candidate in (f"{target.as_posix()}.py", f"{target.as_posix()}/__init__.py"):
            if candidate.lstrip("./") in known:
                return candidate.lstrip("./")
        return None
    if "." in module:
        # "pkg.db.connect" may name a function of pkg/db.py: try the module, then its parents.
        parts = module.split(".")
        while len(parts) > 1:
            found = modules.get(".".join(parts))
            if found:
                return found
            parts.pop()
        module = parts[0]
    # One word: a sibling file or package, or a top-level one.
    for base in (folder, PurePosixPath(".")):
        for candidate in (base / f"{module}.py", base / module / "__init__.py"):
            name = candidate.as_posix().lstrip("./")
            if name in known:
                return name
    return None


def _parse_python(source: str, path: str, modules: dict[str, str], known: set[str]) -> tuple[list[str], bool]:
    """(files this one imports, whether it is run as a program)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [], False
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found = _resolve_python(alias.name, 0, path, modules, known)
                if found:
                    targets.append(found)
        elif isinstance(node, ast.ImportFrom):
            found = _resolve_python(node.module or "", node.level, path, modules, known)
            if found and not found.endswith("__init__.py"):
                targets.append(found)
                continue
            # "from pkg import module": the names may be modules of the package.
            hit = False
            for alias in node.names:
                dotted = f"{node.module}.{alias.name}" if node.module else alias.name
                sub = _resolve_python(dotted, node.level, path, modules, known)
                if sub:
                    targets.append(sub)
                    hit = True
            if found and not hit:
                targets.append(found)
    entry = any(
        isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"
        for n in tree.body
    )
    return targets, entry


def _resolve_js(specifier: str, importer: str, known: set[str]) -> str | None:
    if not specifier.startswith("."):
        return None  # a package, not a file of the project
    base = PurePosixPath(os.path.normpath((PurePosixPath(importer).parent / specifier).as_posix()).replace("\\", "/"))
    stem = base.as_posix()
    if stem.startswith(".."):
        return None
    candidates = [stem, *(stem + ext for ext in _JS), *(f"{stem}/index{ext}" for ext in _JS)]
    if base.suffix in (".js", ".jsx", ".mjs"):
        # TypeScript sources are imported by the name of the file they compile to.
        candidates += [base.with_suffix(ext).as_posix() for ext in (".ts", ".tsx")]
    return next((c for c in candidates if c in known), None)


def scan(root: Path, *, max_seconds: float = MAX_SECONDS) -> CodeMap:
    """Read the project under ``root``. Never raises: an unreadable file is skipped."""
    deadline = time.monotonic() + max_seconds
    found, truncated = _walk(root, deadline)
    known = {path for path, _, _ in found}
    python_files = [p for p in known if p.endswith((".py", ".pyi"))]
    modules = _python_modules(python_files)
    digest = hashlib.sha256()
    result = CodeMap(truncated=truncated)
    edges: set[tuple[str, str]] = set()
    for path, size, mtime in sorted(found):
        digest.update(f"{path}|{size}|{mtime}\n".encode())
        suffix = os.path.splitext(path)[1].lower()
        info = FileInfo(path=path, language=LANGUAGES.get(suffix, "Other"), size=size)
        stem = PurePosixPath(path).stem
        parses = suffix in (".py", *_JS) and size <= MAX_PARSE_BYTES and time.monotonic() < deadline
        if parses:
            try:
                source = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                source = ""
            if suffix == ".py":
                targets, info.entry = _parse_python(source, path, modules, known)
            else:
                targets = []
                for match in _JS_IMPORT.finditer(source):
                    target = _resolve_js(next(g for g in match.groups() if g), path, known)
                    if target:
                        targets.append(target)
            edges.update((path, target) for target in targets if target != path)
        elif suffix in (".py", *_JS):
            result.truncated = result.truncated or time.monotonic() >= deadline
        if stem.lower() in _ENTRY_NAMES and suffix in (".py", *_JS, ".go", ".rs", ".java", ".cs"):
            info.entry = True
        result.files.append(info)
    result.imports = sorted(edges)
    result.signature = digest.hexdigest()[:24]
    return result


def overview(code: CodeMap, name: str, today: str) -> str:
    """The project map in words: short enough to hand to every agent."""
    code_files = [f for f in code.files if f.language not in ("Markdown", "YAML", "TOML", "JSON")]
    if not code.files:
        return f"{name} has no source files yet."
    languages = Counter(f.language for f in code.files)
    lines = [
        f"Read from the files on {today}, not written by a model.",
        f"{len(code.files)} files: " + ", ".join(f"{lang} {count}" for lang, count in languages.most_common(6)) + ".",
    ]
    folders = Counter(f.path.split("/", 1)[0] + "/" for f in code.files if "/" in f.path)
    if folders:
        lines.append("Top folders: " + ", ".join(f"{folder} ({count})" for folder, count in folders.most_common(8)) + ".")
    present = sorted(f.path for f in code.files if "/" not in f.path and PurePosixPath(f.path).name in _CONFIG)
    if present:
        lines.append("At the root: " + ", ".join(present[:10]) + ".")
    imported = Counter(target for _, target in code.imports)
    importers = {source for source, _ in code.imports}
    entries = [f.path for f in code_files if f.entry and (f.path in importers or f.path not in imported)]
    if entries:
        lines.append("Entry points: " + ", ".join(sorted(entries, key=lambda p: (p.count("/"), p))[:8]) + ".")
    if imported:
        hubs = ", ".join(f"{path} (imported by {count})" for path, count in imported.most_common(8) if count > 1)
        if hubs:
            lines.append("Most imported: " + hubs + ".")
    if code.imports:
        lines.append(f"{len(code.imports)} imports between files were read (Python and JavaScript/TypeScript only).")
    if code.truncated:
        lines.append(f"The project is large: the map stops at {len(code.files)} files.")
    return "\n".join(lines)
