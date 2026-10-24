import { ExternalLink, KeyRound, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { api, openExternal } from "../lib/api";
import { errorMessage, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { useApp } from "../lib/store";
import type { CloudProvider, CloudStatus } from "../lib/types";
import { Badge, Button, IconButton, TextInput, cx } from "./ui";

const BLURB: Record<string, string> = {
  anthropic: t("Claude Opus, Sonnet and Haiku. Strong at long tool work and careful reading."),
  openai: t("GPT models from OpenAI, the ones behind ChatGPT."),
  deepseek: t("DeepSeek V4: very low prices, thinks before it answers."),
  gemini: t("Google's Gemini models, through their OpenAI-compatible API."),
};

function ProviderRow({ provider, onChanged }: { provider: CloudProvider; onChanged: (p: CloudProvider) => void }) {
  const toast = useApp((s) => s.toast);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const done = (p: CloudProvider) => {
    setError(p.ok === false ? (p.error ?? t("The key did not work.")) : null);
    onChanged(p);
  };
  const save = () => {
    if (!key.trim()) return;
    setBusy("save");
    api
      .put<CloudProvider>(`/api/cloud/${provider.id}/key`, { key: key.trim() })
      .then((p) => {
        setKey("");
        done(p);
        if (p.ok) toast({ tone: "ok", title: t("{name} is ready", { name: provider.label }), body: t("{n} models available.", { n: p.count ?? p.models.length }) });
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(null));
  };
  const test = () => {
    setBusy("test");
    api
      .post<CloudProvider>(`/api/cloud/${provider.id}/test`)
      .then(done)
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(null));
  };
  const remove = () => {
    setBusy("delete");
    api
      .del<CloudProvider>(`/api/cloud/${provider.id}/key`)
      .then(done)
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(null));
  };

  const source = provider.source === "saved" ? t("saved in Will It Run, encrypted for your Windows account") : provider.source ? t("from the environment variable {name}", { name: provider.source }) : null;
  return (
    <li className="grid gap-2 border-b border-line py-3.5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            {provider.label}
            <Badge tone={provider.configured ? "ok" : "off"}>{provider.configured ? t("KEY SET") : t("NO KEY")}</Badge>
            {provider.models.length > 0 && <span className="text-xs text-ink-3">{t("{n} models", { n: provider.models.length })}</span>}
          </div>
          <p className="mt-0.5 max-w-[62ch] text-xs text-ink-3">
            {BLURB[provider.id]} {source && <span className="text-ink-2">{t("Key {hint}", { hint: provider.hint ?? "" })} · {source}.</span>}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="ghost" icon={<ExternalLink size={12} />} onClick={() => void openExternal(provider.console_url)}>
            {t("Get a key")}
          </Button>
          {provider.configured && (
            <IconButton label={t("Check the key and refresh the models")} onClick={test} disabled={busy !== null}>
              <RefreshCw size={13} className={cx(busy === "test" && "animate-spin")} />
            </IconButton>
          )}
          {provider.source === "saved" && (
            <IconButton label={t("Remove the saved key")} onClick={remove} disabled={busy !== null}>
              <Trash2 size={13} />
            </IconButton>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <TextInput
          type="password"
          value={key}
          onChange={(event) => setKey(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && save()}
          placeholder={provider.configured ? t("Paste a new key to replace it") : t("Paste the API key")}
          aria-label={t("{name} API key", { name: provider.label })}
          className="w-[360px] max-w-full font-mono text-xs"
          autoComplete="off"
        />
        <Button size="sm" variant={key ? "primary" : "secondary"} icon={<KeyRound size={12} />} loading={busy === "save"} disabled={!key.trim()} onClick={save}>
          {t("Save and test")}
        </Button>
      </div>
      {error && <p className="text-xs text-bad">{error}</p>}
    </li>
  );
}

/** The four cloud providers, each with its key: paste, test, replace, remove. Keys are never shown again. */
export function CloudKeys({ compact = false, onChanged }: { compact?: boolean; onChanged?: () => void }) {
  const status = useLoad(() => api.get<CloudStatus>("/api/cloud"));
  const providers = status.data?.providers ?? [];
  const update = (p: CloudProvider) => {
    status.setData(status.data ? { ...status.data, providers: status.data.providers.map((x) => (x.id === p.id ? { ...x, ...p } : x)) } : null);
    onChanged?.();
  };
  return (
    <div>
      {!compact && (
        <p className="max-w-[70ch] text-xs leading-relaxed text-ink-3">
          {t("Cloud models run on the provider's servers: nothing is loaded on this PC and every request is paid on your account. What you send them (questions, the files and web pages the agents read) leaves this PC. Keys are encrypted with Windows (DPAPI) for your account and never shown again, nor written to settings or logs.")}
        </p>
      )}
      {status.error && <p className="mt-2 text-xs text-bad">{status.error}</p>}
      <ul className="mt-1">
        {providers.map((provider) => (
          <ProviderRow key={provider.id} provider={provider} onChanged={update} />
        ))}
      </ul>
    </div>
  );
}
