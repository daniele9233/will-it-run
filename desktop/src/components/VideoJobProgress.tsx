import { t } from "../lib/i18n";

export interface VideoJob {
  id: string;
  status: string;
  stage: string;
  percent: number | null;
  step: number | null;
  total: number | null;
  elapsed: number;
  error: string | null;
  unit?: "frames";
}

export function VideoJobProgress({ job }: { job: VideoJob }) {
  const active = job.status === "queued" || job.status === "running";
  const phases: Record<string, string> = {
    queued: t("Queued"), generation: t("Generating video."), loading: t("Loading models."),
    sampling: t("Generating video frames."), decoding: t("Decoding video frames."),
    encoding: t("Encoding video."), saving: t("Saving video."),
    finishing: t("Interpolating motion and preparing Full HD."),
    verifying: t("Checking resolution, frame rate and distinct frames."),
    inspecting: t("Inspecting the source video."), preparing: t("Preparing video frames."),
    upscaling: t("Upscaling with RelayVSR."),
  };
  const label = active ? phases[job.stage] || t("Generating video.")
    : job.status === "done" ? t("Completed") : job.status === "cancelled" ? t("Cancelled") : t("Failed");
  return <div role="status" className="grid gap-2 text-sm">
    <p>{label} · {job.percent != null && <>{job.percent}% · </>}{Math.round(job.elapsed)}s
      {active && job.step != null && !!job.total && <span> · {job.unit === "frames" ? t("Frame") : t("Step")} {job.step}/{job.total}</span>}
    </p>
    <progress aria-label={t("Video task progress")} max={100} value={job.percent ?? undefined} className="h-2 w-full accent-accent" />
    {active && <p className="text-xs text-ink-3">{job.percent == null
      ? t("Waiting for engine progress. Elapsed time is shown; remaining time is unknown.")
      : t("Progress follows generation stages and sampling steps. Final decoding and export may take longer; 100% means the file is ready.")}</p>}
  </div>;
}
