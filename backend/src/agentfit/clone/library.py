"""The library of master prompts: one Markdown file for each prompt, checked when the library is loaded.

A prompt file is a header (``id``, a title and a summary in English and in Italian, a ``size``, the libraries it names with the version that was looked
up, the skills it cites) and a body of level-2 blocks (``## Goal``, ``## Stack``, ...). What every prompt says the same way (the gates, the asset
policy, the hard lines, the checks and what to deliver) is written once, in ``shared.md``. ``proofs.json`` records the prompts that were tried with a
real builder: it is data about the library, not a part of it, and ``content_hash`` leaves it out.

``load`` reads the folder and reports every problem it finds at once, one to a line, so that the author of a batch of files fixes them in one go. The
text of a prompt is English because it is sent to a model; none of it is shown to the owner as it is, so none of it goes through ``tr()``.

``assemble`` writes the text of one prompt on its own, the one the library shows and copies: a line of opening, the recipe, then the shared rules, with
every placeholder given a measured value or ``UNKNOWN``. ``render_recipe`` and ``render_shared`` are its two parts, for a text that is built from more
than one recipe.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from agentfit.clone.skills import PACKS

HERE = Path(__file__).resolve().parent
LIBRARY_FOLDER = HERE / "prompt_library"
SHARED_FILE = "shared.md"
PROOFS_FILE = "proofs.json"
# U+FEFF, the byte order mark that some Windows editors put in front of a UTF-8 file.
BYTE_ORDER_MARK = chr(0xFEFF)

# The version of the prompt files: a change to a shipped prompt bumps it. ``content_hash()`` is what tells that the files changed.
LIBRARY_VERSION = "1"

GROUPS = {"A": "pages", "B": "gallery", "C": "motion", "D": "surfaces", "E": "three-d", "F": "process", "G": "after-copy"}
PLACEHOLDERS = ("host", "palette", "fonts", "type_scale", "spacing", "motion", "sections", "libs", "viewport")
RECIPE_MAX_CHARS, SHARED_MAX_CHARS = 6000, 3000

# The blocks of a recipe in the order of the template, and the headings of shared.md.
BLOCK_NAMES = ("Goal", "Stack", "States", "Steps", "Motion", "Hard lines", "Verify")
SHARED_HEADINGS = ("Gates", "Assets", "Hard lines", "Verify", "Deliver")
# Motion is required for these groups and for the hero and navigation prompts; elsewhere it may say "Not applicable".
MOTION_GROUPS = ("motion", "surfaces", "three-d")
MOTION_IDS = ("A01", "A02", "A03", "A04")
REQUIRED_KEYS = ("id", "slug", "group", "title", "title_it", "summary", "summary_it", "size")
OPTIONAL_KEYS = ("tags", "libs", "skills")
SIZES = ("S", "M", "L")
PROOF_KEYS = ("builder", "date", "build_ok", "widths", "dna_match", "notes")
# A skill is cited by the folder name of its pack. The name in the header of its SKILL.md can differ (taste-skill's says design-taste-frontend).
KNOWN_SKILLS = frozenset(name for pack in PACKS for name in pack.skills)
# {{name}}: a bare name and nothing else between the braces. Code such as a JSX style object ({{ opacity }}, {{ opacity: 0 }}) is not a placeholder.
# Whatever fills the placeholders of a prompt must find them with this pattern, so that it and the check on the closed set always agree.
PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
# What a placeholder says when nothing measured fills it: never a default that looks like data, but the builder's own decision, said out loud.
UNKNOWN = "Unknown: decide it and say so"

# The closing line may end the file: a file that is only a header is told what blocks it lacks, not that it has no header.
_FRONT = re.compile(r"^---\n(.*?)\n---(?:\n|$)(.*)$", re.DOTALL)
_ID = re.compile(r"[A-G][0-9]{2}")
_FILE_ID = re.compile(r"([A-G][0-9]{2})-")
# Whatever stands between two pairs of braces on a line, a placeholder or not: {{ palette }}, {{type-scale}}, {{host.name}}, {{ opacity: 0 }}.
_BRACES = re.compile(r"\{\{.*?\}\}")
# What the author does about a double brace that is not a placeholder of the set.
_FIX = "use exactly {{name}} from the closed set, or move the example into a code block"


class LibraryError(ValueError):
    """The library has problems. The message lists every one, one to a line; ``problems`` holds the same lines as a list."""

    def __init__(self, problems: str | list[str]) -> None:
        self.problems = [" ".join(problem.split()) for problem in ([problems] if isinstance(problems, str) else problems)]
        super().__init__("\n".join(self.problems))


@dataclass(frozen=True)
class Prompt:
    """One recipe: the header of its file, and its blocks."""

    id: str
    slug: str
    group: str
    title: str
    title_it: str
    summary: str
    summary_it: str
    tags: tuple[str, ...]
    size: Literal["S", "M", "L"]
    libs: dict[str, str]
    skills: tuple[str, ...]
    # heading -> text, in the order of the file
    blocks: dict[str, str]

    def view(self, lang: str = "en") -> dict[str, Any]:
        """What a list of prompts shows, not the text: the title and the summary in ``lang`` (English unless it is ``it``)."""
        italian = lang[:2].lower() == "it"
        return {
            "id": self.id, "group": self.group, "title": self.title_it if italian else self.title,
            "summary": self.summary_it if italian else self.summary, "tags": list(self.tags), "size": self.size,
            "skills": list(self.skills), "libs": dict(self.libs),
        }


@dataclass(frozen=True)
class Library:
    """Everything a library folder holds, once it has been checked: the recipes, the shared rules and the proofs."""

    prompts: tuple[Prompt, ...]
    # heading -> text of shared.md, in the order of the file
    shared: dict[str, str]
    # prompt id -> the proof that it was tried (keys: PROOF_KEYS)
    proofs: dict[str, dict[str, Any]]

    def get(self, prompt_id: str) -> Prompt | None:
        return next((prompt for prompt in self.prompts if prompt.id == prompt_id), None)


# -- reading ---------------------------------------------------------------------------------------------------------


def _clean(text: str) -> str:
    """The text as the checks and the hash see it: without a byte order mark and with a plain line feed ending every line, whatever saved it."""
    return text.removeprefix(BYTE_ORDER_MARK).replace("\r\n", "\n").replace("\r", "\n")


def _read(path: Path) -> str:
    return _clean(path.read_bytes().decode("utf-8-sig"))


def _markdown(base: Path) -> list[Path]:
    """The Markdown files of a library folder by name: ``shared.md`` and one file for each prompt."""
    return sorted((path for path in base.glob("*.md") if path.is_file()), key=lambda path: path.name)


def _fenced(text: str) -> Iterator[tuple[str, bool]]:
    """The lines of ``text``, each with whether it is in a code fence (the lines that open and close the fence are in it).
    This is the one place that knows what a fence is, so that the blocks and the placeholders always agree on it."""
    fence = ""
    for line in text.split("\n"):
        marker = line.lstrip()[:3]
        if fence:
            if marker == fence:
                fence = ""
            yield line, True
        elif marker in ("```", "~~~"):
            fence = marker
            yield line, True
        else:
            yield line, False


def _blocks(text: str) -> tuple[dict[str, str], list[str]]:
    """The ``## `` blocks of ``text`` by heading in the order of the file, and what is wrong with how they are laid out.
    A ``## `` line inside a code fence belongs to the block it is in (the skeleton of a document written in a fence has some)."""
    before: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line, fenced in _fenced(text):
        if not fenced and line.startswith("## "):
            sections.append((line[3:].strip(), []))
        else:
            (sections[-1][1] if sections else before).append(line)
    problems: list[str] = []
    if "\n".join(before).strip():
        problems.append("text before the first ## heading would be lost: put it in a block")
    blocks: dict[str, str] = {}
    for heading, lines in sections:
        if heading in blocks:
            problems.append(f"the block {heading!r} appears more than once")
            continue
        blocks[heading] = "\n".join(lines).strip()
        if not blocks[heading]:
            problems.append(f"the block {heading!r} is empty")
    return blocks, problems


