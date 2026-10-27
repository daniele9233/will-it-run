"""A capture folder (``capture.mjs``) turned into a Next.js project: the page, one component for each of its sections.

What is written, and why it is built this way:

* The page's own style sheets, with their rules as the browser held them, are one file in ``public/clone/`` that the layout
  links. They are not run through the framework's CSS pipeline, which rewrites what it does not know; a copy is only a copy
  if the rules reach the browser unchanged.
* Each section the capture cut out is a component with the section's markup as JSX. A section that a phone is sent
  other markup for (another header, another picture) has both, and the page's media query shows one of them.
* Pictures, fonts and the other files the page used are in ``public/clone/`` under the name the capture gave them.

Nothing in this module asks a model anything. A model may *name* the components (``name_components`` takes its answer and
validates it); the page does not depend on the answer.
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any

from agentfit.clone import jsx

ASSET_TOKEN = "__WIR_ASSET__/"
PUBLIC = "clone"
DESKTOP_CLASS = "clone-desktop"
MOBILE_CLASS = "clone-mobile"

# The versions the project is written for. Pinned: a clone that builds today must build in a month.
NEXT_VERSION = "16.2.1"
REACT_VERSION = "19.2.4"

_RESERVED = frozenset(
    ["Fragment", "Image", "Link", "Script", "Head", "Html", "Body", "Main", "Page", "Layout", "Metadata", "React", "Component", "Suspense", "Array", "Object", "String", "Number", "Date", "Map", "Set"]
)


class ReplicaError(RuntimeError):
    pass


# -- names ---------------------------------------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[A-Za-z0-9]+", ascii_text)


def pascal(text: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in _words(text))


def valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Za-z0-9]{2,39}", name)) and name not in _RESERVED


def default_name(section: dict[str, Any]) -> str:
    """A name for a section from what the capture knows of it: its tag, its class (minus the hash a CSS module adds), its heading."""
    tag = str(section.get("tag") or "")
    if tag in ("header", "footer", "nav", "aside"):
        return tag.capitalize()
    klass = re.sub(r"^(?:style|styles|module)_+", "", str(section.get("className") or ""))
    klass = re.sub(r"__[A-Za-z0-9_-]{4,}$", "", klass)
    name = pascal(klass) if len(klass) > 2 else ""
    if not valid_name(name):
        name = pascal(" ".join(_words(str(section.get("heading") or ""))[:3]))
    return name if valid_name(name) else f"Section{section.get('n') or section.get('id') or ''}"


def component_names(sections: list[dict[str, Any]], chosen: dict[int, str] | None = None) -> dict[int, str]:
    """A unique, valid component name for each section: the one a model chose if it is valid, else one made from the section."""
    names: dict[int, str] = {}
    taken: set[str] = set()
    for section in sections:
        number = int(section.get("n") or section.get("id") or 0)
        wanted = (chosen or {}).get(number, "")
        base = wanted if valid_name(wanted) else default_name(section)
        name = base
        if name in taken or name in _RESERVED:
            # The same widget twice: tell them apart by their heading first, by a number if that is not enough.
            candidate = base + pascal(" ".join(_words(str(section.get("heading") or ""))[:2]))
            if candidate != base and valid_name(candidate) and candidate not in taken:
                name = candidate
            else:
                counter = 2
                while f"{base}{counter}" in taken:
                    counter += 1
                name = f"{base}{counter}"
        taken.add(name)
        names[number] = name
    return names


def name_components(answer: str, sections: list[dict[str, Any]]) -> dict[int, str]:
    """The names a model answered with (a JSON object ``{"3": "ProductCarousel"}`` or a list of names in order), validated.

    What cannot be used is left out and the section keeps its own name: a model that answers badly changes nothing.
    """
    text = answer.strip()
    match = re.search(r"\{.*\}|\[.*\]", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    out: dict[int, str] = {}
    if isinstance(data, list):
        for section, name in zip(sections, data, strict=False):
            if isinstance(name, str) and valid_name(pascal(name)):
                out[int(section.get("n") or section.get("id") or 0)] = pascal(name)
    elif isinstance(data, dict):
        known = {int(s.get("n") or s.get("id") or 0) for s in sections}
        for key, name in data.items():
            if str(key).isdigit() and int(key) in known and isinstance(name, str) and valid_name(pascal(name)):
                out[int(key)] = pascal(name)
    return out


# -- project files -------------------------------------------------------------------------------------------------


def _package(slug: str) -> str:
    return json.dumps(
        {
            "name": f"{slug}-clone", "version": "0.1.0", "private": True,
            "scripts": {"dev": "next dev", "build": "next build", "start": "next start", "typecheck": "tsc --noEmit", "check": "npm run typecheck && npm run build"},
            "dependencies": {"next": NEXT_VERSION, "react": REACT_VERSION, "react-dom": REACT_VERSION},
            "devDependencies": {"@types/node": "^24", "@types/react": "^19", "@types/react-dom": "^19", "typescript": "^5"},
        },
        indent=2,
    ) + "\n"


TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts", ".next/dev/types/**/*.ts"],
  "exclude": ["node_modules"]
}
"""

