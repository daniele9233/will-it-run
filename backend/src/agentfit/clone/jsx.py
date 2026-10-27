"""HTML as ``capture.mjs`` writes it, to JSX.

The capture serializes the page itself (double-quoted attributes, every element closed, ``&`` ``<`` and ``"`` escaped,
the case of SVG names kept), so what is read here is not the HTML of the wild: a tokenizer is enough and no parser
that lowercases names (which would break ``viewBox`` and ``linearGradient``) is needed.

What the conversion has to get right, because React is stricter than a browser: ``class`` is ``className``, ``style`` is an
object, SVG attributes are camelCase, a form control's ``value`` is a ``defaultValue``, text with a brace or an angle
bracket is a string expression, and a space that matters between two inline elements is kept.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field

__all__ = ["Element", "attributes", "parse", "render", "style_object"]

VOID = frozenset(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"])

_TOKEN = re.compile(r'<(/?)([A-Za-z][^\s/>]*)((?:\s+[^\s=/>]+(?:="[^"]*")?)*)\s*(/?)>|([^<]+)')
_ATTR = re.compile(r'([^\s=/>]+)(?:="([^"]*)")?')

# HTML attributes React spells differently.
RENAME = {
    "class": "className", "for": "htmlFor", "tabindex": "tabIndex", "readonly": "readOnly", "maxlength": "maxLength", "minlength": "minLength",
    "colspan": "colSpan", "rowspan": "rowSpan", "autocomplete": "autoComplete", "autoplay": "autoPlay", "crossorigin": "crossOrigin",
    "srcset": "srcSet", "datetime": "dateTime", "enctype": "encType", "novalidate": "noValidate", "allowfullscreen": "allowFullScreen",
    "contenteditable": "contentEditable", "spellcheck": "spellCheck", "charset": "charSet", "http-equiv": "httpEquiv",
    "accept-charset": "acceptCharset", "usemap": "useMap", "frameborder": "frameBorder", "cellpadding": "cellPadding",
    "cellspacing": "cellSpacing", "playsinline": "playsInline", "inputmode": "inputMode", "itemprop": "itemProp", "itemscope": "itemScope",
    "itemtype": "itemType", "itemid": "itemID", "itemref": "itemRef", "referrerpolicy": "referrerPolicy", "hreflang": "hrefLang",
    "srclang": "srcLang", "formaction": "formAction", "formmethod": "formMethod", "formtarget": "formTarget", "formenctype": "formEncType",
    "formnovalidate": "formNoValidate", "nomodule": "noModule", "fetchpriority": "fetchPriority", "controlslist": "controlsList",
    "enterkeyhint": "enterKeyHint", "accesskey": "accessKey", "bgcolor": "bgColor", "marginheight": "marginHeight", "marginwidth": "marginWidth",
    "autocapitalize": "autoCapitalize", "autocorrect": "autoCorrect", "checked": "defaultChecked", "mediagroup": "mediaGroup",
    "disablepictureinpicture": "disablePictureInPicture", "disableremoteplayback": "disableRemotePlayback", "charoff": "charOff",
}
BOOLEAN = frozenset(
    ["disabled", "hidden", "required", "multiple", "readonly", "autoplay", "controls", "loop", "muted", "playsinline", "open", "reversed", "async", "defer", "novalidate", "allowfullscreen", "default", "itemscope", "nomodule", "selected", "checked", "autofocus", "formnovalidate"]
)
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_:.-]*$")
_SAFE_ATTR = re.compile(r"^[\w \-./:#,;()%+@!?\[\]*=~|']*$")
# Text that can stand in JSX as it is: no brace or angle bracket (they open an expression or a tag), no entity, no newline
# (JSX folds it), nothing JSX would trim at either end.
_SAFE_TEXT = re.compile(r"^[^\s{}<>&\\][^{}<>&\\\n\r\t]*$")
_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclass
class Element:
    tag: str
    attrs: list[tuple[str, str]] = field(default_factory=list)
    children: list[Element | str] = field(default_factory=list)


def parse(source: str) -> list[Element | str]:
    """The nodes of a fragment, as a list of elements and strings (text is unescaped)."""
    root = Element("#root")
    stack = [root]
    for match in _TOKEN.finditer(source):
        closing, tag, raw_attrs, self_closing, text = match.groups()
        if text is not None:
            stack[-1].children.append(html.unescape(text))
            continue
        if closing:
            # A stray end tag closes what it names, or nothing.
            for depth in range(len(stack) - 1, 0, -1):
                if stack[depth].tag == tag:
                    del stack[depth:]
                    break
            continue
        element = Element(tag, [(m.group(1), html.unescape(m.group(2)) if m.group(2) is not None else "") for m in _ATTR.finditer(raw_attrs)])
        stack[-1].children.append(element)
        if not (self_closing or tag in VOID):
            stack.append(element)
    return root.children


# -- styles --------------------------------------------------------------------------------------------------------


def _split_declarations(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for char in text:
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _style_key(name: str) -> str:
    name = name.strip()
    if name.startswith("--"):
        return name
    name = name.lower()
    if name.startswith("-ms-"):
        name = name[1:]
    return re.sub(r"-([a-z])", lambda m: m.group(1).upper(), name)


def style_object(css: str) -> str:
    """``color: red; --x: 1px`` as the inside of a JSX style object: ``color: "red", "--x": "1px"``."""
    items: list[str] = []
    for declaration in _split_declarations(css):
        name, _, value = declaration.partition(":")
        if not name.strip() or not value.strip():
            continue
        key = _style_key(name)
        shown = key if _IDENT.match(key) else json.dumps(key)
        items.append(f"{shown}: {json.dumps(value.strip(), ensure_ascii=False)}")
    return ", ".join(items)


# -- attributes ----------------------------------------------------------------------------------------------------


def _camel(name: str) -> str:
    return re.sub(r"[-:]([A-Za-z])", lambda m: m.group(1).upper(), name)


def _attribute(name: str, value: str, tag: str, in_svg: bool) -> str | None:
    """One attribute as JSX, or None when it cannot be one (a framework's ``@click``, a style with nothing in it)."""
    lower = name.lower()
    custom = "-" in tag
    if not _NAME.match(name) or lower in ("key", "ref", "children", "dangerouslysetinnerhtml"):
        return None
    if lower == "style":
        inner = style_object(value)
        return f"style={{{{ {inner} }}}}" if inner else None
    if lower == "class":
        key = "className"
    elif custom and not in_svg:
        key = name  # a web component takes its attributes as they are written
    elif lower.startswith(("data-", "aria-")):
        key = lower
    elif in_svg:
        key = _camel(name) if ("-" in name or ":" in name) else name
    elif lower == "value" and tag in ("input", "select", "textarea"):
        key = "defaultValue"
    else:
        key = RENAME.get(lower, name)
    if lower in BOOLEAN and value in ("", lower):
        return key
    if value and _SAFE_ATTR.match(value):
        return f'{key}="{value}"'
    return f"{key}={{{json.dumps(value, ensure_ascii=False)}}}"


def attributes(attrs: dict[str, str], tag: str) -> str:
    """The attributes of an element as JSX, with a space in front (``<html lang="it">``): for the layout's html and body."""
    out = [a for a in (_attribute(n, v, tag, False) for n, v in attrs.items()) if a]
    return (" " + " ".join(out)) if out else ""


# -- output --------------------------------------------------------------------------------------------------------


def _text(value: str) -> str:
    if _SAFE_TEXT.match(value) and not value.endswith((" ", "\t", "\n")):
        return value
    return "{" + json.dumps(value, ensure_ascii=False) + "}"


def render(nodes: list[Element | str], *, depth: int = 0, slots: dict[int, str] | None = None, in_svg: bool = False) -> str:
    """The JSX of a list of nodes, one per line, indented by ``depth``. ``slots`` maps a ``<wir-slot n="3"/>`` to a component name."""
    lines: list[str] = []
    for node in nodes:
        lines.extend(_node(node, depth, slots or {}, in_svg))
    return "\n".join(lines)


def _node(node: Element | str, depth: int, slots: dict[int, str], in_svg: bool) -> list[str]:
    pad = "  " * depth
    if isinstance(node, str):
        return [pad + _text(node)] if node else []
    if node.tag == "wir-slot":
        number = int(dict(node.attrs).get("n", "0") or 0)
        return [f"{pad}<{slots.get(number, 'Fragment')} />"] if number in slots else []
    inside = in_svg or node.tag == "svg"
    attrs = [a for a in (_attribute(n, v, node.tag, inside and node.tag != "foreignObject") for n, v in node.attrs) if a]
    children = node.children
    # A textarea's text is its value for React.
    if node.tag == "textarea":
        text = "".join(c for c in children if isinstance(c, str))
        children = []
        if text:
            attrs.append(f"defaultValue={{{json.dumps(text, ensure_ascii=False)}}}")
    head = " ".join([node.tag, *attrs]) if attrs else node.tag
    if len(head) > 110 and attrs:
        inner_pad = pad + "  "
        open_tag = f"{pad}<{node.tag}\n" + "\n".join(f"{inner_pad}{a}" for a in attrs) + f"\n{pad}"
        alone = "/>"
    else:
        open_tag = f"{pad}<{head}"
        alone = " />"
    if not children:
        return [open_tag + alone]
    body: list[str] = []
    for child in children:
        body.extend(_node(child, depth + 1, slots, inside and node.tag != "foreignObject"))
    if not body:
        return [open_tag + alone]
    # A lone short text stays on the line of its element: <a href="/">Home</a>.
    if len(body) == 1 and isinstance(children[0], str) and "\n" not in body[0] and len(open_tag) + len(body[0]) + len(node.tag) < 100:
        return [f"{open_tag}>{body[0].strip()}</{node.tag}>"]
    return [open_tag + ">", *body, f"{pad}</{node.tag}>"]
