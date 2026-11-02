import { ExternalLink, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Note } from "../../components/ui";
import { ApiError, api, openExternal } from "../../lib/api";
import type { CloneRun } from "../../lib/cloneSite";
import { dnaFetchProblem } from "../../lib/dna";
import { errorMessage } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import { gapLines, matchLines, mirrorFailure, motionLines, pixelLines, type MirrorReport } from "../../lib/mirror";
import { useApp } from "../../lib/store";

function Lines({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="grid gap-1">
      <span className="text-2xs text-ink-3">{title}</span>
      <ul className="grid gap-0.5 text-xs text-ink-2">
        {lines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A mirror clone: the thumbnail, the report of what the replay was measured to do, the warning about the recording, and the buttons that
 * open the replay and record again. The stages above it are the page's own pipeline.
 */
export function MirrorView({ cloneId, run, working, thumbnail, onAgain }: { cloneId: string; run: CloneRun | null; working: boolean; thumbnail: string | null; onAgain: () => void }) {
  const toast = useApp((s) => s.toast);
  const [report, setReport] = useState<MirrorReport | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState<"open" | "again" | null>(null);
  const finished = run !== null && !working;

  // The report is on disk once the run ended; until then (and for a run that never got there) the route answers 404 and there is nothing to show.
  useEffect(() => {
    if (!finished) {
      setReport(null);
      setProblem(null);
      return;
    }
    api
      .get<MirrorReport>(`/api/clone/${cloneId}/mirror`)
      .then((data) => {
        setReport(data);
        setProblem(null);
      })
      .catch((err: unknown) => {
        setReport(null);
        setProblem(dnaFetchProblem(err, err instanceof ApiError ? err.status : undefined));
      });
  }, [cloneId, finished, run?.id]);

  const open = () => {
    setBusy("open");
    api
      .post<{ url: string }>(`/api/clone/${cloneId}/mirror/open`)
      .then((data) => openExternal(data.url))
      .catch((err: unknown) => toast({ tone: "bad", title: t("The mirror could not be opened"), body: errorMessage(err) }))
      .finally(() => setBusy(null));
  };
  const again = () => {
    setBusy("again");
    api
      .post(`/api/clone/${cloneId}/mirror/run`)
      .then(onAgain)
      .catch((err: unknown) => toast({ tone: "bad", title: t("The mirror could not start"), body: errorMessage(err) }))
      .finally(() => setBusy(null));
  };

  const failed = run !== null && !working && run.status !== "completed";
  return (
    <div className="grid gap-4">
      {working && <p className="text-xs text-ink-3">{t("The page is being recorded and then played back. This takes a few minutes.")}</p>}
      {failed && <Note tone="bad">{mirrorFailure(run)}</Note>}
      {problem && <Note tone="warn">{t("There is no report to show. {reason}", { reason: problem })}</Note>}
      <Note tone="warn">{t("A site's text, logos, pictures, videos and code belong to its owner. The recording stays on this PC; do not publish it.")}</Note>
      <div className="flex flex-wrap items-center gap-2">
        {!working && !failed && (
          <Button size="sm" variant="primary" icon={<ExternalLink size={13} />} loading={busy === "open"} onClick={open}>{t("Open the mirror")}</Button>
        )}
        {!working && <Button size="sm" icon={<RotateCcw size={13} />} loading={busy === "again"} onClick={again}>{t("Run again")}</Button>}
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        {thumbnail && <img src={thumbnail} alt="" className="block max-h-[360px] w-full rounded-md border border-line object-cover object-top" />}
        {report && (
          <section className="clone-card grid content-start gap-3 p-4" aria-label={t("Report")}>
            <div className="clone-label">{t("Report")}</div>
            <Lines title={t("Site DNA match")} lines={matchLines(report)} />
            <Lines title={t("Motion")} lines={motionLines(report)} />
            <Lines title={t("Screenshots along the page")} lines={pixelLines(report)} />
            <Lines title={t("Gaps and what is not covered")} lines={gapLines(report)} />
          </section>
        )}
      </div>
    </div>
  );
}
