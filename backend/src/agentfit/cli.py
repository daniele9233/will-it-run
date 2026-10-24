"""``agentfit`` command line interface."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from agentfit import branding
from agentfit.catalog import Alignment, SearchFilters, TaskProfile
from agentfit.config import AlignmentPreference, ExperienceMode

app = typer.Typer(help=f"{branding.APP_NAME}: what AI team can this PC run?", no_args_is_help=True, add_completion=False)
model_app = typer.Typer(help="Search, inspect and install models.", no_args_is_help=True)
team_app = typer.Typer(help="Build AI teams.", no_args_is_help=True)
workflow_app = typer.Typer(help="List, validate and run workflows.", no_args_is_help=True)
memory_app = typer.Typer(help="The permanent memory of each project.", no_args_is_help=True)
arena_app = typer.Typer(help="Put models through questions whose answers code can check.", no_args_is_help=True)
app.add_typer(model_app, name="model")
app.add_typer(team_app, name="team")
app.add_typer(workflow_app, name="workflow")
app.add_typer(memory_app, name="memory")
app.add_typer(arena_app, name="arena")

console = Console()

FIT_STYLE = {
    "PERFECT": "bold green", "EXCELLENT": "green", "GOOD": "cyan", "ACCEPTABLE": "yellow", "SLOW": "dark_orange",
    "MEMORY TIGHT": "dark_orange", "NOT RECOMMENDED": "red", "IMPOSSIBLE": "bold red",
}


def _services() -> Any:
    from agentfit.app import get_services

    return get_services()


def _emit(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


def _gb(value: float | None) -> str:
    return f"{value / 1024**3:.1f} GB" if value else "Unknown"


def _fit(text: str) -> str:
    return f"[{FIT_STYLE.get(text, 'white')}]{text}[/]"


def _cards_table(cards: list[Any], title: str) -> Table:
    table = Table(title=title, title_justify="left", header_style="bold")
    for column in ("Score", "Fit", "Model", "Quant", "Size", "VRAM", "Speed", "Type", ""):
        table.add_column(column)
    for card in cards:
        size = _gb(card.download_size_bytes)
        table.add_row(
            f"{card.score.agentfit_score:.1f}",
            _fit(card.fit.category.value),
            card.entry.repo,
            card.quant,
            ("~" if card.size_is_estimate else "") + size,
            f"{card.fit.vram_required_gb:.1f} GB" if card.fit.vram_required_gb else "CPU",
            f"{card.fit.speed.tokens_per_second:.0f} tok/s" + ("" if card.fit.speed.measured else " est."),
            "uncensored" if card.entry.alignment == Alignment.UNCENSORED else "standard",
            "installed" if card.installed else "",
        )
    return table


@app.command()
def hardware(
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
    refresh: bool = typer.Option(True, help="Rescan instead of using the cached profile."),
) -> None:
    """Detect this PC's CPU, RAM, GPUs and storage, and the memory Will It Run will plan with."""
    svc = _services()
    profile = svc.hardware(refresh=refresh)
    budget = svc.budget()
    if json_out:
        _emit({"profile": profile.model_dump(mode="json"), "budget": budget.model_dump(mode="json")})
        return
    console.print(f"[bold]{profile.os.caption}[/] (build {profile.os.build})")
    cpu = profile.cpu
    features = ", ".join(k.upper() for k, v in cpu.features.items() if v) or "unknown"
    console.print(f"[bold]CPU[/]  {cpu.model}  {cpu.physical_cores} cores / {cpu.logical_cores} threads  [{features}]")
    ram = profile.ram
    kind = f"{ram.memory_type or ''} {ram.speed_mhz or ''}".strip()
    console.print(f"[bold]RAM[/]  {ram.total_gb:.1f} GB {kind}  ({ram.available_gb:.1f} GB available now, {budget.ram_usable_gb:.1f} GB recommended usable)")
    if not profile.gpus:
        console.print("[bold]GPU[/]  none detected")
    for gpu in profile.gpus:
        tag = "integrated" if gpu.is_integrated else gpu.backend.value.upper()
        extra = f"  driver {gpu.driver_version}" if gpu.driver_version else ""
        cuda = f"  CUDA {gpu.cuda_version}" if gpu.cuda_version else ""
        console.print(f"[bold]GPU {gpu.index}[/] {gpu.name}  {gpu.vram_total_gb:.1f} GB  [{tag}]{extra}{cuda}  (VRAM confidence {gpu.vram_confidence.value})")
    for line in budget.explanation:
        console.print(f"     [dim]{line}[/]")
    for drive in profile.drives:
        console.print(f"[bold]{drive.letter}[/]   {drive.free_gb:.0f} GB free of {drive.total_gb:.0f} GB  {drive.filesystem or ''} {drive.media_type or ''}")
    for warning in profile.warnings:
        console.print(f"[yellow]! {warning}[/]")