def _is_placeholder(text: str) -> bool:
    """Whether ``text`` is exactly ``{{name}}`` with a name of the closed set."""
    found = PLACEHOLDER_RE.fullmatch(text)
    return found is not None and found.group(1) in PLACEHOLDERS


def _placeholder_problems(text: str) -> list[str]:
    """What is wrong with the double braces of ``text``, as messages without the name of the file (none when all is well).
    Outside a code fence every ``{{ ... }}`` must be exactly ``{{name}}`` with a name of the closed set: a placeholder written ``{{ palette }}`` or
    ``{{type-scale}}`` is not filled and would reach the builder as it is. Inside a fence only a bare ``{{name}}`` is read as a placeholder, so code
    such as a JSX style object (``style={{ y: 0 }}``) can be written there; a bare name that is not in the set is refused in a fence too.
    Each text is named once, in the order it appears."""
    prose: list[str] = []
    code: list[str] = []
    for line, fenced in _fenced(text):
        if fenced:
            code.extend("{{" + name + "}}" for name in PLACEHOLDER_RE.findall(line) if name not in PLACEHOLDERS)
        else:
            prose.extend(found for found in _BRACES.findall(line) if not _is_placeholder(found))
    the_set = ", ".join(PLACEHOLDERS)
    problems: list[str] = []
    if prose:
        problems.append(f"double braces that are not a placeholder of the closed set: {', '.join(dict.fromkeys(prose))} ({_FIX}; the set is {the_set})")
    if code:
        problems.append(f"placeholders that are not in the set: {', '.join(dict.fromkeys(code))} (the set is {the_set})")
    return problems


