// Shapes returned by the Will It Run core API. Kept in sync with the Pydantic models.

export type Confidence = "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN";
export type FitCategory =
  | "PERFECT"
  | "EXCELLENT"
  | "GOOD"
  | "ACCEPTABLE"
  | "SLOW"
  | "MEMORY TIGHT"
  | "NOT RECOMMENDED"
  | "IMPOSSIBLE";
export type Alignment = "standard" | "uncensored";
export type Placement = "gpu" | "gpu_split" | "partial_offload" | "cpu" | "none";

export interface GpuInfo {
  index: number;
  vendor: string;
  name: string;
  vram_total_mb: number;
  vram_used_mb: number | null;
  vram_free_mb: number | null;
  shared_memory_mb: number | null;
  driver_version: string | null;
  driver_date: string | null;
  is_integrated: boolean;
  backend: string;
  cuda_version: string | null;
  compute_capability: string | null;
  memory_bandwidth_gbps: number | null;
  sources: string[];
  vram_confidence: Confidence;
}

export interface DriveInfo {
  letter: string;
  label: string | null;
  filesystem: string | null;
  total_bytes: number;
  free_bytes: number;
  media_type: string | null;
  bus_type: string | null;
}

export interface HardwareProfile {
  os: { caption: string; version: string; build: string; architecture: string };
  cpu: {
    manufacturer: string;
    model: string;
    architecture: string;
    physical_cores: number;
    logical_cores: number;
    max_clock_mhz: number;
    features: Record<string, boolean | null>;
  };
  ram: {
    total_bytes: number;
    available_bytes: number;
    installed_bytes: number | null;
    memory_type: string | null;
    speed_mhz: number | null;
    modules: { capacity_bytes: number; speed_mhz: number | null; slot: string | null }[];
    bandwidth_gbps: number | null;
  };
  gpus: GpuInfo[];
  drives: DriveInfo[];
  detected_at: string;
  confidence: Record<string, Confidence>;
  warnings: string[];
}

export interface GpuBudget {
  index: number;
  name: string;
  backend: string;
  total_gb: number;
  os_reserve_gb: number;
  safety_reserve_gb: number;
  usable_gb: number;
  bandwidth_gbps: number | null;
}

export interface ResourceBudget {
  gpus: GpuBudget[];
  single_gpu_usable_gb: number;
  split_usable_gb: number;
  split_supported: boolean;
  ram_total_gb: number;
  ram_reserve_gb: number;
  ram_usable_gb: number;
  gpu_bandwidth_gbps: number | null;
  ddr_bandwidth_gbps: number;
  cpu_cores: number;
  explanation: string[];
}

export interface HardwareResponse {
  profile: HardwareProfile;
  budget: ResourceBudget;
  fingerprint: string;
}

export interface LMStudioStatus {
  installed: boolean;
  home: string | null;
  app_path: string | null;
  cli_path: string | null;
  models_dir: string | null;
  server_running: boolean;
  server_port: number | null;
  api_reachable: boolean;
  cli_version: string | null;
  engines: string[];
  notes: string[];
  download_url?: string;
}

export interface ModelFile {
  repo: string;
  filename: string;
  quant: string | null;
  size_bytes: number | null;
  sha256: string | null;
  revision: string | null;
  url?: string;
}

export interface ModelEntry {
  repo: string;
  name: string;
  publisher: string;
  base_models: string[];
  quantizer: string | null;
  family: string | null;
  architecture: string | null;
  params_b: number | null;
  /** The size the model's name states ("27B"); exact metadata counts can differ (27.8B). */
  nominal_params_b: number | null;
  active_params_b: number | null;
  is_moe: boolean;
  context_length: number | null;
  license: string | null;
  capabilities: string[];
  categories: string[];
  alignment: Alignment;
  alignment_evidence: string[];
  downloads: number | null;
  likes: number | null;
  release_date: string | null;
  last_modified: string | null;
  tags: string[];
  files: ModelFile[];
  mmproj_files: ModelFile[];
  files_complete: boolean;
  revision: string | null;
  source: string;
  metadata_confidence: Confidence;
  unknown_fields: string[];
  quality: { coding: number; reasoning: number; writing: number; general: number; basis: string; confidence: Confidence };
  model_card_url?: string;
}

