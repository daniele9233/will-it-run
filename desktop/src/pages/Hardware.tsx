import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Collapsible, ConfidenceTag, KeyValue, Meter, Note, PageHeader, Section, SkeletonRows } from "../components/ui";
import { api } from "../lib/api";
import { UNKNOWN, gbFromBytes, gbFromMb, timeAgo } from "../lib/format";
import { errorMessage, useLoad } from "../lib/hooks";
import { useApp } from "../lib/store";
import type { GpuBudget, GpuInfo, StorageLocation } from "../lib/types";
import { t } from "../lib/i18n";

function GpuBlock({ gpu, budget }: { gpu: GpuInfo; budget: GpuBudget | undefined }) {
  const total = gpu.vram_total_mb / 1024;
  return (
    <div className="border-t border-line py-4 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold tracking-tight">{gpu.name}</h3>
        {gpu.is_integrated ? <Badge tone="off">{t("Integrated · not used for models")}</Badge> : <Badge tone="ok">{gpu.backend.toUpperCase()}</Badge>}
        <ConfidenceTag value={gpu.vram_confidence} />
      </div>
      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-10 gap-y-3">
        <KeyValue
          rows={[
            ["VRAM", gbFromMb(gpu.vram_total_mb)],
            [t("In use now"), gpu.vram_used_mb != null ? gbFromMb(gpu.vram_used_mb) : UNKNOWN],
            [t("Driver"), gpu.driver_version ?? UNKNOWN],
            ["CUDA", gpu.cuda_version ?? (gpu.vendor === "nvidia" ? UNKNOWN : t("Not applicable"))],
            [t("Compute capability"), gpu.compute_capability ?? UNKNOWN],
            [t("Memory bandwidth"), gpu.memory_bandwidth_gbps ? `${gpu.memory_bandwidth_gbps} GB/s` : UNKNOWN],
            [t("Detected by"), gpu.sources.join(", ")],
          ]}
        />
        {budget ? (
          <div className="grid content-start gap-2">
            <div className="flex items-baseline justify-between text-sm">
              <span className="text-ink-2">{t("Recommended AI VRAM")}</span>
              <span className="tabular text-lg font-semibold text-ok">{budget.usable_gb.toFixed(1)} GB</span>
            </div>
            <Meter value={budget.usable_gb / total} tone="ok" height={8} label={t("Recommended AI VRAM")} />
            <dl className="mt-1 grid grid-cols-[1fr_auto] gap-y-1 text-sm">
              <dt className="text-ink-3">{t("GPU VRAM")}</dt>
              <dd className="tabular text-right">{total.toFixed(1)} GB</dd>
              <dt className="text-ink-3">{t("Windows / desktop reserve")}</dt>
              <dd className="tabular text-right">− {budget.os_reserve_gb.toFixed(1)} GB</dd>
              <dt className="text-ink-3">{t("Will It Run safety reserve")}</dt>
              <dd className="tabular text-right">− {budget.safety_reserve_gb.toFixed(1)} GB</dd>
              <dt className="border-t border-line pt-1 text-ink-2">{t("Recommended usable")}</dt>
              <dd className="tabular border-t border-line pt-1 text-right font-medium">{budget.usable_gb.toFixed(1)} GB</dd>
            </dl>
          </div>
        ) : (
          <p className="text-sm text-ink-3">
            {gpu.is_integrated
              ? t("Shares system memory (up to {size}). Will It Run plans CPU inference instead, which is more predictable.", { size: gbFromMb(gpu.shared_memory_mb, 0) })
              : t("Not usable for inference.")}
          </p>
        )}
      </div>
    </div>
  );
}