def _required(group: str, prompt_id: str) -> tuple[str, ...]:
    """The blocks a prompt of this group must have, in the order of the template."""
    if group == "process":
        needed = {"Goal", "Steps", "Verify"}
    else:
        needed = {"Goal", "Stack", "States", "Verify"}
        if group in MOTION_GROUPS or prompt_id in MOTION_IDS:
            needed.add("Motion")
    return tuple(name for name in BLOCK_NAMES if name in needed)


# -- one prompt file -------------------------------------------------------------------------------------------------


def _yaml_reason(exc: yaml.YAMLError) -> str:
    """Where YAML gave up, as a line of the file (the header starts on its second line)."""
    mark = getattr(exc, "problem_mark", None)
    problem = " ".join(str(getattr(exc, "problem", None) or exc).split())
    return f"line {mark.line + 2}: {problem}" if mark is not None else problem


def _front_matter(raw: str, bad: Callable[[str], None]) -> dict[Any, Any] | None:
    try:
        header = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        bad(f"the front matter is not valid YAML ({_yaml_reason(exc)})")
        return None
    if not isinstance(header, dict):
        bad("the front matter must be lines of key: value")
        return None
    return header


def _identity(header: dict[Any, Any], name: str, bad: Callable[[str], None]) -> dict[str, str]:
    """The required keys that hold text, after saying what is wrong with the keys, the id, the group, the size and the name of the file."""
    fields: dict[str, str] = {}
    absent: list[str] = []
    not_text: list[str] = []
    for key in REQUIRED_KEYS:
        value = header.get(key)
        if key not in header:
            absent.append(key)
        elif isinstance(value, str) and value.strip():
            fields[key] = value.strip()
        else:
            not_text.append(key)
    if absent:
        bad(f"the front matter lacks {', '.join(absent)}")
    if not_text:
        bad(f"{', '.join(not_text)} must be text, and not empty")
    extra = [str(key) for key in header if key not in REQUIRED_KEYS and key not in OPTIONAL_KEYS]
    if extra:
        bad(f"the front matter has keys the template does not: {', '.join(extra)}")
    prompt_id, slug, group, size = (fields.get(key) for key in ("id", "slug", "group", "size"))
    if prompt_id is not None:
        if _ID.fullmatch(prompt_id) is None:
            bad(f"the id {prompt_id!r} must be a letter from A to G and two digits, like C01")
        elif group in GROUPS.values() and GROUPS[prompt_id[0]] != group:
            bad(f"the id {prompt_id} is in the group {GROUPS[prompt_id[0]]!r}, and the file says {group!r}")
    if group is not None and group not in GROUPS.values():
        bad(f"the group {group!r} must be one of {', '.join(GROUPS.values())}")
    if size is not None and size not in SIZES:
        bad(f"size must be S, M or L, not {size!r}")
    if prompt_id and slug and name != f"{prompt_id}-{slug}.md":
        bad(f"the file must be named {prompt_id}-{slug}.md")
    return fields