export interface SpeedEstimate {
  tokens_per_second: number;
  low: number;
  high: number;
  method: string;
  confidence: Confidence;
  measured: boolean;
  prompt_tokens_per_second: number | null;
  ttft_ms: number | null;
  notes: string[];
}

export interface MemoryEstimate {
  weights_gb: number;
  weights_source: string;
  kv_cache_gb: number;
  kv_source: string;
  runtime_overhead_gb: number;
  vision_projector_gb: number;
  draft_model_gb: number;
  context_length: number;
  parallel: number;
  total_gb: number;
  confidence: Confidence;
}

export interface FitResult {
  category: FitCategory;
  placement: Placement;
  quant: string;
  context_length: number;
  memory: MemoryEstimate;
  vram_required_gb: number;
  ram_required_gb: number;
  vram_usable_gb: number;
  vram_headroom_gb: number;
  gpu_offload_ratio: number;
  speed: SpeedEstimate;
  reasons: { kind: string; text: string }[];
  fit_score: number;
}

export interface ScoreBreakdown {
  hardware_fit: number;
  task_quality: number;
  specialization: number;
  speed: number;
  context: number;
  tool_use: number;
  reliability: number;
  local_benchmark: number;
  preference_bonus: number;
  total: number;
  agentfit_score: number;
  confidence: Record<string, string>;
}

export interface ModelCard {
  entry: ModelEntry;
  quant: string;
  file: ModelFile | null;
  download_size_bytes: number | null;
  size_is_estimate: boolean;
  fit: FitResult;
  score: ScoreBreakdown;
  accuracy: Accuracy | null;
  stars: Record<string, number>;
  why: string[];
  quant_reasoning: string[];
  installed: boolean;
  installed_key: string | null;
  measured_tps: number | null;
}

export interface InstalledModel {
  model_key: string;
  display_name: string;
  type: string;
  repo: string | null;
  filename: string | null;
  quant: string | null;
  size_bytes: number | null;
  params_string: string | null;
  vision: boolean;
  tool_use: boolean;
  max_context_length: number | null;
  loaded_instances: { instance_id: string; model_key: string; context_length: number | null }[];
}

export interface SearchResponse {
  cards: ModelCard[];
  parsed: { terms: string[]; alignment: Alignment | null; categories: string[]; params_b: number | null; vram_gb: number | null };
  online: boolean;
  notes: string[];
  /** True when Hugging Face could not be reached for this search. */
  hub_unreachable: boolean;
  total_candidates: number;
  /** The exact sizes the catalog offers, with how many models state each. */
  sizes: SizeFacet[];
}

export interface SizeFacet {
  params_b: number;
  models: number;
}

/** What a quantization costs in quality: a published reference measurement, not an estimate. */
export interface Accuracy {
  quant: string;
  /** 5 = indistinguishable from the original ... 0 = severe loss. */
  level: number;
  label: string;
  ppl_delta: number | null;
  bits_per_weight: number | null;
  basis: "measured" | "nearest" | "bits" | "original" | "unknown";
  reference: string | null;
  note: string;
}

export interface ModelFileRow {
  file: ModelFile;
  fit: FitResult;
  score: ScoreBreakdown;
  accuracy: Accuracy;
  installed: boolean;
}

export interface QuantizationReference {
  table: Accuracy[];
  reference: { model: string; source: string; url: string; caveat: string };
}

export interface DownloadPlan {
  plan: DiskPlan;
  backend: string;
  default_folder: string | null;
  lmstudio_folder: string | null;
  outside_lmstudio: boolean;
}

export interface ModelDetails {
  entry: ModelEntry;
  card: ModelCard;
  files: ModelFileRow[];
  /** The file Will It Run would pick among all of them, whatever is installed. */
  recommended_file: string | null;
  online: boolean;
  installed: InstalledModel[];
  benchmarks: Benchmark[];
  stars: Record<string, number>;
}

export interface DiskPlan {
  target_dir: string;
  drive: string;
  download_gb: number;
  temp_gb: number;
  required_gb: number;
  free_gb: number;
  reserve_gb: number;
  safe_available_gb: number;
  after_install_free_gb: number;
  after_install_safe_gb: number;
  safe: boolean;
  verdict: string;
  /** The verdict without its SAFE / NOT SAFE prefix. */
  reason: string;
  options: string[];
}