NEXT_CONFIG = """/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  images: { unoptimized: true },
};

export default nextConfig;
"""

# A page with no doctype is drawn in quirks mode (an old table-based site: news.ycombinator.com), where a line of small inline text has
# another height, and Next.js always writes a doctype. So "/" is served from a document without one, with the same sections.
NEXT_CONFIG_QUIRKS = """/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  images: { unoptimized: true },
  // The original has no doctype and the browser draws it in quirks mode; Next.js always writes one, so "/" is served from a
  // document without it (public/clone/page.html, the same sections as the components).
  async rewrites() {
    return { beforeFiles: [{ source: "/", destination: "/clone/page.html" }] };
  },
};

export default nextConfig;
"""

GITIGNORE = "node_modules/\n.next/\nout/\n*.tsbuildinfo\nnext-env.d.ts\n.DS_Store\n"


# A copy keeps every attribute of the original, whatever React's types say of it (a numeric tabIndex written as a string, a web
# component's own attributes, a custom property in a style): the generated files are not type-checked until their owner tidies them.
NO_CHECK = "// @ts-nocheck: generated from the original page, which React's types do not describe. Remove this line once you tidy the file.\n"


def _tag_root(nodes: list[jsx.Element | str], number: int, extra_class: str = "") -> jsx.Element:
    """The one element a section is, marked with its number (the measure step finds the section by it)."""
    elements = [n for n in nodes if isinstance(n, jsx.Element)]
    if len(elements) != 1:
        raise ReplicaError(f"section {number} is not one element")
    root = elements[0]
    root.attrs = [a for a in root.attrs if a[0] != "data-clone-section"]
    root.attrs.append(("data-clone-section", str(number)))
    if extra_class:
        for index, (name, value) in enumerate(root.attrs):
            if name == "class":
                root.attrs[index] = (name, f"{extra_class} {value}".strip())
                break
        else:
            root.attrs.append(("class", extra_class))
    return root


_FIRST_TAG = re.compile(r'<([A-Za-z][^\s/>]*)((?:\s+[^\s=/>]+(?:="[^"]*")?)*)\s*>')


def _mark_html(html_text: str, number: int, extra_class: str = "") -> str:
    """A section's markup with its number (and a class) on its first tag: the same marks the JSX has."""

    def first(match: re.Match[str]) -> str:
        attrs = re.sub(r'\sdata-clone-section="[^"]*"', "", match.group(2))
        if extra_class:
            if re.search(r'\sclass="', attrs):
                attrs = re.sub(r'(\sclass=")', lambda k: k.group(1) + extra_class + " ", attrs, count=1)
            else:
                attrs += f' class="{extra_class}"'
        return f'<{match.group(1)}{attrs} data-clone-section="{number}">'

    return _FIRST_TAG.sub(first, html_text, count=1)


def _attrs_html(attrs: dict[str, str]) -> str:
    return "".join(f' {name}="{str(value).replace("&", "&amp;").replace(chr(34), "&quot;")}"' for name, value in attrs.items()
                   if re.fullmatch(r"[A-Za-z_][\w:.-]*", name) and not (name == "style" and not str(value).strip()))