def _words(header: dict[Any, Any], key: str, bad: Callable[[str], None]) -> tuple[str, ...]:
    """A list of words of the front matter (``tags``, ``skills``); empty when the key is left out."""
    value = header.get(key)
    if value is None:
        return ()
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return tuple(item.strip() for item in value)
    bad(f"{key} must be a list of words, like [one, two]")
    return ()


def _libs(header: dict[Any, Any], bad: Callable[[str], None]) -> dict[str, str]:
    """The libraries a recipe names, each with the version that was looked up: text, never a template or a range."""
    value = header.get("libs")
    if value is None:
        return {}
    if not isinstance(value, dict):
        bad('libs must be pairs of a library and its version, like {lenis: "1.3.4"}')
        return {}
    libs: dict[str, str] = {}
    for lib, version in value.items():
        if not isinstance(lib, str) or not lib.strip():
            bad(f"libs has a name that is not text: {lib!r}")
        elif not isinstance(version, str) or not version.strip():
            bad(f"libs.{lib} must be a version written as text (in quotes), not {version!r}")
        elif "<" in version or ">" in version:
            bad(f"libs.{lib} is {version!r}, a template or a range: write the one version that was looked up")
        else:
            libs[lib.strip()] = version.strip()
    return libs


def _body(body: str, group: str | None, prompt_id: str | None, bad: Callable[[str], None]) -> dict[str, str]:
    """The blocks of a recipe's body, after saying what is wrong with its size, its placeholders and its blocks."""
    if len(body) > RECIPE_MAX_CHARS:
        bad(f"the body has {len(body)} characters, {len(body) - RECIPE_MAX_CHARS} more than the {RECIPE_MAX_CHARS} a recipe may have")
    for message in _placeholder_problems(body):
        bad(message)
    blocks, layout = _blocks(body)
    for message in layout:
        bad(message)
    outside = [heading for heading in blocks if heading not in BLOCK_NAMES]
    if outside:
        bad(f"blocks the template does not have: {', '.join(outside)} (it has {', '.join(BLOCK_NAMES)})")
    if group is not None and group in GROUPS.values():
        missing = [name for name in _required(group, prompt_id or "") if name not in blocks]
        if missing:
            bad(f"missing blocks: {', '.join(missing)}")
    return blocks


def _check(text: str, filename: str) -> Prompt | list[str]:
    """The ``Prompt`` a file holds, or every problem found in it: a list with one message to a problem, each starting with the name of the file."""
    name = Path(filename).name
    problems: list[str] = []

    def bad(message: str) -> None:
        problems.append(f"{name}: {message}")

    parts = _FRONT.match(_clean(text))
    if parts is None:
        bad("it has no front matter: the file starts with a line of ---, then the keys, then a line of ---")
        return problems
    header = _front_matter(parts.group(1), bad)
    fields: dict[str, str] = {}
    tags: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    libs: dict[str, str] = {}
    if header is not None:
        fields = _identity(header, name, bad)
        tags, skills, libs = _words(header, "tags", bad), _words(header, "skills", bad), _libs(header, bad)
        strangers = [skill for skill in skills if skill not in KNOWN_SKILLS]
        if strangers:
            bad(f"skills no pack has: {', '.join(strangers)} (a skill is the folder name of a pack in clone/skills.py)")
    blocks = _body(parts.group(2).strip(), fields.get("group"), fields.get("id"), bad)
    if problems:
        return problems
    return Prompt(
        id=fields["id"], slug=fields["slug"], group=fields["group"], title=fields["title"], title_it=fields["title_it"],
        summary=fields["summary"], summary_it=fields["summary_it"], tags=tags, size=cast(Literal["S", "M", "L"], fields["size"]),
        libs=libs, skills=skills, blocks=blocks,
    )


