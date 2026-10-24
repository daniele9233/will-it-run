"""AI team builder and resource optimizer.

Turns "what should the team be good at" into agents, the smallest sensible set of
models to back them, a loading strategy that fits the VRAM budget, a disk-safety
verdict and a ready-to-run workflow. Agents are not models: many agents share one
model instance and differ by prompt, tools and output schema.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentfit import storage
from agentfit.catalog.models import Alignment
from agentfit.catalog.service import CatalogService, ModelCard
from agentfit.cloud.providers import is_cloud
from agentfit.config import AlignmentPreference, ExperienceMode, Settings
from agentfit.fit.models import CATEGORY_SCORE, FitCategory, Placement
from agentfit.hardware.budget import ResourceBudget
from agentfit.hardware.models import GIB, DriveInfo
from agentfit.lmstudio.models import InstalledModel
from agentfit.runtime import UNSUPPORTED_MODALITY_MESSAGE
from agentfit.teams import roles as R
from agentfit.teams import specializations as S
from agentfit.teams import templates as T
from agentfit.workflows.dsl import LoadingPolicy, WorkflowDef, dump_yaml

# A dedicated specialist model must beat the shared model by this many points to be worth
# its disk space and the model switching it causes.
SPECIALIST_GAIN_THRESHOLD = 2.0
# An installed model within this many points of the best candidate is preferred (no download).
INSTALLED_TOLERANCE = 2.5
MAX_MODELS = 3
MAX_SPEC_SPECIALISTS = 2


class TeamError(Exception):
    pass


class ModelChoice(BaseModel):
    """Explicit model for a slot (Guided / Expert mode)."""

    repo: str | None = None
    quant: str | None = None
    installed_key: str | None = None


class TeamRequest(BaseModel):
    specializations: list[str] = Field(default_factory=list)
    description: str = ""
    name: str | None = None
    mode: ExperienceMode = ExperienceMode.GUIDED
    # Hard filter: only models with this alignment. None = no filter.
    alignment: Alignment | None = None
    # Soft preference used for ranking. None = use the setting.
    preference: AlignmentPreference | None = None
    overrides: dict[str, ModelChoice] = Field(default_factory=dict)
    # Model chosen by hand for one agent (agent id -> model). That agent gets a slot of its
    # own, so the choice touches no other role.
    agent_overrides: dict[str, ModelChoice] = Field(default_factory=dict)
    max_models: int = MAX_MODELS
    # "auto": specialist models only when they clearly beat one shared model.
    # "single": one model for every role. "specialists": the best model for each role,
    # swapped by the workflow's model gateway.
    model_strategy: str = "auto"
    prefer_installed: bool = True


class AgentSummary(BaseModel):
    id: str
    role: str
    goal: str
    slot: str
    tools: list[str] = Field(default_factory=list)
    output_schema: str = ""


class ModelAssignment(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    slots: list[str]
    label: str
    card: ModelCard
    agents: list[str]
    loading_policy: LoadingPolicy
    vram_gb: float
    ram_gb: float
    needs_download: bool
    download_gb: float
    alternatives: list[ModelCard] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)


class RoleModel(BaseModel):
    """One agent and the model that serves it: the plan seen role by role."""

    model_config = ConfigDict(protected_namespaces=())

    agent: str
    role: str
    goal: str
    # What this role needs from a model: reasoning, coding, writing or vision.
    kind: str
    kind_label: str
    slot: str
    repo: str
    quant: str
    model: str
    installed: bool
    installed_key: str | None = None
    # How well this model does this kind of work on this PC, 0 to 10.
    score: float
    vram_gb: float
    tokens_per_second: float
    # True when the user picked this model for this role.
    manual: bool = False
    # True when running this step changes the model in memory.
    loads_model: bool = False


class ModelOption(BaseModel):
    """A model the user can give to a role, scored for that kind of work."""

    model_config = ConfigDict(protected_namespaces=())

    repo: str
    quant: str
    installed_key: str | None = None
    name: str
    score: float
    fit: str
    runnable: bool
    vram_gb: float
    tokens_per_second: float
    installed: bool
    download_gb: float


class TimelineSegment(BaseModel):
    agent: str
    role: str
    model: str
    slot: str
    vram_gb: float
    start: int
    end: int
    shared: bool = False
    loaded_models: list[str] = Field(default_factory=list)
    loaded_vram_gb: float = 0.0
    switch: bool = False


class Optimization(BaseModel):
    needed: bool = False
    before_models_loaded: int = 0
    before_peak_gb: float = 0.0
    after_persistent: int = 0
    after_on_demand: int = 0
    after_peak_gb: float = 0.0
    message: str = ""


class ResourcePlan(BaseModel):
    usable_vram_gb: float
    peak_vram_gb: float
    ram_gb: float
    download_gb: float
    disk_gb: float
    disk: storage.DiskPlan | None = None
    model_switches_per_run: int = 0
    timeline: list[TimelineSegment] = Field(default_factory=list)
    status: str = "SAFE"
    optimization: Optimization = Field(default_factory=Optimization)


class TeamPlan(BaseModel):
    id: str
    name: str
    request: TeamRequest
    specializations: list[str]
    agents: list[AgentSummary]
    models: list[ModelAssignment]
    # Which model each agent uses, in the order the agents run.
    roles: list[RoleModel] = Field(default_factory=list)
    # Models that can be given to a role, by the kind of work the role does.
    choices: dict[str, list[ModelOption]] = Field(default_factory=dict)
    resource_plan: ResourcePlan
    workflow: WorkflowDef
    workflow_yaml: str
    fit: str
    expected_speed: str
    model_preference: str
    can_build: bool
    notes: list[str] = Field(default_factory=list)
    explanations: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


# -- roles --------------------------------------------------------------------

_IMPLEMENT_WORDS = re.compile(r"\b(implement|deploy|build|automate|provision|write code|scripts?)\b", re.IGNORECASE)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "team"


def select_roles(spec_ids: list[str], description: str) -> tuple[list[R.RoleTemplate], str, list[str]]:
    """Choose the roles for a multi-specialization or custom team.

    Returns (roles, focus text, notes).
    """
    specs = [S.SPECIALIZATIONS[s] for s in spec_ids if s in S.SPECIALIZATIONS]
    notes: list[str] = []
    custom = S.detect_custom_specialists(description)
    groups = {s.group for s in specs}
    has_software = "Software" in groups
    has_infra = "Infrastructure" in groups
    has_docs = any(s.template == "documentation" for s in specs)
    has_writing = any(s.template == "writing" for s in specs)
    has_research = "Research" in groups
    has_creative = "Creative" in groups
    has_computer = "Automation" in groups
    advisory = len(custom) >= 3 and not _IMPLEMENT_WORDS.search(description)

    picked: list[R.RoleTemplate] = [R.ROLES["supervisor"]]
    custom_plan = [c for c in custom if c[3] == R.PLAN]
    if has_infra and not custom_plan:
        picked.append(R.ROLES["architect"])
    elif has_software and not has_infra:
        picked.append(R.ROLES["planner"])
    for sid, title, focus, phase in custom_plan:
        picked.append(R.specialist(sid, title, focus, phase=phase))

    spec_specialists = [s.specialist for s in specs if s.specialist]
    if len(spec_specialists) <= MAX_SPEC_SPECIALISTS:
        for sid, title, focus in spec_specialists:
            picked.append(R.specialist(sid, title, focus, phase=R.PLAN if not advisory else R.BUILD))
    elif spec_specialists:
        notes.append(
            f"{len(spec_specialists)} technology areas were folded into the team's shared expertise instead of "
            "adding one specialist agent each, which keeps the run short."
        )
    for sid, title, focus, phase in custom:
        if phase != R.PLAN:
            picked.append(R.specialist(sid, title, focus, phase=phase))

    if has_software:
        picked += [R.ROLES["developer"], R.ROLES["test_runner"], R.ROLES["code_reviewer"]]
    if has_infra and not advisory:
        picked += [R.ROLES["devops_engineer"], R.ROLES["validation_engineer"]]
    if has_research:
        picked += [R.ROLES["domain_analyst"], R.ROLES["critic"]]
    if has_creative:
        picked += [R.ROLES["prompt_engineer"], R.ROLES["visual_critic"]]
    if has_writing:
        picked += [R.ROLES["author"], R.ROLES["editor"]]
    if has_computer:
        notes.append("Computer-use steps run best as their own team; use the Computer Use template for desktop automation.")
    wants_docs = has_docs or has_infra or has_research or bool(re.search(r"\bdocument", description, re.IGNORECASE))
    if wants_docs:
        picked += [R.ROLES["documentation_specialist"], R.ROLES["technical_reviewer"]]
    elif len(picked) > 1 and not has_software:
        picked.append(R.ROLES["technical_reviewer"])

    focus_parts = [s.focus for s in specs]
    if description.strip():
        focus_parts.append(description.strip().rstrip("."))
    return picked, "; ".join(dict.fromkeys(focus_parts)), notes


def build_workflow(request: TeamRequest) -> tuple[WorkflowDef, list[str], list[str]]:
    """Return (workflow, specialization ids, notes) for a request, without models assigned."""
    spec_ids = list(dict.fromkeys([*request.specializations, *S.detect_specializations(request.description)]))
    unknown = [s for s in request.specializations if s not in S.SPECIALIZATIONS]
    if unknown:
        raise TeamError(f"Unknown specialization: {', '.join(unknown)}")
    if not spec_ids and not request.description.strip():
        raise TeamError("Choose at least one specialization or describe the team you want.")
    notes: list[str] = []
    for sid in spec_ids:
        modality = S.SPECIALIZATIONS[sid].unsupported_modality
        if modality:
            notes.append(UNSUPPORTED_MODALITY_MESSAGE[modality])
    custom = S.detect_custom_specialists(request.description)
    name = request.name or (
        " + ".join(S.SPECIALIZATIONS[s].label for s in spec_ids[:3]) + (" Team" if spec_ids else "")
        or "Custom Team"
    )
    workflow_id = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"

    single = S.SPECIALIZATIONS[spec_ids[0]] if len(spec_ids) == 1 else None
    # A specialization with its own domain specialist gets a generated pipeline that includes it.
    if single and single.template and not custom and single.specialist is None:
        workflow = T.BUILTIN[single.template](focus=single.focus if single.id != single.template else "")
        workflow.workflow.id = workflow_id
        workflow.workflow.name = name
        workflow.workflow.specializations = spec_ids
        return workflow, spec_ids, list(dict.fromkeys(notes))

    role_list, focus, role_notes = select_roles(spec_ids, request.description)
    if not spec_ids:
        # Pure custom description with no recognised area: a general advisory team.
        role_list = [R.ROLES["supervisor"], R.specialist("domain_specialist", "Domain Specialist", request.description.strip()[:160]),
                     R.ROLES["documentation_specialist"], R.ROLES["technical_reviewer"]] + [
            R.specialist(sid, title, f, phase=phase) for sid, title, f, phase in custom
        ]
        focus = request.description.strip()
    workflow = T.generate_pipeline(
        workflow_id, name, request.description.strip() or f"Team for {focus}", role_list, focus=focus, specializations=spec_ids
    )
    return workflow, spec_ids, list(dict.fromkeys(notes + role_notes))


# -- model selection ----------------------------------------------------------


def _vram(card: ModelCard) -> float:
    return 0.0 if card.fit.placement == Placement.CPU else card.fit.vram_required_gb


def _key(card: ModelCard) -> str:
    return f"{card.entry.repo.lower()}|{card.quant}"


class TeamBuilder:
    def __init__(
        self,
        catalog: CatalogService,
        settings: Settings,
        budget: ResourceBudget,
        installed: list[InstalledModel],
        drives: list[DriveInfo],
        models_dir: str | None,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.budget = budget
        self.installed = installed
        self.drives = drives
        self.models_dir = models_dir
        # slot id -> kind of work, for the workflow being planned
        self._kinds: dict[str, str] = {}

    def _kind(self, slot: str) -> str:
        kind = self._kinds.get(slot) or slot
        return kind if kind in S.SLOT_PROFILES else R.REASONING

    def _profile(self, slot: str) -> Any:
        return S.SLOT_PROFILES[self._kind(slot)]

    # candidates and scoring
    def _candidates(self, slot: str, request: TeamRequest, context: int, limit: int = 8) -> list[ModelCard]:
        return self.catalog.recommend(
            self._profile(slot), self.budget, self.installed, limit=limit, alignment=request.alignment,
            preference=self._preference(request), context_length=context,
        )

    def _preference(self, request: TeamRequest) -> AlignmentPreference:
        if request.preference is not None:
            return request.preference
        if request.alignment == Alignment.UNCENSORED:
            return AlignmentPreference.UNCENSORED
        return self.settings.models.alignment_preference

    def _score_for(self, card: ModelCard, slot: str, context: int, request: TeamRequest) -> ModelCard:
        installed = next((m for m in self.installed if m.model_key == card.installed_key), None)
        return self.catalog.evaluate(
            card.entry, self.budget, self._profile(slot), installed=installed, context_length=context,
            quant=None if installed else card.quant, preference=self._preference(request),
            measured=self.catalog.measured_tps(),
        )

    def _override(self, choice: ModelChoice, slot: str, context: int, request: TeamRequest) -> ModelCard:
        if choice.installed_key:
            model = next((m for m in self.installed if m.model_key == choice.installed_key), None)
            if model is None:
                raise TeamError(f"Model '{choice.installed_key}' is not installed.")
            from agentfit.catalog.service import entry_from_installed

            entry = self.catalog.known_entries().get((model.repo or "").lower()) or entry_from_installed(model)
            return self.catalog.evaluate(entry, self.budget, self._profile(slot), installed=model, context_length=context,
                                         preference=self._preference(request), measured=self.catalog.measured_tps())
        if not choice.repo:
            raise TeamError(f"Override for slot '{slot}' names no model.")
        entry, _ = self.catalog.details(choice.repo)
        if entry is None:
            raise TeamError(f"Model '{choice.repo}' was not found.")
        installed = next((m for m in self.installed if (m.repo or "").lower() == choice.repo.lower()
                          and (not choice.quant or m.quant == choice.quant.upper())), None)
        return self.catalog.evaluate(entry, self.budget, self._profile(slot), installed=installed, context_length=context,
                                     quant=None if installed else choice.quant, preference=self._preference(request),
                                     measured=self.catalog.measured_tps())

    def _pick(self, cards: list[ModelCard], request: TeamRequest) -> tuple[ModelCard, str | None]:
        """Best card, preferring an installed one that is nearly as good."""
        best = cards[0]
        if request.prefer_installed and not best.installed:
            installed = [c for c in cards if c.installed and best.score.total - c.score.total <= INSTALLED_TOLERANCE]
            if installed:
                pick = installed[0]
                return pick, (
                    f"{pick.entry.name} is already installed and scores within "
                    f"{best.score.total - pick.score.total:.1f} points of the top pick ({best.entry.name}), so no download is needed."
                )
        return best, None

    def assign_models(
        self, workflow: WorkflowDef, request: TeamRequest
    ) -> tuple[dict[str, ModelCard], dict[str, list[ModelCard]], list[str]]:
        """Choose a model for every slot. Returns (slot -> card, slot -> alternatives, explanations)."""
        slots = list(workflow.models)
        self._kinds = {s: workflow.models[s].kind or s for s in slots}
        explanations: list[str] = []
        contexts = {s: workflow.models[s].context_length for s in slots}
        # Slots that do the same kind of work at the same context share one search.
        searched: dict[tuple[str, int], list[ModelCard]] = {}
        candidates: dict[str, list[ModelCard]] = {}
        for slot in slots:
            key = (self._kind(slot), contexts[slot])
            if key not in searched:
                searched[key] = self._candidates(slot, request, contexts[slot])
            candidates[slot] = searched[key]
        for slot in slots:
            if not candidates[slot] and slot not in request.overrides:
                kind = "uncensored " if request.alignment == Alignment.UNCENSORED else ""
                raise TeamError(
                    f"No {kind}model that this computer can run was found for the {self._kind(slot)} role. "
                    "Connect to the Internet so Will It Run can search Hugging Face, or relax the model filter."
                )
        chosen: dict[str, ModelCard] = {}
        for slot in slots:
            if slot in request.overrides:
                chosen[slot] = self._override(request.overrides[slot], slot, contexts[slot], request)
                explanations.append(f"{_slot_label(workflow, slot)}: {chosen[slot].entry.name} was chosen manually.")
        free_slots = [s for s in slots if s not in chosen]
        if not free_slots:
            return chosen, candidates, explanations

        # Score every candidate against every open slot.
        pool: dict[str, ModelCard] = {}
        for slot in free_slots:
            for card in candidates[slot]:
                pool.setdefault(_key(card), card)
        matrix: dict[str, dict[str, ModelCard]] = {
            key: {slot: self._score_for(card, slot, contexts[slot], request) for slot in free_slots}
            for key, card in pool.items()
        }
        matrix = {k: v for k, v in matrix.items() if all(c.fit.runnable for c in v.values())}

        def mean(key: str) -> float:
            return sum(matrix[key][s].score.total for s in free_slots) / len(free_slots)

        specialists: dict[str, ModelCard] = {}
        for slot in free_slots:
            ranked = sorted((matrix[k][slot] for k in matrix), key=lambda c: -c.score.total) or candidates[slot]
            specialists[slot], note = self._pick(ranked, request)
            if note:
                explanations.append(note)

        if len(free_slots) == 1 or not matrix:
            chosen.update(specialists)
            return chosen, candidates, explanations

        ranked_shared = sorted(matrix, key=lambda k: -mean(k))
        shared_cards = [matrix[k][free_slots[0]] for k in ranked_shared]
        shared_pick, shared_note = self._pick(shared_cards, request)
        shared_key = _key(shared_pick)
        specialist_mean = sum(specialists[s].score.total for s in free_slots) / len(free_slots)
        gain = specialist_mean - mean(shared_key)
        distinct = {_key(c) for c in specialists.values()}

        if len(distinct) == 1:
            chosen.update(specialists)
            explanations.append(
                f"One model, {next(iter(specialists.values())).entry.name}, is the best fit for every role, so all "
                f"{len(workflow.agents)} agents share a single loaded instance."
            )
        elif request.model_strategy == "single" or request.max_models <= 1:
            for slot in free_slots:
                chosen[slot] = matrix[shared_key][slot]
            explanations.append(
                f"One model for every role, as requested: {shared_pick.entry.name} has the best average across the roles."
            )
            if shared_note:
                explanations.append(shared_note)
        elif gain < SPECIALIST_GAIN_THRESHOLD and request.model_strategy != "specialists":
            for slot in free_slots:
                chosen[slot] = matrix[shared_key][slot]
            explanations.append(
                f"A single strong model ({shared_pick.entry.name}) covers every role. Dedicated specialist models "
                f"would raise the average score by only {max(gain, 0):.1f} points, which does not justify the extra "
                "disk space and model switching."
            )
            if shared_note:
                explanations.append(shared_note)
        else:
            chosen.update(specialists)
            if request.model_strategy == "specialists" and gain < SPECIALIST_GAIN_THRESHOLD:
                explanations.append(
                    f"A model per role, as requested: {len(distinct)} models, {max(gain, 0):.1f} points better on average "
                    f"than sharing {shared_pick.entry.name}. Only one is in memory at a time, so each change of role "
                    "that changes model is a reload."
                )
            else:
                explanations.append(
                    f"Specialist models improve the average role score by {gain:.1f} points over a single shared model "
                    f"({shared_pick.entry.name}), so Will It Run uses {len(distinct)} models."
                )
            # Too many distinct models: merge the slot that loses least by sharing. Models the
            # user chose by hand are theirs to count; the limit applies to the automatic picks.
            while len({_key(chosen[s]) for s in free_slots}) > max(1, request.max_models):
                keys_in_use = {_key(chosen[s]) for s in free_slots}
                best_move: tuple[float, str, str] | None = None
                for slot in free_slots:
                    for other in keys_in_use - {_key(chosen[slot])}:
                        if other in matrix:
                            loss = chosen[slot].score.total - matrix[other][slot].score.total
                            if best_move is None or loss < best_move[0]:
                                best_move = (loss, slot, other)
                if best_move is None:
                    break
                _, slot, other = best_move
                chosen[slot] = matrix[other][slot]
        return chosen, candidates, explanations

    # -- resource planning --------------------------------------------------
    def plan_resources(self, workflow: WorkflowDef, chosen: dict[str, ModelCard]) -> tuple[ResourcePlan, dict[str, LoadingPolicy]]:
        usable = self.budget.vram_usable_gb
        by_model: dict[str, list[str]] = {}
        cards: dict[str, ModelCard] = {}
        for slot, card in chosen.items():
            by_model.setdefault(_key(card), []).append(slot)
            cards[_key(card)] = card
        vram = {k: _vram(c) for k, c in cards.items()}
        steps = {k: sum(1 for a in workflow.agents if a.model in slots) for k, slots in by_model.items()}
        total = sum(vram.values())

        policies: dict[str, LoadingPolicy] = {}
        if len(cards) == 1 or total <= usable:
            policies = {k: LoadingPolicy.KEEP_LOADED for k in cards}
        else:
            # The model most agents use is the persistent one: it is the last to be evicted and
            # is left loaded when the run ends. The others are loaded only while needed.
            primary = max(cards, key=lambda k: (steps[k], vram[k]))
            for key in cards:
                policies[key] = LoadingPolicy.LOAD_ON_DEMAND
            policies[primary] = LoadingPolicy.KEEP_LOADED

        # Walk the agents in order, loading and evicting exactly as the run-time manager will.
        loaded: list[str] = []
        timeline: list[TimelineSegment] = []
        peak, switches = 0.0, 0
        slot_to_key = {slot: _key(card) for slot, card in chosen.items()}
        for index, agent in enumerate(workflow.agents):
            key = slot_to_key[agent.model]
            switched = False
            if key in loaded:
                loaded.remove(key)
                loaded.append(key)
            else:
                order = sorted(loaded, key=lambda k: (policies[k] == LoadingPolicy.KEEP_LOADED, loaded.index(k)))
                while loaded and sum(vram[k] for k in loaded) + vram[key] > usable and order:
                    loaded.remove(order.pop(0))
                loaded.append(key)
                switched = True
                if index > 0:
                    switches += 1
            current = sum(vram[k] for k in loaded)
            peak = max(peak, current)
            timeline.append(
                TimelineSegment(
                    agent=agent.id, role=agent.role, model=cards[key].entry.name, slot=agent.model, vram_gb=round(vram[key], 1),
                    start=index, end=index + 1, shared=steps[key] > 1,
                    loaded_models=[cards[k].entry.name for k in loaded], loaded_vram_gb=round(current, 1), switch=switched,
                )
            )

        ram = sum(c.fit.ram_required_gb for c in cards.values() if policies[_key(c)] == LoadingPolicy.KEEP_LOADED)
        ram += max((c.fit.ram_required_gb for c in cards.values() if policies[_key(c)] != LoadingPolicy.KEEP_LOADED), default=0.0)
        download = sum((c.download_size_bytes or 0) for c in cards.values() if not c.installed) / GIB
        disk = sum((c.download_size_bytes or 0) for c in cards.values()) / GIB
        if peak > usable + 0.05:
            status = "OVER"
        elif usable and peak / usable > 0.97:
            status = "TIGHT"
        else:
            status = "SAFE"

        optimization = Optimization()
        if len(cards) > 1 and total > usable:
            persistent = sum(1 for p in policies.values() if p == LoadingPolicy.KEEP_LOADED)
            optimization = Optimization(
                needed=True,
                before_models_loaded=len(cards),
                before_peak_gb=round(total, 1),
                after_persistent=persistent,
                after_on_demand=len(cards) - persistent,
                after_peak_gb=round(peak, 1),
                message=(
                    f"Keeping all {len(cards)} models loaded would need {total:.1f} GB, above the {usable:.1f} GB "
                    f"recommended maximum. Will It Run keeps {persistent} model loaded and loads {len(cards) - persistent} "
                    f"on demand, for a peak of {peak:.1f} GB."
                ),
            )
        plan = ResourcePlan(
            usable_vram_gb=round(usable, 1), peak_vram_gb=round(peak, 1), ram_gb=round(ram, 1),
            download_gb=round(download, 1), disk_gb=round(disk, 1), model_switches_per_run=switches,
            timeline=timeline, status=status, optimization=optimization,
        )
        slot_policies = {slot: policies[key] for slot, key in slot_to_key.items()}
        return plan, slot_policies

    def _choices(self, workflow: WorkflowDef, candidates: dict[str, list[ModelCard]], request: TeamRequest) -> dict[str, list[ModelOption]]:
        """Every installed model plus the recommended ones, scored for each kind of work in the team."""
        from agentfit.catalog.service import entry_from_installed

        known = self.catalog.known_entries()
        measured = self.catalog.measured_tps()
        out: dict[str, list[ModelOption]] = {}
        for slot in workflow.models:
            kind = self._kind(slot)
            if kind in out:
                continue
            context = workflow.models[slot].context_length
            cards = [
                self.catalog.evaluate(
                    known.get((m.repo or "").lower()) or entry_from_installed(m), self.budget, S.SLOT_PROFILES[kind],
                    installed=m, context_length=context, preference=self._preference(request), measured=measured,
                )
                for m in self.installed if m.type == "llm"
            ]
            have = {_key(c) for c in cards}
            cards += [c for c in candidates.get(slot, []) if _key(c) not in have and not c.installed]
            cards.sort(key=lambda c: (not c.fit.runnable, -c.score.total))
            out[kind] = [
                ModelOption(
                    repo=c.entry.repo, quant=c.quant, installed_key=c.installed_key, name=c.entry.name,
                    score=round(c.score.agentfit_score, 1), fit=c.fit.category.value, runnable=c.fit.runnable,
                    vram_gb=round(_vram(c), 1), tokens_per_second=round(c.fit.speed.tokens_per_second, 1), installed=c.installed,
                    download_gb=0.0 if c.installed else round((c.download_size_bytes or 0) / GIB, 1),
                )
                for c in cards
            ]
        return out

    def _disk_plan(self, cards: list[ModelCard]) -> storage.DiskPlan | None:
        total = sum((c.download_size_bytes or 0) for c in cards if not c.installed)
        target = self.settings.storage.custom_model_dir or self.models_dir
        if not total or not target:
            return None
        return storage.plan_download(total, target, self.settings.storage, self.drives)

    # -- top level ----------------------------------------------------------
    def plan(self, request: TeamRequest) -> TeamPlan:
        if S.wants_uncensored(request.description) and request.alignment is None:
            request = request.model_copy(update={"alignment": Alignment.UNCENSORED})
        workflow, spec_ids, notes = build_workflow(request)
        request = _give_agents_their_own_model(workflow, request)
        chosen, candidates, explanations = self.assign_models(workflow, request)
        blockers: list[str] = []

        disk = self._disk_plan(list({_key(c): c for c in chosen.values()}.values()))
        if disk is not None and not disk.safe:
            # Not enough safe disk space: fall back to what is installed, then to one shared model.
            notes.append(disk.verdict)
            retry = request.model_copy(update={"max_models": 1, "prefer_installed": True})
            installed_only = [c for cards in candidates.values() for c in cards if c.installed]
            if installed_only:
                best = max(installed_only, key=lambda c: c.score.total)
                chosen = {slot: self._score_for(best, slot, workflow.models[slot].context_length, retry) for slot in workflow.models}
                explanations.append(
                    f"There is not enough safe disk space for new downloads, so the team uses the installed model {best.entry.name} for every role."
                )
            else:
                chosen, candidates, extra = self.assign_models(workflow, retry)
                explanations += extra
            disk = self._disk_plan(list({_key(c): c for c in chosen.values()}.values()))
            if disk is not None and not disk.safe:
                blockers.append(disk.verdict)
                notes += [f"Option: {o}" for o in disk.options]

        resources, slot_policies = self.plan_resources(workflow, chosen)
        resources.disk = disk

        assignments: list[ModelAssignment] = []
        grouped: dict[str, list[str]] = {}
        for slot, card in chosen.items():
            grouped.setdefault(_key(card), []).append(slot)
        for key, slots in grouped.items():
            card = chosen[slots[0]]
            agent_ids = [a.id for a in workflow.agents if a.model in slots]
            labels = list(dict.fromkeys(_slot_label(workflow, s) for s in slots))
            assignments.append(
                ModelAssignment(
                    slots=slots,
                    label=" + ".join(labels),
                    card=card,
                    agents=agent_ids,
                    loading_policy=slot_policies[slots[0]],
                    vram_gb=round(_vram(card), 1),
                    ram_gb=round(card.fit.ram_required_gb, 1),
                    needs_download=not card.installed,
                    download_gb=0.0 if card.installed else round((card.download_size_bytes or 0) / GIB, 1),
                    alternatives=[c for c in candidates.get(slots[0], []) if _key(c) != key][:4],
                    why=card.why + card.quant_reasoning,
                )
            )
            for slot in slots:
                model_slot = workflow.models[slot]
                model_slot.model_key = card.installed_key or ""
                model_slot.repo = card.entry.repo
                model_slot.quant = card.quant
                model_slot.context_length = card.fit.context_length
                model_slot.loading_policy = slot_policies[slot]
                model_slot.vram_gb = round(_vram(card), 2)
        for agent in workflow.agents:
            share = sum(1 for a in workflow.agents if chosen[a.model] is chosen[agent.model] or _key(chosen[a.model]) == _key(chosen[agent.model]))
            agent.loading_policy = LoadingPolicy.SHARED_INSTANCE if share > 1 else slot_policies[agent.model]

        if resources.status == "OVER":
            blockers.append(
                f"Peak VRAM {resources.peak_vram_gb:.1f} GB exceeds the {resources.usable_vram_gb:.1f} GB recommended maximum."
            )
        worst = min((a.card.fit.category for a in assignments), key=lambda c: CATEGORY_SCORE[c], default=FitCategory.IMPOSSIBLE)
        slowest = min((a.card.fit.speed.tokens_per_second for a in assignments), default=0.0)
        target = self.settings.performance.target_tokens_per_second
        speed = "EXCELLENT" if slowest >= target * 1.5 else "GOOD" if slowest >= target else "ACCEPTABLE" if slowest >= target / 2 else "SLOW"
        explanations.insert(
            0,
            f"{len(workflow.agents)} agents run on {len(assignments)} model instance{'s' if len(assignments) != 1 else ''}: "
            "agents are specialized by system prompt, tools and output schema, not by having their own model.",
        )
        if resources.optimization.needed:
            explanations.append(resources.optimization.message)
        workflow.resource_plan = {
            "peak_vram_gb": resources.peak_vram_gb,
            "usable_vram_gb": resources.usable_vram_gb,
            "ram_gb": resources.ram_gb,
            "download_gb": resources.download_gb,
            "model_switches_per_run": resources.model_switches_per_run,
            "status": resources.status,
            "models": [
                {"slots": a.slots, "repo": a.card.entry.repo, "quant": a.card.quant, "policy": a.loading_policy.value, "vram_gb": a.vram_gb}
                for a in assignments
            ],
        }
        preference = request.alignment.value if request.alignment else self._preference(request).value
        loads = {segment.agent: segment.switch for segment in resources.timeline}
        roles = [
            RoleModel(
                agent=a.id, role=a.role, goal=a.goal, kind=self._kind(a.model), kind_label=S.SLOT_PROFILES[self._kind(a.model)].label,
                slot=a.model, repo=chosen[a.model].entry.repo, quant=chosen[a.model].quant, model=chosen[a.model].entry.name,
                installed=chosen[a.model].installed, installed_key=chosen[a.model].installed_key,
                score=round(chosen[a.model].score.agentfit_score, 1), vram_gb=round(_vram(chosen[a.model]), 1),
                tokens_per_second=round(chosen[a.model].fit.speed.tokens_per_second, 1),
                manual=a.model in request.overrides, loads_model=bool(loads.get(a.id)),
            )
            for a in workflow.agents
        ]
        return TeamPlan(
            id=workflow.workflow.id,
            name=workflow.workflow.name,
            request=request,
            specializations=spec_ids,
            agents=[
                AgentSummary(id=a.id, role=a.role, goal=a.goal, slot=a.model, tools=a.tools, output_schema=a.output_schema)
                for a in workflow.agents
            ],
            models=assignments,
            roles=roles,
            choices=self._choices(workflow, candidates, request),
            resource_plan=resources,
            workflow=workflow,
            workflow_yaml=dump_yaml(workflow),
            fit=worst.value,
            expected_speed=speed,
            model_preference={"any": "ANY", "standard": "STANDARD", "uncensored": "UNCENSORED"}.get(preference, "ANY"),
            can_build=not blockers,
            notes=list(dict.fromkeys(notes)),
            explanations=explanations,
            blockers=blockers,
        )


def _slot_label(workflow: WorkflowDef, slot: str) -> str:
    return workflow.models[slot].label or T.SLOT_LABELS.get(slot, (slot.replace("_", " ").title(), ""))[0]


def _give_agents_their_own_model(workflow: WorkflowDef, request: TeamRequest) -> TeamRequest:
    """Move each agent the user chose a model for onto a slot of its own.

    Returns the request with those choices turned into slot overrides. Slots left without
    an agent are removed, so the plan never carries a model nobody uses.
    """
    if not request.agent_overrides:
        return request
    overrides = dict(request.overrides)
    for agent in workflow.agents:
        choice = request.agent_overrides.get(agent.id)
        if choice is None:
            continue
        shared = workflow.models[agent.model]
        slot_id = f"{agent.id}_model"
        workflow.models[slot_id] = shared.model_copy(update={"label": agent.role, "kind": shared.kind or agent.model})
        agent.model = slot_id
        overrides[slot_id] = choice
    used = {a.model for a in workflow.agents}
    for slot_id in [s for s in workflow.models if s not in used]:
        del workflow.models[slot_id]
        overrides.pop(slot_id, None)
    return request.model_copy(update={"overrides": overrides})


def resolve_model_keys(workflow: WorkflowDef, installed: list[InstalledModel]) -> list[str]:
    """Fill each slot's LM Studio model key from its repo and quant. Returns unresolved slots."""
    missing: list[str] = []
    for slot_id, slot in workflow.models.items():
        if slot.model_key and any(m.model_key == slot.model_key for m in installed):
            continue
        if is_cloud(slot.model_key):
            # Called over the Internet: nothing to install. Whether the provider has a key is checked at the call.
            continue
        match = next(
            (
                m for m in installed
                if slot.repo and (m.repo or "").lower() == slot.repo.lower() and (not slot.quant or m.quant == slot.quant.upper())
            ),
            None,
        ) or next((m for m in installed if slot.repo and (m.repo or "").lower() == slot.repo.lower()), None)
        if match:
            slot.model_key = match.model_key
        else:
            missing.append(slot_id)
    return missing


def plan_to_summary(plan: TeamPlan) -> dict[str, Any]:
    """The compact recommendation card shown before building."""
    return {
        "agents": len(plan.agents),
        "models": len(plan.models),
        "download_gb": plan.resource_plan.download_gb,
        "peak_vram_gb": plan.resource_plan.peak_vram_gb,
        "ram_gb": plan.resource_plan.ram_gb,
        "disk_gb": plan.resource_plan.disk_gb,
        "expected_speed": plan.expected_speed,
        "model_preference": plan.model_preference,
        "fit": plan.fit,
        "model_switches_per_run": plan.resource_plan.model_switches_per_run,
        "can_build": plan.can_build,
    }