export interface DownloadRecord {
  id: string;
  repo: string;
  filename: string;
  quant: string | null;
  size_bytes: number | null;
  downloaded_bytes: number;
  backend: string;
  target_dir: string;
  status: string;
  error: string | null;
  created_at: string;
  bytes_per_second: number | null;
  note: string | null;
  can_pause: boolean;
  can_cancel: boolean;
  progress: number;
}

export interface Benchmark {
  id: number | null;
  model_key: string;
  quant: string | null;
  context_length: number | null;
  load_time_s: number | null;
  ttft_ms: number | null;
  prompt_tps: number | null;
  generation_tps: number | null;
  vram_used_mb: number | null;
  ram_used_mb: number | null;
  gpu_util_percent: number | null;
  output_tokens: number | null;
  created_at: string;
  notes: string[];
  agentfit_score?: number | null;
  fit?: FitCategory | null;
  display_name?: string;
}

export interface SpecGroup {
  group: string;
  items: { id: string; label: string; focus: string; unsupported_modality: string | null }[];
}

export type LoadingPolicy =
  | "keep_loaded"
  | "load_on_demand"
  | "unload_after_use"
  | "shared_instance"
  | "sequential"
  | "parallel";

export interface ModelSlot {
  label: string;
  /** What the agents on this slot need from a model: reasoning, coding, writing or vision. */
  kind?: string;
  model_key: string;
  repo: string | null;
  quant: string | null;
  context_length: number;
  loading_policy: LoadingPolicy;
  /** Automatic: the context is chosen for each request from what it needs and what the VRAM holds. */
  auto_tune?: boolean;
  vram_gb: number | null;
  purpose: string;
  /** Stand-in for requests that do not fit `context_length`, usually the same model at a smaller quantization. */
  wide_model_key: string;
  wide_context_length: number | null;
}

export interface AgentDef {
  id: string;
  role: string;
  goal: string;
  responsibilities: string[];
  model: string;
  system_prompt: string;
  tools: string[];
  input: string;
  output_schema: string;
  loading_policy: LoadingPolicy;
  max_retries: number;
  escalation: "fail" | "human" | "continue";
  temperature: number;
  max_tool_rounds: number;
  /** Which earlier outputs this agent is shown. null = everything so far. */
  reads?: string[] | null;
  /** Cap, in tokens, on the earlier work put in the prompt. */
  max_input_tokens?: number | null;
  /** Fixed values for text fields of the output. */
  choices?: Record<string, string[]>;
}

/** A deterministic step: code, not a model. */
export interface CheckDef {
  id: string;
  kind: string;
  label: string;
  params: Record<string, unknown>;
}

/** How much context a model can be given on this PC, and how that is known. */
export interface ContextAdvice {
  model_key: string;
  max_context: number | null;
  /** Largest context that fits the usable VRAM. 0: the model itself does not fit. null: not known. */
  safe_context: number | null;
  basis: "measured" | "estimate" | null;
  fits_gpu: boolean | null;
}

export interface CheckInfo {
  kind: string;
  label: string;
  description: string;
  defaults: Record<string, unknown>;
}

export interface ApprovalDef {
  id: string;
  prompt: string;
  show: string | null;
  on_reject: string;
  max_rejections: number;
}

export interface EdgeDef {
  from: string | string[];
  to: string;
  when: string | null;
  label: string | null;
  max_loops: number | null;
  on_exhausted: string | null;
}

export interface WorkflowDef {
  workflow: { id: string; name: string; description: string; version: number; max_steps: number; specializations: string[]; category?: string };
  models: Record<string, ModelSlot>;
  agents: AgentDef[];
  checks?: CheckDef[];
  human_approval: ApprovalDef[];
  edges: EdgeDef[];
  conditions: Record<string, string>;
  shared_state: Record<string, unknown>;
  resource_plan: Record<string, unknown>;
  layout: Record<string, { x: number; y: number }>;
}

export interface ValidationResult {
  ok: boolean;
  issues: { level: "error" | "warning"; message: string; node: string | null }[];
}

export interface WorkflowInfo {
  id: string;
  name: string;
  description: string;
  source: "template" | "user";
  agents: number;
  models: number;
  runnable: boolean;
  updated_at: string | null;
  category: string;
  checks: number;
  /** True when a run can be given a folder of documents as evidence. */
  evidence: boolean;
  /** Id of the run of this workflow that is working right now, if one is. */
  running_run?: string | null;
}