def parse_prompt(text: str, filename: str) -> Prompt:
    """One prompt file as a ``Prompt``. ``filename`` must be ``<id>-<slug>.md``. A file with problems raises a ``LibraryError`` that lists them all."""
    result = _check(text, filename)
    if isinstance(result, Prompt):
        return result
    raise LibraryError(result)


# -- the folder ------------------------------------------------------------------------------------------------------


def _repeated_ids(names: list[str]) -> list[str]:
    """One message for each id that more than one file starts with."""
    owners: dict[str, list[str]] = {}
    for name in names:
        found = _FILE_ID.match(name)
        if found:
            owners.setdefault(found.group(1), []).append(name)
    return [f"{', '.join(files)}: the id {prompt_id} is used more than once" for prompt_id, files in owners.items() if len(files) > 1]


def _load_shared(base: Path, problems: list[str]) -> dict[str, str]:
    try:
        text = _read(base / SHARED_FILE)
    except FileNotFoundError:
        problems.append(f"{SHARED_FILE}: the file is missing")
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{SHARED_FILE}: it cannot be read as UTF-8 text ({exc})")
        return {}
    shared, layout = _blocks(text)
    problems.extend(f"{SHARED_FILE}: {message}" for message in layout)
    if list(shared) != list(SHARED_HEADINGS):
        wanted, found = ", ".join(SHARED_HEADINGS), ", ".join(shared) or "none"
        problems.append(f"{SHARED_FILE}: the headings must be exactly {wanted}, in this order; it has {found}")
    size = len(text.strip())
    if size > SHARED_MAX_CHARS:
        problems.append(f"{SHARED_FILE}: it has {size} characters, {size - SHARED_MAX_CHARS} more than the {SHARED_MAX_CHARS} it may have")
    problems.extend(f"{SHARED_FILE}: {message}" for message in _placeholder_problems(text))
    return shared