def _quirks_page(bundle: dict[str, Any], sections: list[dict[str, Any]], icon: str | None) -> str:
    """The page as one document with no doctype: the shell with every section in its place."""
    shell = str(bundle.get("shell") or "")
    for section in sections:
        number = int(section["n"])
        variant = bool(section.get("htmlMobile"))
        body = _mark_html(str(section["html"]), number, DESKTOP_CLASS if variant else "")
        if variant:
            body += _mark_html(str(section["htmlMobile"]), number, MOBILE_CLASS)
        shell = shell.replace(f'<wir-slot n="{number}"/>', body)
    title = str(bundle.get("title") or bundle.get("host") or "").replace("&", "&amp;").replace("<", "&lt;")
    head = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' f"<title>{title}</title>"
            + (f'<link rel="icon" href="/{PUBLIC}/{icon}">' if icon else "")
            + f'<link rel="stylesheet" href="/{PUBLIC}/site.css"><link rel="stylesheet" href="/{PUBLIC}/fixes.css">')
    page = f"<html{_attrs_html(bundle.get('htmlAttrs') or {})}><head>{head}</head><body{_attrs_html(bundle.get('bodyAttrs') or {})}>{shell}</body></html>\n"
    return page.replace(ASSET_TOKEN, f"/{PUBLIC}/")


def _component(name: str, section: dict[str, Any]) -> str:
    number = int(section["n"])
    variant = bool(section.get("htmlMobile"))
    desktop = _tag_root(jsx.parse(str(section["html"]).replace(ASSET_TOKEN, f"/{PUBLIC}/")), number, DESKTOP_CLASS if variant else "")
    depth = 3 if variant else 2
    body = jsx.render([desktop], depth=depth)
    if variant:
        mobile = _tag_root(jsx.parse(str(section["htmlMobile"]).replace(ASSET_TOKEN, f"/{PUBLIC}/")), number, MOBILE_CLASS)
        body = f"    <>\n{body}\n{jsx.render([mobile], depth=depth)}\n    </>"
    label = str(section.get("heading") or section.get("className") or section.get("tag") or "").replace("*/", "").replace("\n", " ")[:70]
    return f"{NO_CHECK}// Section {number}: {label}\nexport default function {name}() {{\n  return (\n{body}\n  );\n}}\n"


def _layout(bundle: dict[str, Any], icon: str | None) -> str:
    fields = [f"  title: {json.dumps(bundle.get('title') or bundle.get('host') or 'Clone', ensure_ascii=False)},"]
    if bundle.get("description"):
        fields.append(f"  description: {json.dumps(bundle['description'], ensure_ascii=False)},")
    if icon:
        fields.append(f'  icons: {{ icon: "/{PUBLIC}/{icon}" }},')
    metadata = "\n".join(fields)
    html_attrs = {k: v for k, v in (bundle.get("htmlAttrs") or {}).items() if not (k == "style" and not v.strip())}
    body_attrs = {k: v for k, v in (bundle.get("bodyAttrs") or {}).items() if not (k == "style" and not v.strip())}
    return (
        NO_CHECK + 'import type { Metadata } from "next";\nimport type { ReactNode } from "react";\n\n'
        f"export const metadata: Metadata = {{\n{metadata}\n}};\n\n"
        "export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {\n"
        "  return (\n"
        f"    <html{jsx.attributes(html_attrs, 'html')}>\n"
        "      <head>\n"
        f'        <link rel="stylesheet" href="/{PUBLIC}/site.css" />\n'
        f'        <link rel="stylesheet" href="/{PUBLIC}/fixes.css" />\n'
        "      </head>\n"
        f"      <body{jsx.attributes(body_attrs, 'body')}>{{children}}</body>\n"
        "    </html>\n"
        "  );\n"
        "}\n"
    )


def _page(shell: str, names: dict[int, str], folder: str) -> str:
    imports = "".join(f'import {name} from "@/components/sites/{folder}/{name}";\n' for _, name in sorted(names.items()))
    shell = shell.replace(ASSET_TOKEN, f"/{PUBLIC}/")
    body = jsx.render(jsx.parse(shell), depth=3, slots=names)
    return f"{NO_CHECK}{imports}\nexport default function Page() {{\n  return (\n    <>\n{body}\n    </>\n  );\n}}\n"