export interface TimelineSegment {
  agent: string;
  role: string;
  model: string;
  slot: string;
  vram_gb: number;
  start: number;
  end: number;
  shared: boolean;
  loaded_models: string[];
  loaded_vram_gb: number;
  switch: boolean;
}

export interface ModelAssignment {
  slots: string[];
  label: string;
  card: ModelCard;
  agents: string[];
  loading_policy: LoadingPolicy;
  vram_gb: number;
  ram_gb: number;
  needs_download: boolean;
  download_gb: number;
  alternatives: ModelCard[];
  why: string[];
}

export interface TeamPlan {
  id: string;
  name: string;
  specializations: string[];
  agents: { id: string; role: string; goal: string; slot: string; tools: string[]; output_schema: string }[];
  models: ModelAssignment[];
  roles: RoleModel[];
  choices: Record<string, ModelOption[]>;
  resource_plan: {
    usable_vram_gb: number;
    peak_vram_gb: number;
    ram_gb: number;
    download_gb: number;
    disk_gb: number;
    disk: DiskPlan | null;
    model_switches_per_run: number;
    timeline: TimelineSegment[];
    status: "SAFE" | "TIGHT" | "OVER";
    optimization: {
      needed: boolean;
      before_models_loaded: number;
      before_peak_gb: number;
      after_persistent: number;
      after_on_demand: number;
      after_peak_gb: number;
      message: string;
    };
  };
  workflow: WorkflowDef;
  workflow_yaml: string;
  fit: FitCategory;
  expected_speed: string;
  model_preference: string;
  can_build: boolean;
  notes: string[];
  explanations: string[];
  blockers: string[];
}

/** One agent and the model that serves it. */
export interface RoleModel {
  agent: string;
  role: string;
  goal: string;
  kind: string;
  kind_label: string;
  slot: string;
  repo: string;
  quant: string;
  model: string;
  installed: boolean;
  installed_key: string | null;
  score: number;
  vram_gb: number;
  tokens_per_second: number;
  manual: boolean;
  loads_model: boolean;
}

/** A model that can be given to a role, scored for that kind of work. */
export interface ModelOption {
  repo: string;
  quant: string;
  installed_key: string | null;
  name: string;
  score: number;
  fit: FitCategory;
  runnable: boolean;
  vram_gb: number;
  tokens_per_second: number;
  installed: boolean;
  download_gb: number;
}

export interface TeamSummary {
  agents: number;
  models: number;
  download_gb: number;
  peak_vram_gb: number;
  ram_gb: number;
  disk_gb: number;
  expected_speed: string;
  model_preference: string;
  fit: FitCategory;
  model_switches_per_run: number;
  can_build: boolean;
}

export interface PendingApproval {
  id: string;
  run_id: string;
  node_id: string;
  kind: "gate" | "tool" | "escalation";
  prompt: string;
  payload: Record<string, unknown>;
  requested_at: string;
}

export interface RunRecord {
  id: string;
  workflow_id: string;
  project: string;
  title: string;
  task: string;
  status: string;
  result: {
    final: Record<string, unknown> | null;
    final_node: string | null;
    outputs: Record<string, Record<string, unknown>>;
    history: string[];
    model_switches: number;
    /** One entry per step: the hash of its output and of everything it read. */
    ledger?: { node: string; kind: string; sha256: string; read: Record<string, string>; model: string }[];
  } | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  pending_approvals: PendingApproval[];
  /** The request written while this run was working: it starts when the run ends. */
  queued?: string | null;
}

/** One message of a chat and what the model did to answer it. A turn is a run. */
export interface ChatTurn {
  id: string;
  message: string;
  status: string;
  error: string | null;
  answer: string;
  started_at: string;
  finished_at: string | null;
  events: RunEvent[];
  pending_approvals: PendingApproval[];
  /** The message written while this one was being answered. */
  queued: string | null;
}

export interface ChatInfo {
  id: string;
  title: string;
  /** LM Studio key of the model that answered last. */
  model_key: string;
  source: Record<string, unknown>;
  approval?: string;
  /** The folder the last message worked in: the user's, a copy of a repository, or its own. */
  workspace?: string;
  created_at: string;
  updated_at: string;
  turns: number;
  active: boolean;
}

export interface ChatDetail extends Omit<ChatInfo, "turns"> {
  turns: ChatTurn[];
}

