import { useRef, useState } from "react";
import { Bookmark, Check, Copy, Save, Trash2 } from "lucide-react";
import { Button, Select, TextInput } from "./ui";
import { api } from "../lib/api";
import { errorMessage } from "../lib/hooks";
import { t } from "../lib/i18n";
import { copyText } from "../lib/masterPrompts";
import { oneLine } from "../lib/video";

export interface VideoPromptPair { prompt: string; negative: string }
export interface SavedVideoPrompt extends VideoPromptPair { id: string; name: string; created_at: number }
const textStyle = "selectable w-full resize-y rounded-md border border-line bg-bg p-3 text-sm leading-relaxed text-ink";

export function PromptText({ label, value, empty, rows = 5 }: { label: string; value: string; empty?: string; rows?: number }) {
  const input = useRef<HTMLTextAreaElement>(null);
  const [message, setMessage] = useState("");
  async function copy() {
    if (await copyText(value)) setMessage(t("Copied"));
    else {
      input.current?.focus(); input.current?.select();
      setMessage(t("Text selected. Press Ctrl+C to copy."));
    }
  }
  return <div className="grid gap-2">
    <div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">{label}</span><Button size="sm" aria-label={t("Copy {label}", { label })} icon={<Copy size={14} />} disabled={!value} onClick={() => void copy()}>{t("Copy")}</Button></div>
    <textarea ref={input} aria-label={label} className={textStyle} rows={rows} value={value} readOnly placeholder={empty} onFocus={() => setMessage("")} />
    {message && <p role="status" className="text-xs text-ink-2">{message}</p>}
  </div>;
}

export function VideoPromptTexts({ prompt, negative }: VideoPromptPair) {
  return <div className="grid gap-3"><PromptText label={t("Prompt")} value={prompt} /><PromptText label={t("Negative prompt")} rows={3} value={negative || ""} empty={t("No negative prompt saved.")} /></div>;
}

export function SaveVideoPrompts({ prompt, negative, onSaved }: VideoPromptPair & { onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [savedPair, setSavedPair] = useState<VideoPromptPair | null>(null);
  const saved = savedPair?.prompt === prompt && savedPair.negative === (negative || "");
  async function save() {
    setBusy(true); setError(""); setSavedPair(null);
    try {
      await api.post<SavedVideoPrompt>("/api/videos/prompts", { name: name.trim(), prompt, negative: negative || "" });
      setOpen(false); setName(""); setSavedPair({ prompt, negative: negative || "" }); onSaved();
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  return <div className="grid gap-2">
    <Button className="justify-self-start" icon={<Bookmark size={15} />} disabled={busy || !prompt.trim()} onClick={() => { setOpen(!open); setSavedPair(null); }}>{t("Save prompts")}</Button>
    {open && <div className="grid gap-2 rounded-lg border border-line bg-bg p-3">
      <label className="grid gap-1 text-sm">{t("Name for these prompts")}<input className="rounded-md border border-line bg-surface p-2" value={name} maxLength={120} disabled={busy} onChange={e => setName(e.target.value)} placeholder={t("For example: graphics card disassembly")} /></label>
      <p className="text-xs text-ink-2">{t("The prompt and negative prompt are saved together on this PC.")}</p>
      <div className="flex gap-2"><Button variant="primary" icon={<Save size={14} />} loading={busy} disabled={!name.trim() || !prompt.trim()} onClick={() => void save()}>{t("Save to prompt library")}</Button><Button disabled={busy} onClick={() => setOpen(false)}>{t("Cancel")}</Button></div>
    </div>}
    {saved && <p role="status" className="flex items-center gap-1 text-sm text-ok"><Check size={14} />{t("Prompts saved.")}</p>}
    {error && <p role="alert" className="text-sm text-bad">{error}</p>}
  </div>;
}

export function SavedVideoPrompts({ items, loading, error, onReload, onUse, disabled }: { items: SavedVideoPrompt[]; loading: boolean; error: string | null; onReload: () => void; onUse: (pair: VideoPromptPair) => void; disabled: boolean }) {
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState("");
  const [deleting, setDeleting] = useState("");
  const [failure, setFailure] = useState("");
  const filtered = items.filter(p => `${p.name}\n${p.prompt}\n${p.negative}`.toLowerCase().includes(query.toLowerCase()));
  const item = filtered.find(p => p.id === chosen) ?? filtered[0];
  async function remove(id: string) {
    setDeleting(id); setFailure("");
    try { await api.del(`/api/videos/prompts/${id}`); setChosen(""); onReload(); }
    catch (err) { setFailure(errorMessage(err)); }
    finally { setDeleting(""); }
  }
  return <div className="grid gap-4">
    <h2 className="flex items-center gap-2 font-semibold"><Bookmark size={18} />{t("Saved video prompts")} <span className="text-ink-3">({items.length})</span></h2>
    <p className="text-sm text-ink-2">{t("Keep your effects ready to use. Loading a saved pair fills both prompt fields.")}</p>
    {(error || failure) && <p role="alert" className="text-sm text-bad">{error || failure}</p>}
    {!items.length && <p className="text-sm text-ink-3">{loading ? t("Loading…") : t("Your saved prompt pairs will appear here.")}</p>}
    {items.length > 0 && <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <label className="grid min-w-0 gap-1 text-xs font-medium text-ink-2">{t("Saved prompts to show")}
        <Select value={item?.id ?? ""} disabled={!filtered.length} onChange={e => setChosen(e.target.value)}>
          {filtered.map(p => <option key={p.id} value={p.id}>{oneLine(p.name, 120)}</option>)}
          {!filtered.length && <option value="">{t("No saved prompts match this search.")}</option>}
        </Select></label>
      <label className="grid min-w-0 gap-1 text-xs font-medium text-ink-2">{t("Search saved prompts")}<TextInput value={query} onChange={e => setQuery(e.target.value)} /></label>
    </div>}
    {item && <div className="grid gap-3 rounded-lg border border-line bg-bg p-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="selectable min-w-0 font-medium">{item.name}</h3>
        <div className="flex flex-wrap gap-2"><Button icon={<Copy size={14} />} disabled={disabled} onClick={() => onUse(item)}>{t("Use these prompts")}</Button><Button size="sm" variant="danger" icon={<Trash2 size={14} />} loading={deleting === item.id} disabled={!!deleting} onClick={() => void remove(item.id)}>{t("Delete saved prompts")}</Button></div></div>
      <VideoPromptTexts key={item.id} prompt={item.prompt} negative={item.negative} />
    </div>}
  </div>;
}