def _readme(bundle: dict[str, Any], accuracy: str) -> str:
    return (
        f"# Clone of {bundle.get('host') or bundle.get('url')}\n\n"
        f"Made by Will It Run from {bundle.get('url')}: the page as a browser rendered it, rebuilt as a Next.js project.\n\n"
        "```\nnpm install\nnpm run dev      # http://localhost:3000\nnpm run build && npm start\n```\n\n"
        "- `src/app/page.tsx`: the page; `src/components/sites/`: one component for each section.\n"
        "- `public/clone/`: the style sheet, pictures and fonts the page used.\n"
        "- `docs/research/`: the capture (screenshots of the original) and the report that measured this clone against it.\n"
        + ("- The original has no doctype and the browser draws it in quirks mode, which Next.js cannot write, so `/` is served from "
           "`public/clone/page.html` (`next.config.mjs` rewrites it). It holds the same sections as the components.\n"
           if bundle.get("compatMode") == "BackCompat" else "")
        + f"\n{accuracy}"
        "The text, pictures and brand of the original belong to its owner. Use this for a site you own or for learning how it is built.\n"
    )


def write_project(capture_dir: Path, project: Path, *, slug: str, names: dict[int, str] | None = None, accuracy: str = "") -> dict[str, Any]:
    """Write the project from a capture. Returns what was written (components, files) for the report."""
    try:
        bundle = json.loads((capture_dir / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReplicaError(f"the capture is not readable: {exc}") from exc
    sections = [s for s in bundle.get("sections") or [] if isinstance(s, dict) and s.get("html")]
    if not sections:
        raise ReplicaError("the capture holds no section of the page")
    chosen = component_names(sections, names)
    folder = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-") or "site"
    written: list[str] = []

    def put(relative: str, text: str) -> None:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        written.append(relative.replace("\\", "/"))

    put("package.json", _package(folder))
    put("tsconfig.json", TSCONFIG)
    quirks = bundle.get("compatMode") == "BackCompat"
    put("next.config.mjs", NEXT_CONFIG_QUIRKS if quirks else NEXT_CONFIG)
    put(".gitignore", GITIGNORE)
    icon = next((i.get("file") for i in bundle.get("icons") or [] if i.get("file")), None)
    put("src/app/layout.tsx", _layout(bundle, icon))
    put("src/app/page.tsx", _page(str(bundle.get("shell") or ""), chosen, folder))
    components: list[dict[str, Any]] = []
    for section in sections:
        number = int(section["n"])
        name = chosen[number]
        relative = f"src/components/sites/{folder}/{name}.tsx"
        text = _component(name, {**section, "n": number})
        put(relative, text)
        components.append({"n": number, "name": name, "file": relative, "bytes": len(text), "variants": bool(section.get("htmlMobile"))})
    css = (capture_dir / "site.css").read_text(encoding="utf-8").replace(ASSET_TOKEN, f"/{PUBLIC}/")
    breakpoint_ = int(bundle.get("breakpoint") or 768)
    if any(c["variants"] for c in components):
        css += (
            f"\n/* a section with its own markup for phones: the page shows one of the two */\n"
            f"@media (max-width: {breakpoint_ - 1}px) {{ .{DESKTOP_CLASS} {{ display: none !important; }} }}\n"
            f"@media (min-width: {breakpoint_}px) {{ .{MOBILE_CLASS} {{ display: none !important; }} }}\n"
        )
    put(f"public/{PUBLIC}/site.css", css)
    if quirks:
        put(f"public/{PUBLIC}/page.html", _quirks_page(bundle, sections, icon))
    # Loaded after site.css: where a model's or a person's fixes go. A second run of the project keeps what is there.
    if not (project / "public" / PUBLIC / "fixes.css").is_file():
        put(f"public/{PUBLIC}/fixes.css", "/* Style fixes for the clone: loaded after site.css. Select a section by [data-clone-section=\"N\"]. */\n")
    copied = 0
    assets = capture_dir / "assets"
    if assets.is_dir():
        (project / "public" / PUBLIC).mkdir(parents=True, exist_ok=True)
        for file in assets.iterdir():
            if file.is_file():
                shutil.copy2(file, project / "public" / PUBLIC / file.name)
                copied += 1
    put("README.md", _readme(bundle, accuracy))
    return {"components": components, "files": written, "assets": copied, "breakpoint": breakpoint_, "customTags": bundle.get("customTags") or [], "quirks": quirks}