export interface RunEvent {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
}

export interface LiveState {
  ram_total_bytes: number;
  ram_used_bytes: number;
  gpus: { index: number; name: string; vram_total_mb: number | null; vram_used_mb: number | null; utilization_percent: number | null; temperature_c: number | null }[];
  loaded: { instance_id: string; model_key: string }[];
  lmstudio_connected: boolean;
}

export interface Settings {
  runtime: {
    primary: string;
    lmstudio_host: string;
    lmstudio_port: number;
    lmstudio_api_token_set?: boolean;
    lmstudio_api_token?: string;
    speculative_mtp: boolean;
    speculative_draft_tokens: number;
  };
  hardware: { vram_os_reserve_gb: number; vram_reserve_percent: number; ram_reserve_percent: number; tps_efficiency: number; ddr_bandwidth_gbps: number | null };
  storage: { reserve_percent: number; min_free_gb: number; temp_overhead_percent: number; custom_model_dir: string };
  models: { alignment_preference: "any" | "standard" | "uncensored"; default_context_length: number; huggingface_enabled: boolean; catalog_cache_hours: number };
  performance: { target_tokens_per_second: number };
  workflow: {
    max_retries: number;
    max_steps: number;
    require_approval_for_high_risk_actions: boolean;
    model_idle_ttl_seconds: number;
    vram_margin_mib: number;
    vram_saturated_mib: number;
  };
  /** The Project Brain. `embedding_model` empty: the best embedding model installed. */
  memory: { enabled: boolean; embedding_model: string };
  /** Cloud models. The API keys are never part of the settings. */
  cloud: { max_context: number };
  /** The image studio: ComfyUI is the engine, and the gallery is the app's own copy. */
  images: {
    comfy_url: string;
    comfy_dir: string;
    timeout_seconds: number;
    request_timeout_seconds: number;
    gallery_dir: string;
    free_vram_on_leave: boolean;
    civitai_token: string;
    hf_token: string;
  };
  downloads: { backend: string; max_concurrent: number; verify_hash: boolean };
  scoring: Record<string, number>;
  ui: {
    theme: "dark" | "light" | "system";
    accent: string;
    mode: "automatic" | "guided" | "expert";
    notifications: boolean;
    first_run_complete: boolean;
    language: "system" | "en" | "it";
    zoom: number;
    guide_dismissed: boolean;
  };
}

// -- project brain ---------------------------------------------------------------------

export type MemoryKind = "architecture" | "decision" | "change" | "error" | "solution" | "note" | "session";

export interface BrainProject {
  id: string;
  key: string;
  kind: "folder" | "github";
  name: string;
  path: string | null;
  created_at: string;
  updated_at: string;
  memories: number;
  counts: Partial<Record<MemoryKind, number>>;
}

export interface BrainMemory {
  id: number;
  project_id: string;
  kind: MemoryKind;
  title: string;
  body: string;
  files: string[];
  tags: string[];
  /** Who wrote it: the engine from what happened, an agent with `remember`, or the user. */
  source: "engine" | "agent" | "user";
  agent: string;
  run_id: string | null;
  /** Set on entries the engine rewrites from the files, such as the project map. */
  slot: string | null;
  created_at: string;
  updated_at: string;
  superseded_by: number | null;
  uses: number;
  embedded: boolean;
}

export interface BrainLink {
  relation: string;
  /** True when the edge goes from this memory to the other end. */
  outgoing: boolean;
  type: "memory" | "entity";
  kind: string;
  label: string;
  id: string;
  memory_id?: number;
}

export interface BrainRow {
  memory: BrainMemory;
  score: number | null;
  /** Similarity to the question, when the search compared meanings. */
  semantic: number | null;
  keyword: boolean;
  via: string[];
  links: BrainLink[];
  /** The run it was recorded in, while that run is still kept. `chat` is set when the run was a turn of a chat. */
  run: { id: string; chat: string | null } | null;
}

export interface BrainRows {
  rows: BrainRow[];
  searched: boolean;
  semantic: boolean;
  model: string | null;
  total: number;
}

export interface EmbedStatus {
  available: boolean;
  model: string | null;
  reason: string;
  code: "" | "off" | "no_runtime" | "offline" | "no_model" | "failed";
}

