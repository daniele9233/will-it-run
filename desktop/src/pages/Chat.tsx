import { FolderGit2, FolderOpen, MessageSquarePlus, Square, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { CloudModelsLink, ModelOptions } from "../components/ModelOptions";
import { RunComposer } from "../components/RunComposer";
import { Button, EmptyState, IconButton, Note, Select, SkeletonRows, StatusDot, cx } from "../components/ui";
import { api } from "../lib/api";
import { retryText, timeAgo } from "../lib/format";
import { errorMessage, useBus, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { useModelChoices } from "../lib/models";
import { repoName, sourceFromEvent, sourcePayload, type ApprovalMode, type RunSource } from "../lib/runSource";
import { useApp } from "../lib/store";
import type { ChatDetail, ChatInfo, ChatTurn, CloudProvider, InstalledModel, PendingApproval, RunEvent } from "../lib/types";
import { ApprovalCard, LiveBlock, RecalledLine, SavedLine, SwitchLine, ToolCard, type LiveText } from "./Sessions";

const TERMINAL = new Set(["completed", "failed", "rejected", "cancelled", "retry_limit", "no_route", "blocked"]);
const MODEL_KEY = "agentfit.chat.model";

function rememberedModel(): string {
  try {
    return window.localStorage.getItem(MODEL_KEY) ?? "";
  } catch {
    return "";
  }
}

function sizeLabel(model: InstalledModel): string {
  const parts = [model.display_name, model.quant, model.size_bytes ? `${(model.size_bytes / 1024 ** 3).toFixed(1)} GB` : null];
  return parts.filter(Boolean).join(" · ");
}

type TurnItemKind = "tool" | "retry" | "switch" | "failed" | "recalled" | "saved";

/** The tool calls of a turn, each with its running and finished event, plus the lines between them. */
function turnItems(events: RunEvent[]): { key: string; kind: TurnItemKind; events: RunEvent[] }[] {
  const items: { key: string; kind: TurnItemKind; events: RunEvent[] }[] = [];
  for (const event of events) {
    if (event.type === "tool.call") {
      const last = items[items.length - 1];
      if (last && last.kind === "tool" && last.events[0]?.payload.tool === event.payload.tool && last.events[last.events.length - 1]?.payload.status === "running") {
        last.events.push(event);
      } else {
        items.push({ key: `t${event.seq}`, kind: "tool", events: [event] });
      }
    } else if (event.type === "node.retry") {
      items.push({ key: `r${event.seq}`, kind: "retry", events: [event] });
    } else if (event.type === "model.switch") {
      items.push({ key: `s${event.seq}`, kind: "switch", events: [event] });
    } else if (event.type === "memory.recalled") {
      items.push({ key: `b${event.seq}`, kind: "recalled", events: [event] });
    } else if (event.type === "memory.saved") {
      items.push({ key: `w${event.seq}`, kind: "saved", events: [event] });
    }
  }
  return items;
}

export function Turn({
  turn, stream, onDecide,
}: {
  turn: ChatTurn;
  stream?: LiveText;
  onDecide: (approval: PendingApproval, approved: boolean, comment: string) => void;
}) {
  const working = !TERMINAL.has(turn.status);
  const items = useMemo(() => turnItems(turn.events), [turn.events]);
  const writing = working && stream && stream.content.trim().length > 0;
  return (
    <div className="grid gap-3">
      <div className="selectable max-w-[82%] justify-self-end rounded-2xl bg-raised px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap">{turn.message}</div>
      <div className="grid min-w-0 gap-2">
        {items.length > 0 && (
          <ol className="grid gap-2">
            {items.map((item) =>
              item.kind === "tool" ? (
                <li key={item.key}>
                  <ToolCard events={item.events} />
                </li>
              ) : item.kind === "switch" ? (
                <SwitchLine key={item.key} event={item.events[0] as RunEvent} />
              ) : item.kind === "recalled" ? (
                <li key={item.key}>
                  <RecalledLine event={item.events[0] as RunEvent} />
                </li>
              ) : item.kind === "saved" ? (
                <SavedLine key={item.key} event={item.events[0] as RunEvent} />
              ) : (
                <li key={item.key} className="text-xs text-warn" title={String(item.events[0]?.payload.reason ?? "")}>
                  {retryText(item.events[0]?.payload ?? {})}
                </li>
              ),
            )}
          </ol>
        )}
        {turn.pending_approvals.length > 0 && (
          <ol>
            {turn.pending_approvals.map((approval) => (
              <ApprovalCard key={approval.id} approval={approval} onDecide={(approved, comment) => onDecide(approval, approved, comment)} />
            ))}
          </ol>
        )}
        {working && stream && !writing && (stream.reasoning || stream.content) && <LiveBlock stream={stream} />}
        {working && !stream && turn.pending_approvals.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-ink-3">
            <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
            {t("Working")}
          </div>
        )}
        {writing && (
          <div className="grid gap-1">
            <div className="selectable text-sm leading-relaxed whitespace-pre-wrap">{stream.content}</div>
            <div className="tabular text-2xs text-ink-3">
              {stream.tokensPerSecond != null ? `${Math.round(stream.tokensPerSecond)} tok/s · ` : ""}
              {t("{n} tokens", { n: stream.tokens.toLocaleString() })}
            </div>
          </div>
        )}
        {!working && turn.answer && <div className="selectable text-sm leading-relaxed whitespace-pre-wrap">{turn.answer}</div>}
        {turn.status === "cancelled" && <div className="text-xs text-ink-3">{t("Stopped by you.")}</div>}
        {turn.status === "failed" && <Note tone="bad">{turn.error ?? t("The model gave no answer.")}</Note>}
      </div>
    </div>
  );
}

/**
 * What the model answering a turn is writing right now, from the live events of that turn's run.
 * `onEvent` is called with the type of every other event of the run: a tool call, the end.
 */
export function useAnswerStream(workingId: string | null, onEvent: (type: string) => void): LiveText | null {
  const [stream, setStream] = useState<LiveText | null>(null);
  // A new turn starts from nothing.
  useEffect(() => setStream(null), [workingId]);
  useBus(workingId ? `run.${workingId}` : "run.none", (event) => {
    const data = event.data as { type?: string; payload?: Record<string, unknown> };
    if (data.type !== "node.stream") {
      onEvent(data.type ?? "");
      return;
    }
    const p = data.payload ?? {};
    setStream((before) => {
      const current = before ?? { reasoning: "", content: "", tokens: 0, tokensPerSecond: null };
      // A count that went back means the model started a new call: its text starts again.
      const fresh = typeof p.tokens === "number" && p.tokens < current.tokens;
      return {
        reasoning: ((fresh ? "" : current.reasoning) + String(p.reasoning ?? "")).slice(-12000),
        content: ((fresh ? "" : current.content) + String(p.content ?? "")).slice(-40000),
        tokens: typeof p.tokens === "number" ? p.tokens : current.tokens,
        tokensPerSecond: typeof p.tokens_per_second === "number" ? p.tokens_per_second : current.tokensPerSecond,
      };
    });
  });
  return stream;
}

function ChatView({ chatId, models, providers, onChanged }: { chatId: string | null; models: InstalledModel[]; providers: CloudProvider[]; onChanged: () => void }) {
  const toast = useApp((s) => s.toast);
  const navigate = useApp((s) => s.navigate);
  const [chat, setChat] = useState<ChatDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState(rememberedModel);
  const bottom = useRef<HTMLDivElement>(null);

  const refresh = () => {
    if (!chatId) return;
    api
      .get<ChatDetail>(`/api/chats/${chatId}`)
      .then((data) => {
        setChat(data);
        setError(null);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
  };
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId]);

  const turns = chat?.turns ?? [];
  const working = turns.find((turn) => !TERMINAL.has(turn.status)) ?? null;
  const workingId = working?.id ?? null;
  useInterval(refresh, working ? 2000 : null);

  // Text as the model writes it.
  const stream = useAnswerStream(workingId, (type) => {
    if (type === "run.followed" || type === "run.completed") onChanged();
    refresh();
  });

  // The model of this chat, else the one used last, else the first one there is.
  const chosen = models.find((m) => m.model_key === model) ?? models.find((m) => m.model_key === chat?.model_key) ?? models[0] ?? null;
  useEffect(() => {
    if (chat?.model_key && models.some((m) => m.model_key === chat.model_key)) setModel(chat.model_key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat?.id]);
  const pickModel = (key: string) => {
    setModel(key);
    try {
      window.localStorage.setItem(MODEL_KEY, key);
    } catch {
      // Not remembered this time.
    }
  };

  const count = turns.length;
  const written = stream?.tokens ?? 0;
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [count, written, working?.status]);

  const send = (message: string, source: RunSource, approval: ApprovalMode) => {
    if (!chosen) return;
    setBusy(true);
    const body = { message, model_key: chosen.model_key, approval, queue: working !== null, ...sourcePayload(source) };
    api
      .post<{ chat: string; run?: string; queued?: boolean }>(chatId ? `/api/chats/${chatId}/messages` : "/api/chats", body)
      .then((data) => {
        onChanged();
        if (!chatId) navigate("chat", { chat: data.chat });
        else refresh();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not send the message"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };
  const stop = () => {
    if (workingId) void api.post(`/api/runs/${workingId}/cancel`).then(refresh);
  };
  const unqueue = () => {
    if (workingId) void api.del(`/api/runs/${workingId}/queued`).then(refresh);
  };
  const decide = (runId: string, approval: PendingApproval, approved: boolean, comment: string) => {
    api
      .post(`/api/runs/${runId}/approvals/${approval.id}`, { approved, comment })
      .then(refresh)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Decision not recorded"), body: errorMessage(err) }));
  };

  if (chatId && error && !chat) return <div className="p-7"><Note tone="bad">{error}</Note></div>;
  if (chatId && !chat) return <div className="flex-1 p-7"><SkeletonRows rows={4} height={56} /></div>;

  const source = chat ? sourceFromEvent({ source: chat.source, protect_existing: true }) : undefined;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line px-6 py-2.5">
        <div className="min-w-0">
          <h1 className="truncate text-base font-semibold tracking-tight">{chat ? chat.title : t("New chat")}</h1>
          {source && source.kind !== "none" && (
            <p className="selectable flex items-center gap-1.5 truncate font-mono text-xs text-ink-3">
              {source.kind === "github" ? <FolderGit2 size={12} aria-hidden /> : <FolderOpen size={12} aria-hidden />}
              {source.kind === "github" ? `github.com/${repoName(source.url)}${source.branch ? ` (${source.branch})` : ""}` : source.path}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <label htmlFor="chat-model" className="text-xs text-ink-3">
            {t("Model")}
          </label>
          <Select id="chat-model" value={chosen?.model_key ?? ""} onChange={(event) => pickModel(event.target.value)} className="h-8 max-w-[340px] text-xs" disabled={models.length === 0}>
            <ModelOptions models={models} providers={providers} label={sizeLabel} />
          </Select>
          <CloudModelsLink models={models} />
          {working && (
            <Button size="sm" variant="danger" icon={<Square size={11} />} onClick={stop}>
              {t("Stop")}
            </Button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {turns.length === 0 ? (
          <div className="mx-auto max-w-[620px] px-6 pt-16">
            <h2 className="text-xl font-semibold tracking-tight">{t("Chat with one model")}</h2>
            <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-ink-2">
              {t("One model answers, with no team around it. Pick the model above and write below. With the + you give it a folder on this PC or one of your GitHub repositories to read and work in.")}
            </p>
            {models.length === 0 && (
              <div className="mt-4">
                <Note tone="warn" action={<Button size="sm" onClick={() => navigate("models")}>{t("Explore Models")}</Button>}>
                  {t("No model is installed in LM Studio yet. Download one first.")}
                </Note>
              </div>
            )}
          </div>
        ) : (
          <div className="mx-auto grid w-full max-w-[860px] gap-7 px-6 py-6">
            {turns.map((turn) => (
              <Turn key={turn.id} turn={turn} stream={turn.id === workingId ? (stream ?? undefined) : undefined} onDecide={(approval, approved, comment) => decide(turn.id, approval, approved, comment)} />
            ))}
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="shrink-0 border-t border-line bg-panel px-4 pt-3 pb-2.5">
        <RunComposer
          key={chatId ?? "new"}
          busy={busy}
          initialSource={source}
          initialApproval={chat?.approval === "bypass" ? "bypass" : undefined}
          placeholder={chosen ? t("Write to {model}", { model: chosen.display_name }) : t("Install a model to chat")}
          running={working !== null}
          queued={working?.queued ?? null}
          onUnqueue={unqueue}
          onStop={stop}
          clearOnSend
          onStart={send}
        />
      </div>
    </div>
  );
}

/**
 * The questions asked about a session's work once it ended, under its result. Each is answered
 * by one model in the place the team worked; the team does not run again.
 */
export function SessionQuestions({ runId, version }: { runId: string; version: number }) {
  const toast = useApp((s) => s.toast);
  const [chat, setChat] = useState<ChatDetail | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const refresh = () => {
    api
      .get<{ chat: ChatDetail | null }>(`/api/runs/${runId}/questions`)
      .then((data) => setChat(data.chat))
      .catch(() => undefined);
  };
  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, version]);

  const turns = chat?.turns ?? [];
  const working = turns.find((turn) => !TERMINAL.has(turn.status)) ?? null;
  const workingId = working?.id ?? null;
  useInterval(refresh, working ? 2000 : null);
  const stream = useAnswerStream(workingId, refresh);
  const written = stream?.tokens ?? 0;
  useEffect(() => {
    if (turns.length > 0) bottom.current?.scrollIntoView({ block: "end" });
  }, [turns.length, written]);

  if (!chat || turns.length === 0) return null;
  const decide = (turnId: string, approval: PendingApproval, approved: boolean, comment: string) => {
    api
      .post(`/api/runs/${turnId}/approvals/${approval.id}`, { approved, comment })
      .then(refresh)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Decision not recorded"), body: errorMessage(err) }));
  };
  const stop = () => {
    if (workingId) void api.post(`/api/runs/${workingId}/cancel`).then(refresh);
  };
  return (
    <section aria-label={t("Questions about this work")} className="mt-6 grid max-w-[860px] gap-6 border-t border-line pt-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm text-ink-2">{t("Questions about this work")}</span>
        <span className="flex items-center gap-2 text-xs text-ink-3">
          {t("answered by {model}, without running the team again", { model: chat.model_key })}
          {working && (
            <Button size="sm" variant="danger" icon={<Square size={11} />} onClick={stop}>
              {t("Stop")}
            </Button>
          )}
        </span>
      </div>
      {turns.map((turn) => (
        <Turn key={turn.id} turn={turn} stream={turn.id === workingId ? (stream ?? undefined) : undefined} onDecide={(approval, approved, comment) => decide(turn.id, approval, approved, comment)} />
      ))}
      <div ref={bottom} />
    </section>
  );
}

export function ChatPage() {
  const routeChat = useApp((s) => s.params.chat);
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const chats = useLoad(() => api.get<{ chats: ChatInfo[] }>("/api/chats"));
  useInterval(chats.reload, 5000);
  // Local models, then the cloud ones that have a key (Settings > Cloud models), spelled like local ones.
  const { installed, all: models, providers } = useModelChoices();
  const list = chats.data?.chats ?? [];

  const remove = (id: string) => {
    api
      .del(`/api/chats/${id}`)
      .then(() => {
        if (id === routeChat) navigate("chat");
        chats.reload();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not delete"), body: errorMessage(err) }));
  };

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex w-[250px] shrink-0 flex-col border-r border-line bg-panel">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
          <span className="text-sm font-semibold">{t("Chat")}</span>
          <Button size="sm" icon={<MessageSquarePlus size={13} />} onClick={() => navigate("chat")}>
            {t("New")}
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {chats.loading && !chats.data && <SkeletonRows rows={4} height={40} />}
          {chats.data && list.length === 0 && <p className="px-2 py-3 text-xs text-ink-3">{t("No chats yet. Each conversation with a model appears here.")}</p>}
          <ul className="grid gap-px">
            {list.map((chat) => {
              const active = chat.id === routeChat;
              return (
                <li key={chat.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => navigate("chat", { chat: chat.id })}
                    aria-current={active ? "true" : undefined}
                    className={cx("grid w-full grid-cols-[10px_1fr] items-baseline gap-2 rounded-md px-2 py-1.5 text-left transition-colors duration-150", active ? "bg-raised" : "hover:bg-surface")}
                  >
                    <span className="pt-1">
                      <StatusDot tone={chat.active ? "accent" : "off"} pulse={chat.active} />
                    </span>
                    <span className="min-w-0">
                      <span className={cx("block truncate text-sm", active ? "text-ink" : "text-ink-2")}>{chat.title}</span>
                      <span className="block truncate text-2xs text-ink-3">
                        {chat.active ? t("Answering") : timeAgo(chat.updated_at)} · {chat.model_key}
                      </span>
                    </span>
                  </button>
                  {!chat.active && (
                    <IconButton label={t("Delete {name}", { name: chat.title })} onClick={() => remove(chat.id)} className="absolute top-1.5 right-1 opacity-0 group-hover:opacity-100 focus:opacity-100">
                      <Trash2 size={13} />
                    </IconButton>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      </div>
      {installed.loading && !installed.data ? (
        <div className="flex-1 p-7">
          <SkeletonRows rows={4} height={56} />
        </div>
      ) : installed.error && !installed.data && models.length === 0 ? (
        <div className="flex-1 px-8">
          <EmptyState title={t("LM Studio is not reachable")}>{installed.error}</EmptyState>
        </div>
      ) : (
        <ChatView key={routeChat ?? "new"} chatId={routeChat ?? null} models={models} providers={providers} onChanged={chats.reload} />
      )}
    </div>
  );
}
