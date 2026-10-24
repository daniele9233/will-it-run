import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";

import { Button, EmptyState, Note, PageHeader, Segmented, SkeletonRows, TextInput, cx } from "../components/ui";
import { api } from "../lib/api";
import { useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";

interface LogEntry {
  ts?: string;
  level?: string;
  logger?: string;
  msg?: string;
  data?: Record<string, unknown>;
  exc?: string;
}

type Level = "all" | "WARNING" | "ERROR";

export function LogsPage() {
  const logs = useLoad(() => api.get<{ entries: LogEntry[] }>("/api/logs?lines=500"));
  const [level, setLevel] = useState<Level>("all");
  const [text, setText] = useState("");

  const entries = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return (logs.data?.entries ?? [])
      .filter((e) => level === "all" || e.level === level || (level === "WARNING" && e.level === "ERROR"))
      .filter((e) => !needle || JSON.stringify(e).toLowerCase().includes(needle))
      .reverse();
  }, [logs.data, level, text]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={t("Logs")}
        subtitle={t("What Will It Run did, newest first. Passwords, tokens, API keys, cookies and private keys are redacted before anything is written.")}
        actions={
          <Button icon={<RefreshCw size={13} />} loading={logs.loading} onClick={logs.reload}>
            {t("Refresh")}
          </Button>
        }
      />
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-7 py-3">
        <TextInput value={text} onChange={(e) => setText(e.target.value)} placeholder={t("Filter logs")} aria-label={t("Filter logs")} className="max-w-xs" />
        <Segmented
          label={t("Level")}
          value={level}
          onChange={setLevel}
          options={[
            { value: "all", label: t("All") },
            { value: "WARNING", label: t("Warnings") },
            { value: "ERROR", label: t("Errors") },
          ]}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-3">
        {logs.loading && !logs.data && <SkeletonRows rows={10} height={22} />}
        {logs.error && <Note tone="bad">{logs.error}</Note>}
        {logs.data && entries.length === 0 && <EmptyState title={t("Nothing logged yet")}>{t("Activity such as hardware scans, model loads, downloads and workflow runs appears here.")}</EmptyState>}
        <ul className="selectable font-mono text-xs leading-relaxed">
          {entries.map((entry, i) => (
            <li key={i} className="grid grid-cols-[150px_64px_110px_1fr] gap-3 border-b border-line/60 py-1">
              <span className="text-ink-3">{entry.ts ? entry.ts.replace("T", " ").slice(0, 19) : ""}</span>
              <span className={cx(entry.level === "ERROR" ? "text-bad" : entry.level === "WARNING" ? "text-warn" : "text-ink-3")}>{entry.level}</span>
              <span className="truncate text-ink-3">{entry.logger?.replace("agentfit.", "")}</span>
              <span className="min-w-0 break-words text-ink-2">
                {entry.msg}
                {entry.data && <span className="text-ink-3"> {JSON.stringify(entry.data)}</span>}
                {entry.exc && <pre className="mt-1 whitespace-pre-wrap text-bad">{entry.exc}</pre>}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
