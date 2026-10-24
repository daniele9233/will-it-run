import { describe, expect, it as test } from "vitest";

import { cloudAsInstalled, isCloudKey, providerGap } from "./models";
import type { CloudProvider } from "./types";

function provider(configured: boolean, models: number): CloudProvider {
  return {
    id: "anthropic", label: "Claude", configured, source: configured ? "saved" : null, hint: null, console_url: "", env_keys: [],
    models: Array.from({ length: models }, (_, i) => ({ id: `m${i}`, key: `cloud:anthropic/m${i}`, label: `M${i}`, context: 200000, recommended: false })),
  };
}

describe("cloud models in the pickers", () => {
  test("a cloud model is listed like an installed one, with its provider", () => {
    const model = cloudAsInstalled({ id: "claude-opus-5-5", key: "cloud:anthropic/claude-opus-5-5", label: "Claude Opus 5.5", context: 200000, recommended: true, provider_label: "Claude" });
    expect(model.model_key).toBe("cloud:anthropic/claude-opus-5-5");
    expect(model.display_name).toBe("Claude Opus 5.5 · Claude");
    expect(model.type).toBe("llm");
    expect(isCloudKey(model.model_key)).toBe(true);
    expect(isCloudKey("qwen3-14b")).toBe(false);
  });

  test("a provider says what it still needs: a key, or its list of models", () => {
    expect(providerGap(provider(false, 0))).toBe("key");
    expect(providerGap(provider(true, 0))).toBe("list");
    expect(providerGap(provider(true, 3))).toBeNull();
  });
});