@app.command()
def models(json_out: bool = typer.Option(False, "--json", help="Print JSON.")) -> None:
    """List the models installed in LM Studio with their fit on this PC."""
    svc = _services()
    budget = svc.budget()
    known = svc.catalog.known_entries()
    from agentfit.catalog.service import entry_from_installed

    cards = []
    for model in svc.installed_models():
        if model.type != "llm":
            continue
        entry = known.get((model.repo or "").lower()) or entry_from_installed(model)
        cards.append(svc.catalog.evaluate(entry, budget, installed=model, measured=svc.catalog.measured_tps()))
    if json_out:
        _emit([c.model_dump(mode="json") for c in cards])
        return
    if not cards:
        console.print("No models installed in LM Studio (or LM Studio is not reachable).")
        return
    console.print(_cards_table(cards, "Installed models"))


@app.command()
def recommend(
    specialization: str = typer.Option("coding", "--specialization", "-s", help="What the model is for."),
    uncensored: bool = typer.Option(False, "--uncensored", help="Only uncensored / abliterated models."),
    standard: bool = typer.Option(False, "--standard", help="Only standard-alignment models."),
    limit: int = typer.Option(10, "--limit", "-n"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Recommend the best models for a job on this PC."""
    from agentfit.api.server import _profile_for

    svc = _services()
    alignment = Alignment.UNCENSORED if uncensored else Alignment.STANDARD if standard else None
    profile: TaskProfile = _profile_for(specialization, None)
    cards = svc.catalog.recommend(profile, svc.budget(), svc.installed_models(), limit=limit, alignment=alignment)
    if json_out:
        _emit([c.model_dump(mode="json") for c in cards])
        return
    if not cards:
        console.print("No runnable model found. Connect to the Internet so Will It Run can search Hugging Face.")
        raise typer.Exit(1)
    console.print(_cards_table(cards, f"Recommended for {profile.label}"))
    top = cards[0]
    console.print(f"\n[bold]Why Will It Run recommends {top.entry.name}[/]")
    for line in top.why + top.quant_reasoning:
        console.print(f"  {line}")


@model_app.command("search")
def model_search(
    query: str = typer.Argument(..., help='e.g. "Qwen uncensored 27B" or "best uncensored model for 16GB VRAM"'),
    limit: int = typer.Option(15, "--limit", "-n"),
    offline: bool = typer.Option(False, "--offline", help="Do not query Hugging Face."),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Search models across Hugging Face, the built-in catalog and LM Studio."""
    svc = _services()
    result = svc.catalog.search(query, svc.budget(), SearchFilters(), svc.installed_models(), limit=limit, online=not offline)
    if json_out:
        _emit(result.model_dump(mode="json"))
        return
    for note in result.notes:
        console.print(f"[yellow]{note}[/]")
    if not result.cards:
        console.print("No matching models.")
        raise typer.Exit(1)
    console.print(_cards_table(result.cards, f'Results for "{query}"' + ("" if result.online else " (offline)")))


@model_app.command("info")
def model_info(repo: str, json_out: bool = typer.Option(False, "--json")) -> None:
    """Show a model's metadata and every file's fit on this PC."""
    from agentfit.fit import LoadOptions, VariantSpec, analyze

    svc = _services()
    entry, online = svc.catalog.details(repo)
    if entry is None:
        console.print(f"Model '{repo}' was not found.")
        raise typer.Exit(1)
    budget = svc.budget()
    rows = []
    for file in entry.files:
        if not file.quant:
            continue
        fit = analyze(entry.to_spec(), VariantSpec(quant=file.quant, file_size_bytes=file.size_bytes), budget, LoadOptions(context_length=svc.settings.models.default_context_length))
        rows.append((file, fit))
    if json_out:
        _emit({"entry": entry.model_dump(mode="json"), "files": [{"file": f.model_dump(), "fit": r.model_dump(mode="json")} for f, r in rows]})
        return
    unknown = "Unknown"
    console.print(f"[bold]{entry.name}[/]  ({'online' if online else 'cached'})")
    console.print(f"  Repository    {entry.repo}")
    console.print(f"  Publisher     {entry.publisher}")
    console.print(f"  Base model    {', '.join(entry.base_models) or unknown}")
    console.print(f"  Quantizer     {entry.quantizer or unknown}")
    console.print(f"  License       {entry.license or unknown}")
    console.print(f"  Architecture  {entry.architecture or unknown}")
    console.print(f"  Parameters    {f'{entry.params_b:.1f}B' if entry.params_b else unknown}")
    console.print(f"  Context       {entry.context_length or unknown}")
    console.print(f"  Type          {'Uncensored / Abliterated' if entry.alignment == Alignment.UNCENSORED else 'Standard'}" + (f"  ({'; '.join(entry.alignment_evidence)})" if entry.alignment_evidence else ""))
    console.print(f"  Capabilities  {', '.join(entry.capabilities) or 'text'}")
    console.print(f"  Metadata confidence: {entry.metadata_confidence.value}" + (f"  (unknown: {', '.join(entry.unknown_fields)})" if entry.unknown_fields else ""))
    table = Table(header_style="bold")
    for column in ("Quant", "File", "Size", "VRAM", "RAM", "Speed", "Fit"):
        table.add_column(column)
    for file, fit in rows:
        table.add_row(file.quant or "", file.filename, _gb(file.size_bytes), f"{fit.vram_required_gb:.1f} GB", f"{fit.ram_required_gb:.1f} GB", f"{fit.speed.tokens_per_second:.0f} tok/s", _fit(fit.category.value))
    console.print(table)


@model_app.command("install")
def model_install(
    repo: str = typer.Argument(..., help="Hugging Face GGUF repository, e.g. unsloth/Qwen3-14B-GGUF"),
    quant: str = typer.Option(None, "--quant", "-q", help="Quantization, e.g. Q4_K_M. Default: best fit for this PC."),
    backend: str = typer.Option(None, "--backend", help="lmstudio or direct. Default: from settings."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Download a model after checking that there is enough safe disk space."""
    from agentfit.downloads import DownloadError, DownloadRequest, UnsafeDownload

    svc = _services()
    entry, _ = svc.catalog.details(repo, refresh=True)
    if entry is None or not entry.files:
        console.print(f"Could not read the file list for '{repo}'. An Internet connection is required to install models.")
        raise typer.Exit(1)
    card = svc.catalog.evaluate(entry, svc.budget(), quant=quant)
    file = entry.file_for(card.quant)
    if file is None or not file.size_bytes:
        console.print(f"No file with quantization {card.quant} in {repo}.")
        raise typer.Exit(1)
    companions = [min(entry.mmproj_files, key=lambda f: f.size_bytes or 0)] if entry.mmproj_files else []
    request = DownloadRequest(
        repo=repo, filename=file.filename, quant=file.quant, size_bytes=file.size_bytes, sha256=file.sha256,
        revision=file.revision, backend=backend, companions=companions,
    )
    try:
        plan = svc.downloads.plan(request)
    except DownloadError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[bold]INSTALL MODEL[/]  {entry.name}  {file.quant}  ({_fit(card.fit.category.value)}, ~{card.fit.speed.tokens_per_second:.0f} tok/s)")
    console.print(f"  File                 {file.filename}")
    console.print(f"  Download             {plan.download_gb:.1f} GB")
    console.print(f"  Temporary storage    {plan.temp_gb:.1f} GB")
    console.print(f"  Available on {plan.drive}      {plan.free_gb:.0f} GB  ({plan.safe_available_gb:.0f} GB safely usable)")
    console.print(f"  After installation   {plan.after_install_free_gb:.0f} GB free")
    if not plan.safe:
        console.print(f"\n[bold red]DO NOT DOWNLOAD[/]  {plan.verdict}")
        for option in plan.options:
            console.print(f"  - {option}")
        raise typer.Exit(2)
    console.print(f"\n[green]{plan.verdict}[/]")
    if not yes and not typer.confirm("Download now?"):
        raise typer.Exit(0)
    try:
        record = svc.downloads.enqueue(request)
    except UnsafeDownload as exc:
        console.print(f"[bold red]DO NOT DOWNLOAD[/]  {exc}")
        raise typer.Exit(2) from exc
    with console.status("Downloading...") as status:
        while True:
            record = svc.downloads.get(record.id)
            speed = f"{(record.bytes_per_second or 0) / 1e6:.0f} MB/s" if record.bytes_per_second else ""
            status.update(f"{record.status}  {record.progress * 100:.0f}%  {_gb(record.downloaded_bytes)} / {_gb(record.size_bytes)}  {speed}")
            if record.status in ("completed", "failed", "cancelled", "paused"):
                break
            time.sleep(1.0)
    if record.status != "completed":
        console.print(f"[red]Download {record.status}: {record.error or ''}[/]")
        raise typer.Exit(1)
    verified = svc.downloads.verify_installed(record.id)
    console.print("[green]Download complete.[/] " + ("LM Studio now lists the model." if verified else "Restart LM Studio if the model does not appear."))


@app.command()
def benchmark(
    model: str = typer.Argument(..., help="LM Studio model key (see: willitrun models)."),
    context: int = typer.Option(8192, "--context", "-c"),
    tokens: int = typer.Option(256, "--tokens", help="Tokens to generate."),
    keep_loaded: bool = typer.Option(False, "--keep-loaded"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Benchmark an installed model on this PC and store the result."""
    from agentfit.benchmark import BenchmarkError

    svc = _services()
    try:
        with console.status(f"Benchmarking {model}..."):
            result = svc.benchmarks.run(
                model, context_length=context, max_output_tokens=tokens, keep_loaded=keep_loaded,
                hardware_fingerprint=svc.hardware().fingerprint(),
            )
    except BenchmarkError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    if json_out:
        _emit(result.model_dump())
        return

    def show(label: str, value: Any, unit: str = "") -> None:
        console.print(f"  {label:<18}{f'{value} {unit}'.strip() if value is not None else 'Not measured'}")

    console.print(f"[bold]{model}[/]  ON YOUR PC")
    show("Generation", result.generation_tps, "tok/s")
    show("Prompt", result.prompt_tps, "tok/s (approx.)")
    show("TTFT", result.ttft_ms, "ms")
    show("Load time", result.load_time_s, "s")
    show("VRAM", round(result.vram_used_mb / 1024, 1) if result.vram_used_mb else None, "GB")
    show("RAM", round(result.ram_used_mb / 1024, 1) if result.ram_used_mb else None, "GB")
    show("GPU utilization", result.gpu_util_percent, "%")
    for note in result.notes:
        console.print(f"  [dim]{note}[/]")


@team_app.command("create")
def team_create(
    specialization: list[str] = typer.Option([], "--specialization", "-s", help="Repeat for several."),
    describe: str = typer.Option("", "--describe", "-d", help="Free-text description of the team."),
    uncensored: bool = typer.Option(False, "--uncensored", help="Only uncensored / abliterated models."),
    name: str = typer.Option(None, "--name"),
    save: bool = typer.Option(False, "--save", help="Save the team and its workflow."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Plan an AI team: agents, shared models, memory and disk."""
    from agentfit.teams.builder import TeamError, TeamRequest, plan_to_summary

    svc = _services()
    request = TeamRequest(
        specializations=specialization, description=describe, name=name, mode=ExperienceMode.AUTOMATIC,
        alignment=Alignment.UNCENSORED if uncensored else None,
        preference=AlignmentPreference.UNCENSORED if uncensored else None,
    )
    try:
        plan = svc.team_builder().plan(request)
    except TeamError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    if save and plan.can_build:
        svc.workflows.save(plan.workflow)
        svc.workflows.save_team(plan.id, plan.name, plan.workflow.workflow.id, {"summary": plan_to_summary(plan)})
    if json_out:
        _emit({"summary": plan_to_summary(plan), "plan": plan.model_dump(mode="json", by_alias=True)})
        return
    rp = plan.resource_plan
    console.print(f"[bold]AGENTFIT RECOMMENDATION[/]  {plan.name}")
    console.print(f"  Agents            {len(plan.agents)}")
    console.print(f"  Models            {len(plan.models)}")
    console.print(f"  Download          {rp.download_gb:.1f} GB")
    console.print(f"  Peak VRAM         {rp.peak_vram_gb:.1f} / {rp.usable_vram_gb:.1f} GB  [{rp.status}]")
    console.print(f"  RAM               {rp.ram_gb:.1f} GB")
    console.print(f"  Model switches    {rp.model_switches_per_run} per run")
    console.print(f"  Expected speed    {plan.expected_speed}")
    console.print(f"  Model preference  {plan.model_preference}")
    console.print(f"  Fit               {_fit(plan.fit)}")
    table = Table(header_style="bold", title="Role -> model", title_justify="left")
    for column in ("Role", "Model", "Quant", "Policy"):
        table.add_column(column)
    for assignment in plan.models:
        for agent_id in assignment.agents:
            agent = next(a for a in plan.agents if a.id == agent_id)
            table.add_row(agent.role, assignment.card.entry.name, assignment.card.quant, assignment.loading_policy.value.upper())
    console.print(table)
    for line in plan.explanations:
        console.print(f"  [dim]Why? {line}[/]")
    for note in plan.notes:
        console.print(f"  [yellow]{note}[/]")
    for blocker in plan.blockers:
        console.print(f"  [bold red]BLOCKED: {blocker}[/]")
    if save and plan.can_build:
        console.print(f"\nSaved. Run it with: {branding.CLI_NAME} workflow run {plan.workflow.workflow.id} --task \"...\"")


@workflow_app.command("list")
def workflow_list(json_out: bool = typer.Option(False, "--json")) -> None:
    """List built-in templates and saved workflows."""
    items = _services().workflows.list()
    if json_out:
        _emit([i.model_dump() for i in items])
        return
    table = Table(header_style="bold")
    for column in ("Id", "Name", "Source", "Agents", "Models", "Description"):
        table.add_column(column)
    for item in items:
        table.add_row(item.id, item.name, item.source, str(item.agents), str(item.models), item.description[:70])
    console.print(table)


@workflow_app.command("show")
def workflow_show(workflow_id: str) -> None:
    """Print a workflow as YAML."""
    from agentfit.workflows.dsl import dump_yaml

    definition = _services().workflows.get(workflow_id)
    if definition is None:
        console.print(f"Workflow '{workflow_id}' not found.")
        raise typer.Exit(1)
    sys.stdout.write(dump_yaml(definition))


@workflow_app.command("validate")
def workflow_validate(path: str) -> None:
    """Validate a workflow YAML file."""
    from pathlib import Path

    from agentfit.workflows.dsl import load_yaml
    from agentfit.workflows.validator import validate

    try:
        definition = load_yaml(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Cannot read workflow: {exc}[/]")
        raise typer.Exit(1) from exc
    result = validate(definition)
    for issue in result.issues:
        color = "red" if issue.level == "error" else "yellow"
        console.print(f"[{color}]{issue.level.upper()}[/] {issue.message}")
    console.print("[green]Valid.[/]" if result.ok else "[red]Invalid.[/]")
    raise typer.Exit(0 if result.ok else 1)


@workflow_app.command("run")
def workflow_run(
    workflow_id: str,
    task: str = typer.Option(..., "--task", "-t", help="What the team should do."),
    model: str = typer.Option(None, "--model", "-m", help="Installed model key to use for every unassigned slot."),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="Approve plan gates automatically. Risky tool calls still ask."),
    folder: str = typer.Option(None, "--folder", help="Work in this folder instead of an empty workspace."),
    repo: str = typer.Option(None, "--repo", help="Clone this GitHub repository and work on the copy."),
    branch: str = typer.Option(None, "--branch", help="Branch to clone, with --repo."),
    documents: str = typer.Option(None, "--documents", help="Folder of text documents, for workflows that answer from evidence."),
) -> None:
    """Run a workflow locally and print its activity."""
    from agentfit.teams.builder import resolve_model_keys
    from agentfit.workflows import sources
    from agentfit.workflows.runner import TERMINAL, RunError

    svc = _services()
    definition = svc.workflows.get(workflow_id)
    if definition is None:
        console.print(f"Workflow '{workflow_id}' not found.")
        raise typer.Exit(1)
    installed = svc.installed_models()
    if model:
        if not any(m.model_key == model for m in installed):
            console.print(f"Model '{model}' is not installed. See: {branding.CLI_NAME} models")
            raise typer.Exit(1)
        for slot in definition.models.values():
            if not slot.model_key:
                slot.model_key = model
    missing = resolve_model_keys(definition, installed)
    if missing:
        console.print(f"[red]No installed model for: {', '.join(missing)}.[/] Pass --model <key> or install the team's models.")
        raise typer.Exit(1)
    if not svc.runtime.ensure_server():
        console.print("[red]LM Studio's local server is not running and could not be started.[/]")
        raise typer.Exit(1)
    try:
        place = sources.resolve(
            folder=folder, repo_url=repo, repo_branch=branch, evidence_dir=documents,
            takes_evidence=any(c.kind == "evidence_dossier" for c in definition.checks),
        )
        run = svc.runs.start(
            definition, task, workspace=place.workspace, evidence_dir=place.evidence_dir,
            protect_existing=place.protect_existing, source=place.source,
        )
    except (RunError, sources.SourceError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    seen = 0
    while True:
        record = svc.runs.get(run.id)
        for event in svc.runs.events(run.id, seen):
            seen = event["seq"]
            _print_event(event)
        for approval in record.pending_approvals:
            if approval.kind == "gate" and auto_approve:
                svc.runs.decide(run.id, approval.id, True)
                continue
            console.print(f"\n[bold yellow]APPROVAL NEEDED[/] {approval.prompt}")
            if approval.payload:
                console.print(json.dumps(approval.payload, indent=2, default=str)[:3000])
            approved = typer.confirm("Approve?")
            comment = "" if approved else typer.prompt("Reason (optional)", default="")
            svc.runs.decide(run.id, approval.id, approved, comment)
        if record.status in TERMINAL:
            break
        time.sleep(0.3)
    record = svc.runs.get(run.id)
    for event in svc.runs.events(run.id, seen):
        _print_event(event)
    color = "green" if record.status == "completed" else "red"
    console.print(f"\n[bold {color}]{record.status.upper()}[/] {record.error or ''}")
    if record.result and record.result.get("final"):
        console.print(json.dumps(record.result["final"], indent=2, default=str))
    raise typer.Exit(0 if record.status == "completed" else 1)


def _print_event(event: dict[str, Any]) -> None:
    kind, payload = event["type"], event["payload"]
    if kind == "node.started":
        console.print(f"[bold cyan]{payload.get('role', payload.get('node'))}[/] working  [dim]{payload.get('model', '')}[/]")
    elif kind == "node.completed":
        tps = f"{payload['tokens_per_second']} tok/s" if payload.get("tokens_per_second") else ""
        console.print(f"  [green]done[/] in {payload.get('seconds')}s  {tps}")
    elif kind == "model.switch" and payload.get("phase") == "ready" and not payload.get("already_loaded"):
        unloaded = f" (unloaded {', '.join(payload['unloaded'])})" if payload.get("unloaded") else ""
        console.print(f"  [magenta]model ready[/] {payload.get('to')} in {payload.get('total_seconds')}s{unloaded}")
    elif kind == "tool.call" and payload.get("status") != "running":
        args = json.dumps(payload.get("arguments", {}), default=str)[:120]
        console.print(f"  [blue]{payload.get('label', payload.get('tool'))}[/] {args} -> {payload.get('status')}")
    elif kind == "node.retry":
        console.print(f"  [yellow]retry {payload.get('attempt')}[/]: {payload.get('reason')}")
    elif kind == "node.failed":
        console.print(f"  [red]failed[/]: {payload.get('error')}")
    elif kind == "memory.recalled" and payload.get("items"):
        titles = "; ".join(str(item.get("title")) for item in payload["items"][:4])
        console.print(f"  [dim]project memory: {len(payload['items'])} entries given ({titles})[/]")
    elif kind == "memory.saved":
        count = sum((payload.get("saved") or {}).values()) + int(payload.get("by_agents") or 0)
        console.print(f"[dim]{count} entries written to the memory of {payload.get('name')}[/]")


def _memory_project(svc: Any, name: str) -> Any:
    """A project by its id, its name or the folder it lives in."""
    projects = svc.brain.projects()
    wanted = name.strip().lower()
    match = next((p for p in projects if wanted in (p.id, p.name.lower(), (p.path or "").lower())), None)
    if match is None:
        known = ", ".join(p.name for p in projects) or "none yet"
        console.print(f"No project called '{name}' has a memory. Projects with one: {known}.")
        raise typer.Exit(1)
    return match


@memory_app.command("list")
def memory_list(json_out: bool = typer.Option(False, "--json", help="Print JSON.")) -> None:
    """List the projects that have a memory."""
    projects = _services().brain.projects()
    if json_out:
        _emit([p.model_dump() for p in projects])
        return
    if not projects:
        console.print("No project has a memory yet. A run in a folder (--folder) or a repository (--repo) starts one.")
        return
    table = Table(title="Project memories", title_justify="left", header_style="bold")
    for column in ("Project", "Kind", "Entries", "Decisions", "Errors", "Last written", "Where"):
        table.add_column(column)
    for p in projects:
        table.add_row(p.name, p.kind, str(p.memories), str(p.counts.get("decision", 0)), str(p.counts.get("error", 0)), p.updated_at[:16], p.path or "")
    console.print(table)


@memory_app.command("search")
def memory_search(
    project: str = typer.Argument(..., help="Project name, id or folder (see: memory list)."),
    query: str = typer.Argument(..., help="What you want to know, in your own words."),
    kind: str = typer.Option(None, "--kind", "-k", help="architecture, decision, change, error, solution, note or session."),
    limit: int = typer.Option(8, "--limit", "-n"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Search a project's memory by meaning, by words and through its graph."""
    from agentfit.brain.service import render_entry

    svc = _services()
    found = svc.brain.search(_memory_project(svc, project).id, query, kinds=[kind] if kind else None, limit=limit)
    if json_out:
        _emit(found.model_dump())
        return
    how = f"by meaning ({found.model}), by words and through the graph" if found.semantic else "by words and through the graph (no embedding model answered)"
    console.print(f"[dim]{len(found.hits)} of {found.total} entries, searched {how}.[/]")
    for hit in found.hits:
        console.print("")
        console.print(render_entry(hit.memory, 600, svc.brain.links(hit.memory), hit), markup=False, highlight=False)


# -- the Arena ---------------------------------------------------------------------------------------


def _arena_board(report: dict[str, Any]) -> None:
    """The ranking of a run, and what it was made of."""
    run = report["run"]
    console.print(f"\n[bold]{run['title'] or run['id']}[/]  [dim]suite {run['suite_version']} · seed {run['seed']} · {run['status']}[/]")
    table = Table(header_style="bold")
    for column in ("#", "Model", "Score", "95 %", "Scored", "tok/s", "Wrong", "Not evaluated", "Skipped", "Run errors"):
        table.add_column(column, justify="left" if column == "Model" else "right")
    for m in report["models"]:
        counts, speed = m["summary"]["counts"], m["summary"]["speed"]["tps_mean"]
        score = m["score"] if m["score"] is not None else m["score_all"]
        low_high = m["ci"] or m["ci_all"]
        table.add_row(
            str(m["rank"] or "-"), m["label"], f"{score:.1f}" if score is not None else "-", f"{low_high[0]:.0f}-{low_high[1]:.0f}" if low_high else "",
            f"{m['summary']['questions']['scored']}/{m['summary']['questions']['total']}", f"{speed:.0f}" if speed else "-",
            str(counts["failed"]), str(counts["unscored"]), str(counts["skipped"]), str(counts["error"]),
        )
        if m["status"] == "failed":
            console.print(f"[red]{m['label']}: {m['error']}[/]")
    console.print(table)
    if report["common"]:
        console.print(f"[dim]Score = the mean of the categories every model could be scored on: {', '.join(report['common'])}[/]")


@arena_app.command("run")
def arena_run(
    models: list[str] = typer.Argument(..., help="LM Studio model keys, or cloud:<provider>/<model> (see: willitrun models)."),
    preset: str = typer.Option("standard", "--preset", "-p", help="quick (48 questions), standard (115) or full (192)."),
    category: list[str] = typer.Option(None, "--category", "-c", help="Only these categories (repeat the option). Default: all."),
    seed: int = typer.Option(42, "--seed", help="42 is the reference challenge: runs with the same seed and suite can be compared."),
    temperature: float = typer.Option(0.0, "--temperature"),
    repeats: int = typer.Option(1, "--repeats"),
    max_tokens: int = typer.Option(2048, "--max-tokens"),
    context: int = typer.Option(0, "--context", help="0: chosen by the pack."),
    title: str = typer.Option("", "--title"),
    unload: bool = typer.Option(False, "--unload", help="Agree to unload the models LM Studio already holds."),
    cloud: bool = typer.Option(False, "--cloud", help="Agree to use cloud models (they are paid)."),
    export: str = typer.Option("", "--export", help="Save the report here when it ends (.html, .md, .csv or .json)."),
    json_out: bool = typer.Option(False, "--json", help="Print the report as JSON."),
) -> None:
    """Run a challenge in this terminal. Ctrl+C stops it, and the answers given are kept."""
    import queue as queue_mod

    from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

    from agentfit.arena.export import FORMATS, export_run
    from agentfit.arena.report import build_report
    from agentfit.arena.runner import ArenaError, RunRequest

    svc = _services()
    if export and export.rsplit(".", 1)[-1].lower() not in FORMATS:
        console.print(f"[red]Choose a file ending in one of: {', '.join('.' + f for f in FORMATS)}[/]")
        raise typer.Exit(2)
    try:
        request = RunRequest(models=models, preset=preset, categories=category or None, seed=seed, temperature=temperature, repeats=repeats, max_tokens=max_tokens,
                             context=context or None, title=title, confirm_unload=unload, confirm_cloud=cloud)
        run_id = svc.arena.start(request)
    except ArenaError as exc:
        console.print(f"[red]{exc}[/]")
        held = exc.extra.get("loaded")
        if held:
            console.print(f"[dim]Loaded: {', '.join(i['model_key'] for i in held)}. Run again with --unload to agree to unload them.[/]")
        if exc.extra.get("cloud"):
            console.print("[dim]Run again with --cloud to agree to use them.[/]")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    events = svc.bus.subscribe()
    run = svc.arena.store.get_run(run_id)
    assert run is not None
    labels = {m["model_key"]: m["label"] for m in svc.arena.store.models(run_id)}
    progress = Progress(TextColumn("{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), TimeRemainingColumn(), console=console)
    marks = {"passed": "[green]right[/]", "partial": "[yellow]partly[/]", "failed": "[red]wrong[/]"}
    interrupted = False
    with progress:
        task = progress.add_task("starting", total=run["total"])
        try:
            while svc.arena.busy() == run_id:
                try:
                    event = events.get(timeout=0.5)
                except queue_mod.Empty:
                    continue
                data = event["data"]
                if data.get("run_id") != run_id:
                    continue
                who = labels.get(str(data.get("model") or data.get("to") or ""), str(data.get("model") or data.get("to") or ""))
                if event["topic"] == "arena.progress":
                    progress.update(task, completed=data.get("done", 0), description=f"{who} · {data.get('question')} {marks.get(str(data.get('status')), '')}")
                elif event["topic"] == "arena.model" and data.get("phase") in ("loading", "ready", "unloaded", "failed"):
                    progress.update(task, description=f"{data.get('phase')} {who}")
        except KeyboardInterrupt:
            interrupted = True
            progress.update(task, description="stopping")
            svc.arena.stop(run_id)
            svc.arena.join(run_id, 300)
    svc.bus.unsubscribe(events)
    report = build_report(svc.arena.store, run_id)
    assert report is not None
    if json_out:
        _emit(report)
    else:
        _arena_board(report)
        console.print(f"[dim]Run {run_id}.{' Stopped: resume it from the app.' if interrupted else ''}[/]")
    if export:
        exported = export_run(svc.arena.store, run_id, export.rsplit(".", 1)[-1].lower())
        if exported:
            with open(export, "wb") as handle:
                handle.write(exported[0])
            console.print(f"Report saved to {export}")
    raise typer.Exit(1 if interrupted or report["run"]["status"] == "failed" else 0)


@arena_app.command("list")
def arena_list(json_out: bool = typer.Option(False, "--json")) -> None:
    """The challenges run so far."""
    runs = _services().arena.store.list_runs()
    if json_out:
        _emit([{k: r[k] for k in ("id", "title", "status", "created_at", "suite_version", "seed", "preset", "total", "done")} for r in runs])
        return
    table = Table(header_style="bold")
    for column in ("Id", "Title", "Status", "When", "Suite", "Seed", "Answers"):
        table.add_column(column)
    for r in runs:
        table.add_row(r["id"], r["title"], r["status"], (r["started_at"] or r["created_at"])[:16].replace("T", " "), r["suite_version"], str(r["seed"]), f"{r['done']}/{r['total']}")
    console.print(table)


@arena_app.command("show")
def arena_show(run_id: str, json_out: bool = typer.Option(False, "--json")) -> None:
    """The ranking of a challenge."""
    from agentfit.arena.report import build_report

    report = build_report(_services().arena.store, run_id)
    if report is None:
        console.print("[red]No such challenge.[/]")
        raise typer.Exit(1)
    if json_out:
        _emit(report)
    else:
        _arena_board(report)


@arena_app.command("export")
def arena_export(run_id: str, path: str = typer.Argument(..., help="Where to save it: .html, .md, .csv or .json.")) -> None:
    """Save the report of a challenge."""
    from agentfit.arena.export import FORMATS, export_run

    fmt = path.rsplit(".", 1)[-1].lower()
    if fmt not in FORMATS:
        console.print(f"[red]Choose a file ending in one of: {', '.join('.' + f for f in FORMATS)}[/]")
        raise typer.Exit(2)
    exported = export_run(_services().arena.store, run_id, fmt)
    if exported is None:
        console.print("[red]No such challenge.[/]")
        raise typer.Exit(1)
    with open(path, "wb") as handle:
        handle.write(exported[0])
    console.print(f"Saved {path}")


@app.command(name="doctor")
def doctor_cmd(json_out: bool = typer.Option(False, "--json")) -> None:
    """Check the environment: Windows, hardware, LM Studio, storage and database."""
    from agentfit import doctor as doctor_mod

    checks = doctor_mod.run_doctor(_services())
    overall = doctor_mod.overall(checks)
    if json_out:
        _emit({"overall": overall, "checks": [c.model_dump() for c in checks]})
    else:
        style = {"PASS": "green", "WARNING": "yellow", "FAIL": "red"}
        for check in checks:
            console.print(f"[{style[check.status]}]{check.status:<8}[/] [bold]{check.name:<18}[/] {check.detail}")
            if check.hint and check.status != "PASS":
                console.print(f"{'':27}[dim]{check.hint}[/]")
        console.print(f"\nOverall: [{style[overall]}]{overall}[/]")
    raise typer.Exit(1 if overall == "FAIL" else 0)


def _exit_with_parent(pid: int) -> None:
    """Stop the core when the desktop shell that started it is gone, however it ended.

    The shell stops the core on a normal exit. This covers a crash or a forced kill, so
    a headless core is never left running in the background.
    """
    import threading

    import psutil

    def watch() -> None:
        while psutil.pid_exists(pid):
            time.sleep(2.0)
        os._exit(0)

    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@app.command()
def serve(
    port: int = typer.Option(0, "--port", help="0 picks a free port."),
    token: str = typer.Option("", "--token", help="Bearer token the UI must send."),
) -> None:
    """Run the local API used by the desktop app."""
    import uvicorn

    from agentfit.api.server import create_app

    if token:
        os.environ["AGENTFIT_TOKEN"] = token
    parent = os.environ.get("AGENTFIT_PARENT_PID", "")
    if parent.isdigit():
        _exit_with_parent(int(parent))
    chosen = port or _free_port()
    # The desktop shell reads this line to learn where the core is listening.
    sys.stdout.write(f"AGENTFIT_READY port={chosen}\n")
    sys.stdout.flush()
    uvicorn.run(create_app(), host="127.0.0.1", port=chosen, log_level="warning", access_log=False)


@app.command()
def version() -> None:
    """Print the version."""
    sys.stdout.write(f"{branding.APP_NAME} {branding.APP_VERSION}\n")


if __name__ == "__main__":
    app()
