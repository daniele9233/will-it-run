import { describe, expect, it } from "vitest";

import capabilityText from "../../src-tauri/capabilities/default.json?raw";
import { ApiError } from "./api";
import { errorMessage } from "./hooks";

describe("errorMessage", () => {
  it("says what the shell said when a plugin rejects with a string", () => {
    // Seen with "Open the local version": the opener refused the address and the toast said only "Something went wrong".
    expect(errorMessage("Not allowed to open url http://127.0.0.1:61234/")).toBe("Not allowed to open url http://127.0.0.1:61234/");
    expect(errorMessage(new ApiError("There is no local version yet.", 404))).toBe("There is no local version yet.");
    expect(errorMessage(undefined)).toBe("Something went wrong");
    expect(errorMessage("  ")).toBe("Something went wrong");
  });
});

describe("what the window may open in the browser", () => {
  it("includes the local version a website study serves on 127.0.0.1", () => {
    const capability = JSON.parse(capabilityText) as {
      permissions: (string | { identifier: string; allow?: { url: string }[] })[];
    };
    const opener = capability.permissions.find((p) => typeof p !== "string" && p.identifier === "opener:allow-open-url");
    const patterns = typeof opener === "object" ? (opener.allow ?? []).map((a) => a.url) : [];
    // The opener matches with glob patterns: "*" stands for any run of characters.
    const allowed = (url: string) => patterns.some((p) => new RegExp(`^${p.split("*").map((s) => s.replace(/[.?+^$()[\]{}|\\/]/g, "\\$&")).join(".*")}$`).test(url));
    expect(allowed("http://127.0.0.1:61234/")).toBe(true);
    expect(allowed("http://192.168.1.10:8080/")).toBe(false);
  });
});