export interface BrainOverview {
  project: BrainProject;
  semantic: EmbedStatus;
  /** Memories that have a vector for the embedding model in use. */
  embedded: number;
  codemap: { files: number; imports: number; truncated: boolean; scanned_at: string } | null;
  keyword_index: boolean;
}

export interface BrainGraphNode {
  id: string;
  type: "memory" | "entity";
  kind: string;
  label: string;
  degree: number;
}

export interface BrainGraphEdge {
  source: string;
  target: string;
  relation: string;
  origin: string;
}

export interface BrainGraphData {
  nodes: BrainGraphNode[];
  edges: BrainGraphEdge[];
  total_nodes: number;
  total_edges: number;
}

export interface DoctorCheck {
  name: string;
  status: "PASS" | "WARNING" | "FAIL";
  detail: string;
  hint: string;
}

export interface ToolInfo {
  name: string;
  label: string;
  description: string;
  risk: "low" | "medium" | "high";
  group: string;
}

export interface BusEvent {
  seq: number;
  topic: string;
  ts: number;
  data: Record<string, unknown>;
}

export interface StorageLocation {
  path: string;
  drive: string;
  free_gb: number;
  total_gb: number;
  reserve_gb: number;
  safe_available_gb: number;
  is_lmstudio_dir: boolean;
  recommended: boolean;
  reason: string;
}

// -- Cloud models ----------------------------------------------------------------

export interface CloudModel {
  id: string;
  /** What goes where a model key goes: cloud:<provider>/<model>. */
  key: string;
  label: string;
  context: number;
  recommended: boolean;
  provider?: string;
  provider_label?: string;
  /** Whether the model accepts pictures. */
  vision?: boolean;
}

export interface CloudProvider {
  id: "anthropic" | "openai" | "deepseek" | "gemini";
  label: string;
  configured: boolean;
  /** "saved" (in the app, encrypted) or the environment variable the key came from. */
  source: string | null;
  /** The last four characters of the key, never more. */
  hint: string | null;
  console_url: string;
  env_keys: string[];
  models: CloudModel[];
  ok?: boolean;
  error?: string;
  count?: number;
}

export interface CloudStatus {
  providers: CloudProvider[];
  models: CloudModel[];
  max_context: number;
}

// -- image studio (ComfyUI as the engine) -----------------------------------

export interface ImageVram {
  name: string | null;
  total_gb: number;
  free_gb: number;
  torch_total_gb: number;
}

export interface ImageStatus {
  url: string;
  available: boolean;
  vram: ImageVram | null;
  queue: { running: number; pending: number } | null;
  error: string | null;
  gallery: number;
  engine: ImageEngine;
}

export interface ImageModel {
  key: string;
  name: string;
  family: string;
  kinds: string[];
  vram_gb: number | null;
  licence: string;
  source: string;
  notes: string;
  nsfw: string;
  discovered: boolean;
  components: Record<string, string>;
  requires: string[];
  components_detail: ImageComponent[];
  /** The workflow this model uses when nobody chooses one. */
  template: string;
  /** LoRA components trained for this family, selectable on this model. */
  lora_options: ImageComponent[];
  /** Named configurations: a LoRA to switch on and the sampling that goes with it. */
  presets: ImagePreset[];
  /** Why the model cannot run on this engine; null when it can. */
  blocked: { reason: string; missing?: string[] } | null;
  /** Installed and not blocked. */
  ready: boolean;
  download_bytes: number | null;
  sampling: Record<string, unknown>;
  installed: boolean;
  missing: ImageComponent[];
  template_kinds: string[];
  recommended: boolean;
  /** What the model is told not to draw when the person writes nothing. */
  negative: string;
  /** The shape and size its author recommends. */
  aspect: string;
  megapixels: number | null;
}

export interface ImageTemplate {
  key: string;
  name: string;
  family: string;
  kind: "txt2img" | "edit" | "upscale";
  description: string;
  roles: string[];
  lora_mode: string;
  defaults: Record<string, unknown>;
  needs: string[];
}

export interface ImageCheck {
  ready: boolean;
  engine: boolean;
  missing_files: ImageComponent[];
  missing_nodes: string[];
  problems: string[];
}

export type ImageJobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface ImageLora {
  name: string;
  weight: number;
}