export function HardwarePage() {
  const hardware = useApp((s) => s.hardware);
  const loadHardware = useApp((s) => s.loadHardware);
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const [scanning, setScanning] = useState(false);
  const storage = useLoad(() => api.get<{ locations: StorageLocation[] }>("/api/storage"), [hardware?.profile.detected_at]);

  const rescan = () => {
    setScanning(true);
    loadHardware(true)
      .then(() => toast({ tone: "ok", title: t("Hardware rescanned") }))
      .catch((err: unknown) => toast({ tone: "bad", title: t("Rescan failed"), body: errorMessage(err) }))
      .finally(() => setScanning(false));
  };

  if (!hardware) return <div className="p-7"><SkeletonRows rows={5} height={70} /></div>;
  const { profile, budget } = hardware;
  const features = Object.entries(profile.cpu.features);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={t("Hardware")}
        subtitle={`${profile.os.caption} · build ${profile.os.build} · ${t("scanned {when}", { when: timeAgo(profile.detected_at) })}`}
        actions={
          <Button icon={<RefreshCw size={13} />} loading={scanning} onClick={rescan}>
            {t("Rescan")}
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
        <div className="grid max-w-[980px] gap-9">
          {profile.warnings.map((warning) => (
            <Note key={warning} tone="warn">{warning}</Note>
          ))}

          <Section title="GPU" aside={<ConfidenceTag value={profile.confidence.gpu ?? "UNKNOWN"} />}>
            {profile.gpus.length === 0 && <p className="text-sm text-ink-2">{t("No GPU detected. Models will run on the CPU.")}</p>}
            {profile.gpus.map((gpu) => (
              <GpuBlock key={gpu.index} gpu={gpu} budget={budget.gpus.find((b) => b.index === gpu.index)} />
            ))}
            {budget.split_supported && (
              <div className="mt-3">
                <Note>
                  {t("One model can be split across your {n} GPUs for about {split} GB, less than the raw sum. Runtimes that cannot split a model are limited to the largest single card ({single} GB).", { n: budget.gpus.length, split: budget.split_usable_gb.toFixed(1), single: budget.single_gpu_usable_gb.toFixed(1) })}
                </Note>
              </div>
            )}
          </Section>

          <div className="grid grid-cols-2 gap-10">
            <Section title="CPU" aside={<ConfidenceTag value={profile.confidence.cpu ?? "UNKNOWN"} />}>
              <KeyValue
                rows={[
                  [t("Model"), profile.cpu.model],
                  [t("Manufacturer"), profile.cpu.manufacturer],
                  [t("Architecture"), profile.cpu.architecture],
                  [t("Cores"), t("{physical} physical / {logical} logical", { physical: profile.cpu.physical_cores, logical: profile.cpu.logical_cores })],
                  [t("Max frequency"), profile.cpu.max_clock_mhz ? `${(profile.cpu.max_clock_mhz / 1000).toFixed(1)} GHz` : UNKNOWN],
                  [
                    t("Instruction sets"),
                    <span key="f" className="flex flex-wrap gap-1.5">
                      {features.map(([name, present]) => (
                        <Badge key={name} tone={present ? "ok" : present === false ? "off" : "warn"} title={present === null ? t("Could not be determined") : undefined}>
                          {name.toUpperCase().replace("_", ".")}
                          {present === false ? ` ${t("absent")}` : present === null ? " ?" : ""}
                        </Badge>
                      ))}
                    </span>,
                  ],
                ]}
              />
            </Section>
            <Section title="RAM" aside={<ConfidenceTag value={profile.confidence.ram ?? "UNKNOWN"} />}>
              <KeyValue
                rows={[
                  [t("Installed"), profile.ram.installed_bytes ? gbFromBytes(profile.ram.installed_bytes, 0) : UNKNOWN],
                  [t("Visible to Windows"), gbFromBytes(profile.ram.total_bytes)],
                  [t("Available at scan"), gbFromBytes(profile.ram.available_bytes)],
                  [t("Recommended usable"), <span key="u" className="font-medium text-ok">{budget.ram_usable_gb.toFixed(1)} GB</span>],
                  [t("Type"), profile.ram.memory_type ?? UNKNOWN],
                  [t("Speed"), profile.ram.speed_mhz ? `${profile.ram.speed_mhz} MT/s` : UNKNOWN],
                  [t("Modules"), profile.ram.modules.length ? `${profile.ram.modules.length} × ${gbFromBytes(profile.ram.modules[0]?.capacity_bytes ?? 0, 0)}` : UNKNOWN],
                ]}
              />
            </Section>
          </div>

          <Section title={t("Storage")} aside={t("A safety reserve stays free on every drive.")}>
            {storage.loading && !storage.data && <SkeletonRows rows={2} height={56} />}
            {storage.error && <Note tone="bad">{storage.error}</Note>}
            <ul className="border-t border-line">
              {storage.data?.locations.map((loc) => {
                const drive = profile.drives.find((d) => d.letter === loc.drive);
                return (
                  <li key={loc.drive} className="grid grid-cols-[72px_minmax(0,1fr)_auto] items-center gap-5 border-b border-line py-3">
                    <div>
                      <div className="font-mono text-lg font-semibold">{loc.drive}</div>
                      <div className="text-2xs text-ink-3">{[drive?.media_type, drive?.bus_type, drive?.filesystem].filter(Boolean).join(" · ")}</div>
                    </div>
                    <div className="grid gap-1.5">
                      <div className="flex justify-between text-sm">
                        <span className="selectable truncate font-mono text-xs text-ink-2">{loc.path}</span>
                        <span className="tabular shrink-0">
                          {t("{free} GB free of {total} GB", { free: loc.free_gb.toFixed(0), total: loc.total_gb.toFixed(0) })}
                        </span>
                      </div>
                      <Meter value={1 - loc.free_gb / loc.total_gb} tone={loc.safe_available_gb <= 0 ? "bad" : loc.safe_available_gb < 20 ? "limited" : "off"} limit={1 - loc.reserve_gb / loc.total_gb} label={`${loc.drive} used`} />
                      <div className="text-xs text-ink-3">
                        {loc.safe_available_gb > 0 ? t("{n} GB safely usable for models", { n: loc.safe_available_gb.toFixed(0) }) : t("No safe space for models: below the reserve")}
                        {loc.recommended && loc.reason ? ` · ${loc.reason}` : ""}
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      {loc.recommended && <Badge tone="ok">{t("Recommended for models")}</Badge>}
                      {loc.is_lmstudio_dir && <Badge tone="accent">{t("LM Studio folder")}</Badge>}
                    </div>
                  </li>
                );
              })}
            </ul>
          </Section>

          <div>
            <Collapsible title={t("How these numbers are calculated")}>
              <ul className="selectable grid gap-1.5 text-sm text-ink-2">
                {budget.explanation.map((line) => (
                  <li key={line}>{line}</li>
                ))}
                <li>
                  {t("Speed estimates use {gpu} and {ram} GB/s for system RAM.", { gpu: budget.gpu_bandwidth_gbps ? t("the GPU's {n} GB/s memory bandwidth", { n: budget.gpu_bandwidth_gbps }) : t("a per-backend constant, because this GPU's bandwidth is not in the table"), ram: budget.ddr_bandwidth_gbps.toFixed(0) })}
                </li>
              </ul>
              <button type="button" className="mt-3 text-sm text-accent hover:underline" onClick={() => navigate("settings", { tab: "hardware" })}>
                {t("Adjust reserves in Settings")}
              </button>
            </Collapsible>
          </div>
        </div>
      </div>
    </div>
  );
}