def _load_proofs(base: Path, names: list[str], problems: list[str]) -> dict[str, dict[str, Any]]:
    """The proofs that prompts were tried with a real builder. No file means that none was."""
    path = base / PROOFS_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(_read(path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        problems.append(f"{PROOFS_FILE}: it cannot be read ({exc})")
        return {}
    if not isinstance(data, dict):
        problems.append(f"{PROOFS_FILE}: it must be an object with the id of a prompt for each key")
        return {}
    known = {found.group(1) for name in names if (found := _FILE_ID.match(name))}
    for prompt_id, proof in data.items():
        if prompt_id not in known:
            problems.append(f"{PROOFS_FILE}: {prompt_id!r} is not the id of a prompt")
        if not isinstance(proof, dict):
            problems.append(f"{PROOFS_FILE}: the proof of {prompt_id} must be an object with the keys {', '.join(PROOF_KEYS)}")
            continue
        lacking = [key for key in PROOF_KEYS if key not in proof]
        extra = [key for key in proof if key not in PROOF_KEYS]
        if lacking:
            problems.append(f"{PROOFS_FILE}: the proof of {prompt_id} lacks {', '.join(lacking)}")
        if extra:
            problems.append(f"{PROOFS_FILE}: the proof of {prompt_id} has keys a proof does not: {', '.join(extra)}")
    return data


def load(root: Path | None = None) -> Library:
    """Read and check the library in ``root`` (the ``prompt_library`` folder next to this module by default).
    Every problem of every file is collected and raised at once in one ``LibraryError``."""
    base = Path(root) if root is not None else LIBRARY_FOLDER
    problems: list[str] = []
    prompts: list[Prompt] = []
    names: list[str] = []
    for path in _markdown(base):
        if path.name == SHARED_FILE:
            continue
        names.append(path.name)
        try:
            result = _check(_read(path), path.name)
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path.name}: it cannot be read as UTF-8 text ({exc})")
            continue
        if isinstance(result, Prompt):
            prompts.append(result)
        else:
            problems.extend(result)
    problems.extend(_repeated_ids(names))
    shared = _load_shared(base, problems)
    proofs = _load_proofs(base, names, problems)
    if problems:
        raise LibraryError(problems)
    return Library(tuple(prompts), shared, proofs)


@lru_cache(maxsize=1)
def get_library() -> Library:
    """The library that travels with the program, read once. Code that uses it calls ``library.get_library()`` through the module,
    never ``from ... import get_library``, so that a test can swap it."""
    return load()


def content_hash(root: Path | None = None) -> str:
    """A sha256 over ``shared.md`` and the prompt files, each by name and text. A byte order mark and Windows line ends do not change it, and neither
    does ``proofs.json``: a proof says a prompt was tried, not that it changed."""
    digest = hashlib.sha256()
    for path in _markdown(Path(root) if root is not None else LIBRARY_FOLDER):
        digest.update(path.name.encode("utf-8") + b"\0" + _read(path).encode("utf-8") + b"\0")
    return digest.hexdigest()


# -- the text of a prompt ----------------------------------------------------------------------------------------------
# Nothing here changes the library: it returns new text, and ``get_library()`` hands every caller the same objects.

# The line that opens a prompt on its own. It fits every group, the process recipes (study a reference, check an accessibility pass) as well as the pages.
# The text is English because it is sent to a model.
OPENING = ("Follow the recipe below and the shared rules after it; the gates in the shared rules say in what order to work. "
           "Take the grammar of the reference, never its content.")


def fill(text: str, values: dict[str, str] | None) -> str:
    """``text`` with each ``{{name}}`` replaced by ``values[name]``, or by ``UNKNOWN`` when there is no value or an empty one.
    A value goes in as it is, and one pass is made: what a value holds is never read as a placeholder or as a template.
    Only a bare ``{{name}}`` is a placeholder (``PLACEHOLDER_RE``), so the code of a recipe, ``style={{ y: 0 }}``, is left alone."""
    given = values or {}
    return PLACEHOLDER_RE.sub(lambda found: given.get(found.group(1)) or UNKNOWN, text)


def render_recipe(prompt: Prompt, values: dict[str, str] | None = None) -> str:
    """One recipe as a part of a prompt: ``### C01 Title``, then each block under ``#### Heading`` in the order of the file, placeholders filled.
    The title is the English one: the text goes to a model."""
    parts = [f"### {prompt.id} {prompt.title}"]
    parts.extend(f"#### {heading}\n\n{fill(text, values)}" for heading, text in prompt.blocks.items())
    return "\n\n".join(parts)


def render_shared(shared: dict[str, str], values: dict[str, str] | None = None) -> str:
    """The shared rules as a part of a prompt: ``## Shared rules``, then each heading of ``shared.md`` under ``### Heading``, placeholders filled.
    ``load`` has checked that the headings are Gates, Assets, Hard lines, Verify and Deliver in this order, so the Gates come first."""
    parts = ["## Shared rules"]
    parts.extend(f"### {heading}\n\n{fill(text, values)}" for heading, text in shared.items())
    return "\n\n".join(parts)


def assemble(prompt: Prompt, shared: dict[str, str], values: dict[str, str] | None = None) -> str:
    """The text of ``prompt`` on its own: a line of opening, the recipe, the shared rules. A placeholder that ``values`` does not give says ``UNKNOWN``."""
    return "\n\n".join((OPENING, render_recipe(prompt, values), render_shared(shared, values)))