export interface ImageJob {
  id: string;
  status: ImageJobStatus;
  phase: string;
  model_key: string;
  model_name: string;
  template: string;
  template_name: string;
  prompt: string;
  negative: string;
  width: number;
  height: number;
  seed: number;
  loras: ImageLora[];
  batch: number;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  elapsed: number;
  error: string | null;
  hints: ImageWarning[];
  image_id: string | null;
  image_ids: string[];
  prompt_id: string | null;
}

export interface GalleryImage {
  id: string;
  created_at: string;
  model_key: string;
  template: string;
  prompt: string;
  negative: string;
  width: number;
  height: number;
  seed: number;
  steps: number | null;
  cfg: number | null;
  sampler: string | null;
  scheduler: string | null;
  loras: ImageLora[];
  bytes: number;
  elapsed_seconds: number | null;
}

// -- image studio: engine, components, downloads ----------------------------

export interface ImageInstall {
  root: string;
  python: string | null;
  models_dir: string;
  kind: string;
}

export interface ImageEngine {
  installed: boolean;
  running: boolean;
  url: string;
  version: string | null;
  install: ImageInstall | null;
  started_by_us: boolean;
  download_url: string;
  portable_url: string;
  gguf_nodes_url: string;
  log: string;
  already_running?: boolean;
  stopped?: boolean;
}

export type ComponentStatus = "present" | "missing" | "downloading" | "queued" | "verifying" | "failed";

export interface ImageDownload {
  component_id: string;
  status: string;
  received: number;
  total: number | null;
  percent: number | null;
  speed: number;
  error: string;
}

export interface ImageComponent {
  id: string;
  name: string;
  role: string;
  folder: string;
  file: string;
  families: string[];
  vram_gb: number | null;
  licence: string;
  notes: string;
  url: string;
  size_bytes: number | null;
  downloadable: boolean;
  status: ComponentStatus;
  path: string | null;
  installed_size: number | null;
  sha256: string | null;
  error: string | null;
  download: ImageDownload | null;
  used_by: string[];
  target: string;
  /** For a LoRA: its format, trigger word and trained strengths. */
  lora?: {
    format?: string;
    trigger?: string;
    weight?: number;
    weight_clip?: number;
    note?: string;
  };
  /** A Civitai file that cannot be downloaded until the person gives their Civitai token. */
  needs_token?: boolean;
  /** Present, but in a folder this app did not know about: usable, not deletable from here. */
  external?: boolean;
}

export interface ImagePreset {
  key: string;
  name: string;
  loras: { component: string; weight: number; weight_clip?: number }[];
  sampling?: Record<string, number>;
}

export interface ImageLoraCheck {
  lora_tensors: number;
  matched: number;
  unmatched: string[];
  unmatched_count: number;
  compatible: boolean;
  base_tensors: number;
  samples: string[];
  error?: string;
}

export interface ImageImportFile {
  name: string;
  size_bytes: number;
  role: string;
  family: string | null;
  component_id: string | null;
  component_name: string | null;
  reason: string;
  already_visible: boolean;
  target: string | null;
  conflict: string | null;
  known: boolean;
  lora_check: ImageLoraCheck | null;
}

export interface ImageImportModel {
  key: string;
  name: string;
  found: { component_id: string; component_name: string; role: string; size_bytes: number; already_visible: boolean; conflict: string | null; path: string }[];
  missing: string[];
  ready: boolean;
  blocked: { reason: string } | null;
}

export interface ImageImportScan {
  directory: string;
  files: ImageImportFile[];
  models: ImageImportModel[];
  unknown: ImageImportFile[];
  bytes_to_copy: number;
  components_to_import: string[];
  conflicts: ImageImportFile[];
  already_visible: string[];
}

export interface ImageWarning {
  code: string;
  values?: Record<string, number>;
}

export interface ImageEstimate {
  /** null when the memory this PC can give, or what the model needs, is not known. */
  fits: boolean | null;
  needed_gb: number | null;
  usable_gb: number | null;
  headroom_gb: number | null;
  max_megapixels: number | null;
  cost_per_megapixel_gb: number;
  basis: "estimate" | "unknown";
  warnings: ImageWarning[];
  suggestions: ImageWarning[];
}

export interface ImagePlan {
  model: string;
  components: ImageComponent[];
  missing: ImageComponent[];
  download_bytes: number | null;
  problems: string[];
  estimate: ImageEstimate;
  lighter: { key: string; name: string; vram_gb: number | null }[];
}
