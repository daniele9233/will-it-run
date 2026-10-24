// The models a picker offers: the ones installed in LM Studio, then the cloud ones that have
// a key (Settings > Cloud models). A cloud model is given the shape of an installed one, so
// every picker lists, names and assigns both alike.

import { useMemo } from "react";

import { api } from "./api";
import { useLoad } from "./hooks";
import type { CloudModel, CloudProvider, CloudStatus, InstalledModel } from "./types";

export function isCloudKey(key: string): boolean {
  return key.startsWith("cloud:");
}

export function cloudAsInstalled(model: CloudModel): InstalledModel {
  return {
    model_key: model.key, display_name: `${model.label} · ${model.provider_label ?? "cloud"}`, type: "llm", repo: null, filename: null, quant: null, size_bytes: null,
    params_string: null, vision: model.vision ?? false, tool_use: true, max_context_length: model.context, loaded_instances: [],
  } as InstalledModel;
}

/** What a provider still needs before its models can be picked, or null when they can. */
export function providerGap(provider: CloudProvider): "key" | "list" | null {
  if (!provider.configured) return "key";
  return provider.models.length === 0 ? "list" : null;
}

export function useModelChoices() {
  const installed = useLoad(() => api.get<{ models: InstalledModel[] }>("/api/models/installed"));
  const cloud = useLoad(() => api.get<CloudStatus>("/api/cloud"));
  const local = useMemo(() => (installed.data?.models ?? []).filter((m) => m.type === "llm"), [installed.data]);
  const remote = useMemo(() => (cloud.data?.models ?? []).map(cloudAsInstalled), [cloud.data]);
  const all = useMemo(() => [...local, ...remote], [local, remote]);
  return { installed, cloud, local, remote, all, providers: cloud.data?.providers ?? [] };
}
