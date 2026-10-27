import { Cloud } from "lucide-react";

import { t } from "../lib/i18n";
import { isCloudKey, providerGap } from "../lib/models";
import { useApp } from "../lib/store";
import type { CloudProvider, InstalledModel } from "../lib/types";
import { Button } from "./ui";

/**
 * The options of a model <Select>: the models on this PC, then the cloud ones. A provider
 * without a key is listed too, greyed, saying where to add one: otherwise nothing tells
 * that cloud models exist at all.
 */
export function ModelOptions({ models, providers, label }: { models: InstalledModel[]; providers: CloudProvider[]; label: (model: InstalledModel) => string }) {
  const local = models.filter((m) => !isCloudKey(m.model_key));
  const remote = models.filter((m) => isCloudKey(m.model_key));
  const waiting = providers.filter((p) => providerGap(p) !== null && !remote.some((m) => m.model_key.startsWith(`cloud:${p.id}/`)));
  return (
    <>
      {local.length > 0 && (
        <optgroup label={t("On this PC")}>
          {local.map((m) => (
            <option key={m.model_key} value={m.model_key}>
              {label(m)}
            </option>
          ))}
        </optgroup>
      )}
      {(remote.length > 0 || waiting.length > 0) && (
        <optgroup label={t("Cloud")}>
          {remote.map((m) => (
            <option key={m.model_key} value={m.model_key}>
              {label(m)}
            </option>
          ))}
          {waiting.map((p) => (
            <option key={p.id} value={`missing:${p.id}`} disabled>
              {providerGap(p) === "key"
                ? t("{name}: add an API key in Settings › Cloud models", { name: p.label })
                : (p.error ?? t("{name}: press ↻ in Settings › Cloud models to load its models", { name: p.label }))}
            </option>
          ))}
        </optgroup>
      )}
    </>
  );
}

/** A way to Settings › Cloud models, next to a model picker that has no cloud model yet. */
export function CloudModelsLink({ models }: { models: InstalledModel[] }) {
  const navigate = useApp((s) => s.navigate);
  if (models.some((m) => isCloudKey(m.model_key))) return null;
  return (
    <Button size="sm" variant="ghost" icon={<Cloud size={13} />} onClick={() => navigate("settings", { tab: "cloud" })} title={t("Claude, ChatGPT, DeepSeek and Gemini appear here once their API key is saved in Settings › Cloud models.")}>
      {t("Add cloud models")}
    </Button>
  );
}
