"""The names of categories, radar axes, verdicts and kinds of failure, as the core says them in exported reports.

The screen has its own copy in the interface's catalog; these are the English keys the Italian catalog translates.
"""

from __future__ import annotations

CATEGORY_LABELS: dict[str, str] = {
    "general": "General knowledge", "reading": "Reading comprehension", "math": "Math", "logic": "Logic", "coding": "Coding",
    "sql": "PostgreSQL", "terraform": "Terraform", "ansible": "Ansible", "documents": "Business documents", "pdf": "PDF analysis",
    "json": "Structured JSON", "vision": "Image analysis", "tools": "Tool calling", "impossible": "Impossible questions",
    "instructions": "Following instructions", "injection": "Resisting injection", "longctx": "Long context", "writing": "Writing",
}

AREA_LABELS: dict[str, str] = {
    "knowledge": "Knowledge", "math": "Math", "logic": "Logic", "coding": "Coding", "infra": "Infrastructure", "documents": "Documents",
    "agents": "Agents", "honesty": "Honesty", "vision": "Vision",
}

STATUS_LABELS: dict[str, str] = {
    "passed": "Right", "partial": "Partly right", "failed": "Wrong", "unscored": "Not evaluated", "skipped": "Skipped", "error": "Run error",
}

KIND_LABELS: dict[str, str] = {
    "wrong": "Wrong answer", "format": "Format not followed", "fabricated": "Made it up", "refused": "Refused to answer", "truncated": "Cut off",
    "manual": "Needs a person", "tool": "Tool missing", "tech": "Run error", "capability": "Cannot do it",
}
