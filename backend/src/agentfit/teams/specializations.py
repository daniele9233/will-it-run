"""Specialization catalog: what a team can be built for, and how free text maps onto it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentfit.catalog.scoring import TaskProfile
from agentfit.i18n import tr
from agentfit.teams import roles as R


@dataclass(frozen=True)
class Specialization:
    id: str
    label: str
    group: str
    focus: str
    # Built-in template used when this is the only specialization selected.
    template: str | None
    role_ids: tuple[str, ...]
    # Extra domain specialist added to generated pipelines: (id, title, focus).
    specialist: tuple[str, str, str] | None = None
    keywords: tuple[str, ...] = field(default_factory=tuple)
    # Set when the specialization needs a generator that LM Studio cannot run.
    unsupported_modality: str | None = None


_SOFTWARE = ("tech_lead", "planner", "developer", "test_runner", "code_reviewer")
_INFRA = ("supervisor", "architect", "devops_engineer", "validation_engineer", "documentation_specialist", "technical_reviewer")
_DOCS = ("supervisor", "documentation_specialist", "technical_reviewer")
_WRITING = ("editorial_director", "author", "editor", "fact_checker", "proofreader", "final_reviewer")
_RESEARCH = ("research_lead", "domain_analyst", "critic", "documentation_specialist")
_CREATIVE = ("art_director", "prompt_engineer", "visual_critic")
_COMPUTER = ("cu_planner", "permission_checker", "observer", "decision_agent", "risk_classifier", "executor", "verifier", "reporter")


def _s(*args, **kwargs) -> Specialization:
    return Specialization(*args, **kwargs)


SPECIALIZATIONS: dict[str, Specialization] = {
    s.id: s
    for s in [
        # Software
        _s("coding", "Coding", "Software", "general software engineering", "coding", _SOFTWARE, keywords=("coding", "code", "software", "programming", "developer")),
        _s("frontend", "Frontend", "Software", "frontend engineering: HTML, CSS, TypeScript, React, accessibility and performance", "coding", _SOFTWARE, keywords=("frontend", "front-end", "react", "css", "ui")),
        _s("backend", "Backend", "Software", "backend engineering: APIs, data modelling, concurrency, reliability and security", "coding", _SOFTWARE, keywords=("backend", "back-end", "api", "microservice")),
        _s("threejs", "Three.js / WebGL", "Software", "Three.js, WebGL and GLSL shaders", "threejs", _SOFTWARE, keywords=("three.js", "threejs", "webgl", "shader", "glsl")),
        _s("python", "Python", "Software", "Python: idiomatic code, typing, packaging and pytest", "coding", _SOFTWARE, keywords=("python", "pytest", "django", "fastapi")),
        _s("cpp", "C++", "Software", "modern C++: RAII, value semantics, CMake and undefined-behaviour avoidance", "coding", _SOFTWARE, keywords=("c++", "cpp", "cmake")),
        _s("rust", "Rust", "Software", "Rust: ownership, error handling, traits, cargo and clippy-clean code", "coding", _SOFTWARE, keywords=("rust", "cargo")),
        # Infrastructure
        _s("devops", "DevOps / SRE", "Infrastructure", "DevOps and site reliability engineering", "devops", _INFRA, keywords=("devops", "sre", "ci/cd", "pipeline", "reliability")),
        _s("linux", "Linux", "Infrastructure", "Linux system administration", "devops", _INFRA, ("linux_specialist", "Linux Specialist", "Linux administration: systemd, networking, storage, permissions and troubleshooting"), ("linux", "systemd", "bash", "ubuntu", "debian", "rhel")),
        _s("windows_admin", "Windows Administration", "Infrastructure", "Windows Server and PowerShell administration", "devops", _INFRA, ("windows_specialist", "Windows Specialist", "Windows administration: PowerShell, Active Directory, services and Group Policy"), ("windows", "powershell", "active directory")),
        _s("kubernetes", "Kubernetes", "Infrastructure", "Kubernetes platform engineering", "devops", _INFRA, ("kubernetes_specialist", "Kubernetes Architect", "Kubernetes: workloads, networking, storage, RBAC, upgrades and multi-tenancy"), ("kubernetes", "k8s", "helm", "kubectl")),
        _s("docker", "Docker", "Infrastructure", "containers and Docker", "devops", _INFRA, ("docker_specialist", "Container Specialist", "containers: image builds, layering, runtime security and Compose"), ("docker", "container", "compose")),
        _s("terraform", "Terraform", "Infrastructure", "infrastructure as code with Terraform", "devops", _INFRA, ("terraform_specialist", "Terraform Specialist", "Terraform: module design, state management, providers and safe plans"), ("terraform", "opentofu", "iac")),
        _s("ansible", "Ansible", "Infrastructure", "configuration management with Ansible", "devops", _INFRA, ("ansible_specialist", "Ansible Specialist", "Ansible: idempotent roles, inventories, vaults and check mode"), ("ansible", "playbook")),
        _s("networking", "Networking", "Infrastructure", "network engineering", "devops", _INFRA, ("network_specialist", "Network Specialist", "networking: routing, DNS, load balancing, firewalls and TLS"), ("network", "networking", "dns", "firewall", "load balancer")),
        _s("postgresql", "PostgreSQL", "Infrastructure", "PostgreSQL operations", "devops", _INFRA, ("postgresql_specialist", "PostgreSQL Specialist", "PostgreSQL: configuration, replication, WAL, vacuum, indexing and query tuning"), ("postgresql", "postgres", "patroni", "pgbackrest", "wal")),
        _s("cybersecurity", "Cybersecurity", "Infrastructure", "defensive security engineering", "devops", _INFRA, ("security_reviewer", "Security Reviewer", "defensive security: threat modelling, hardening, least privilege, secrets and auditability"), ("security", "cybersecurity", "hardening", "threat")),
        # Content
        _s("enterprise_docs", "Enterprise Documentation", "Content", "enterprise documentation: runbooks, architecture records and operating procedures", "documentation", _DOCS, keywords=("enterprise documentation", "documentation", "runbook", "docs")),
        _s("technical_docs", "Technical Documentation", "Content", "technical documentation: guides, references and tutorials", "documentation", _DOCS, keywords=("technical documentation", "technical writing", "manual", "guide")),
        _s("creative_writing", "Creative Writing", "Content", "creative writing", "writing", _WRITING, keywords=("creative writing", "fiction", "story", "roleplay")),
        _s("book_writing", "Book Writing", "Content", "long-form book writing", "writing", _WRITING, keywords=("book", "novel", "chapter")),
        _s("editing", "Editing", "Content", "editing and revision of existing text", "writing", _WRITING, keywords=("editing", "edit", "proofread")),
        _s("translation", "Translation", "Content", "translation that preserves meaning, register and terminology", "writing", _WRITING, keywords=("translation", "translate", "localization")),
        # Research
        _s("scientific_research", "Scientific Research", "Research", "scientific research analysis", None, _RESEARCH, keywords=("scientific", "research", "science")),
        _s("biomedical_research", "Biomedical Research", "Research", "biomedical research analysis", None, _RESEARCH, keywords=("biomedical", "medical", "clinical", "biology")),
        _s("legal_analysis", "Legal Document Analysis", "Research", "legal document analysis", None, _RESEARCH, keywords=("legal", "contract", "law")),
        # Creative
        _s("image", "Image", "Creative", "image generation prompting and art direction", None, _CREATIVE, keywords=("image", "illustration", "stable diffusion", "flux"), unsupported_modality="image"),
        _s("photography", "Photography", "Creative", "photographic image prompting: lens, light and composition", None, _CREATIVE, keywords=("photography", "photo"), unsupported_modality="image"),
        _s("video", "Video", "Creative", "video generation prompting and shot design", None, _CREATIVE, keywords=("video", "animation clip"), unsupported_modality="video"),
        # Automation
        _s("computer_use", "Computer Use", "Automation", "operating Windows desktop applications", "computer_use", _COMPUTER, keywords=("computer use", "desktop automation")),
        _s("browser_automation", "Browser Automation", "Automation", "operating a web browser on the Windows desktop", "computer_use", _COMPUTER, keywords=("browser automation", "web automation")),
    ]
}

GROUP_ORDER = ["Software", "Infrastructure", "Content", "Research", "Creative", "Automation"]

# Extra specialists recognised in a custom description: (keywords, id, title, focus, phase).
CUSTOM_SPECIALISTS: list[tuple[tuple[str, ...], str, str, str, str]] = [
    (("patroni",), "patroni_specialist", "Patroni Specialist", "Patroni high availability: DCS, failover, switchover and replication slots", R.BUILD),
    (("pgbackrest", "backup", "recovery", "restore", "wal management", "pitr"), "backup_recovery_specialist", "Backup / Recovery Specialist", "backup and recovery: retention, point-in-time recovery, restore testing and WAL archiving", R.BUILD),
    (("performance", "tuning", "latency", "throughput"), "performance_specialist", "Performance Specialist", "performance: measurement, bottleneck analysis and tuning", R.BUILD),
    (("database architect", "cluster", "clusters", "schema design"), "database_architect", "Database Architect", "database architecture: topology, replication, capacity and data safety", R.PLAN),
    (("observability", "monitoring", "prometheus", "grafana", "alerting"), "observability_specialist", "Observability Specialist", "observability: metrics, logs, traces, SLOs and alert design", R.BUILD),
    (("migration", "upgrade"), "migration_specialist", "Migration Specialist", "migrations and upgrades: sequencing, compatibility, cutover and rollback", R.BUILD),
    (("cost", "finops"), "cost_specialist", "Cost Specialist", "cost: sizing, utilization and cost-aware design", R.BUILD),
    (("compliance", "audit", "gdpr", "iso 27001", "soc 2"), "compliance_specialist", "Compliance Specialist", "compliance: controls, evidence and audit readiness", R.REVIEW),
]

_UNCENSORED_WORDS = re.compile(r"\b(uncensored|abliterated|unfiltered|derestricted|de-restricted|unrestricted)\b", re.IGNORECASE)


def wants_uncensored(text: str) -> bool:
    return bool(_UNCENSORED_WORDS.search(text))


def _mentions(text: str, keyword: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None


def _spans(text: str, keyword: str) -> list[tuple[int, int]]:
    pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
    return [m.span() for m in re.finditer(pattern, text)]


def detect_specializations(text: str) -> list[str]:
    """Specializations mentioned in a free-text description, in catalog order.

    A more specific phrase wins over a generic word inside it: "technical documentation"
    selects Technical Documentation, not also Enterprise Documentation via "documentation".
    """
    lowered = text.lower()
    matches: dict[str, list[tuple[int, int]]] = {
        s.id: [span for k in s.keywords for span in _spans(lowered, k)] for s in SPECIALIZATIONS.values()
    }
    all_spans = [(sid, span) for sid, spans in matches.items() for span in spans]

    def shadowed(sid: str, span: tuple[int, int]) -> bool:
        return any(
            other != sid and o[0] <= span[0] and span[1] <= o[1] and (o[1] - o[0]) > (span[1] - span[0])
            for other, o in all_spans
        )

    return [sid for sid, spans in matches.items() if any(not shadowed(sid, span) for span in spans)]


def detect_custom_specialists(text: str) -> list[tuple[str, str, str, str]]:
    lowered = text.lower()
    return [(sid, title, focus, phase) for keywords, sid, title, focus, phase in CUSTOM_SPECIALISTS if any(_mentions(lowered, k) for k in keywords)]


# What each model slot needs from a model.
SLOT_PROFILES: dict[str, TaskProfile] = {
    R.REASONING: TaskProfile(id="reasoning", label="Reasoning", coding=0.6, reasoning=3.0, writing=1.0, categories=["Reasoning"], min_context=16384),
    R.CODING: TaskProfile(id="coding", label="Coding", coding=3.0, reasoning=1.0, writing=0.4, categories=["Coding"], needs_tools=True, min_context=16384),
    R.WRITING: TaskProfile(id="writing", label="Writing", coding=0.2, reasoning=1.0, writing=3.0, categories=["Writing"], min_context=16384),
    R.VISION: TaskProfile(id="vision", label="Vision", coding=0.5, reasoning=1.5, writing=1.0, categories=["Vision"], needs_vision=True, min_context=8192),
}


def catalog() -> list[dict[str, object]]:
    """Specializations grouped for the team-builder UI."""
    groups: list[dict[str, object]] = []
    for group in GROUP_ORDER:
        items = [
            # Labels are shown to the user; ids and the focus text (which goes to models) stay English.
            {"id": s.id, "label": tr(s.label), "focus": s.focus, "unsupported_modality": s.unsupported_modality}
            for s in SPECIALIZATIONS.values()
            if s.group == group
        ]
        groups.append({"group": tr(group), "items": items})
    return groups
