"""Tiny gettext-style localization: the English text is the key.

``tr("Fits completely on your GPU.")`` returns the text in the active language, or the
English text itself when no translation exists. Values are interpolated with
``str.format``, so a key may carry format specs: ``tr("~{tps:.0f} tok/s", tps=44.2)``.

The active language is, in order: the language of the current HTTP request (the UI
sends it with every call), then the process default taken from the user's settings.
Background work (downloads, workflow runs) has no request and uses the default.

Text sent to models (role prompts, tool descriptions) and log lines stay in English.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from agentfit.locales import CATALOGS

SUPPORTED = ("en", "it")
HEADER = "x-willitrun-lang"

_request: ContextVar[str | None] = ContextVar("agentfit_lang", default=None)
_default = "en"

# Primary language id of Italian in a Windows LANGID.
_LANG_ITALIAN = 0x10


def system_language() -> str:
    """Language of the Windows user interface, limited to the supported set."""
    try:
        import ctypes

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return "en"
    return "it" if (lang_id & 0x3FF) == _LANG_ITALIAN else "en"


def resolve(setting: str | None) -> str:
    """Turn a settings value ("system", "en", "it") into a supported language code."""
    value = (setting or "system").strip().lower()[:2]
    if value in SUPPORTED:
        return value
    return system_language()


def set_default_language(setting: str | None) -> str:
    global _default
    _default = resolve(setting)
    return _default


def set_request_language(lang: str | None) -> Token[str | None] | None:
    value = (lang or "").strip().lower()[:2]
    if value not in SUPPORTED:
        return None
    return _request.set(value)


def reset_request_language(token: Token[str | None] | None) -> None:
    if token is not None:
        _request.reset(token)


def current_language() -> str:
    return _request.get() or _default


def tr(text: str, /, **values: Any) -> str:
    template = CATALOGS.get(current_language(), {}).get(text, text)
    return template.format(**values) if values else template


class LanguageMiddleware:
    """ASGI middleware: make the UI's language the active one for the whole request."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = next((v for k, v in scope.get("headers") or [] if k == HEADER.encode()), b"")
        token = set_request_language(raw.decode("ascii", "ignore"))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_language(token)
