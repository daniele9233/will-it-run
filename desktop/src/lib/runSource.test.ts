import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SESSION_PANEL, clampPanelWidth, isGithubUrl, loadApproval, loadPanelPrefs, looksLikeQuestion, PANEL_MAX, PANEL_MIN, repoName, saveApproval, savePanelPrefs,
  sourceFromEvent, sourcePayload, sourceProblem,
} from "./runSource";

describe("run source", () => {
  it("accepts GitHub repository addresses and nothing else", () => {
    expect(isGithubUrl("https://github.com/octocat/Hello-World")).toBe(true);
    expect(isGithubUrl(" https://github.com/octocat/Hello-World.git/ ")).toBe(true);
    for (const bad of ["", "github.com/a/b", "http://github.com/a/b", "https://gitlab.com/a/b", "https://github.com/a", "https://github.com/a/b/tree/main", "https://user:pw@github.com/a/b"]) {
      expect(isGithubUrl(bad)).toBe(false);
    }
    expect(repoName("https://github.com/octocat/Hello-World.git")).toBe("octocat/Hello-World");
  });

  it("says what is missing before a run can start", () => {
    expect(sourceProblem({ kind: "none" })).toBeNull();
    expect(sourceProblem({ kind: "folder", path: "  ", protect: true })).toBe("folder");
    expect(sourceProblem({ kind: "folder", path: "D:\\work", protect: true })).toBeNull();
    expect(sourceProblem({ kind: "github", url: "not a url", branch: "" })).toBe("github");
    expect(sourceProblem({ kind: "github", url: "https://github.com/a/b", branch: "dev" })).toBeNull();
  });

  it("turns the choice into the fields the core expects", () => {
    expect(sourcePayload({ kind: "none" })).toEqual({});
    expect(sourcePayload({ kind: "folder", path: " D:\\work ", protect: false })).toEqual({ workspace: "D:\\work", protect_existing: false });
    expect(sourcePayload({ kind: "github", url: "https://github.com/a/b ", branch: "" })).toEqual({ repo_url: "https://github.com/a/b", repo_branch: null });
    expect(sourcePayload({ kind: "github", url: "https://github.com/a/b", branch: " dev " })).toEqual({ repo_url: "https://github.com/a/b", repo_branch: "dev" });
  });

  it("reads back where a finished run worked, so the next request continues there", () => {
    expect(sourceFromEvent({ source: { kind: "folder", path: "D:\\work" }, protect_existing: false })).toEqual({ kind: "folder", path: "D:\\work", protect: false });
    expect(sourceFromEvent({ source: { kind: "folder", path: "D:\\work" } })).toEqual({ kind: "folder", path: "D:\\work", protect: true });
    expect(sourceFromEvent({ source: { kind: "github", url: "https://github.com/a/b", branch: null } })).toEqual({ kind: "github", url: "https://github.com/a/b", branch: "" });
    // Runs from before this existed, or an empty workspace: no source.
    expect(sourceFromEvent({ workspace: "C:\\x" })).toEqual({ kind: "none" });
    expect(sourceFromEvent(undefined)).toEqual({ kind: "none" });
  });

  it("keeps the side panel between its narrowest and widest", () => {
    expect(clampPanelWidth(120)).toBe(PANEL_MIN);
    expect(clampPanelWidth(5000)).toBe(PANEL_MAX);
    expect(clampPanelWidth(412.6)).toBe(413);
  });

  /** These tests run without a browser: a storage that lives for one test stands in for it. */
  const withStorage = () => {
    const kept = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: { getItem: (key: string) => kept.get(key) ?? null, setItem: (key: string, value: string) => void kept.set(key, value) },
    });
  };
  afterEach(() => vi.unstubAllGlobals());

  it("remembers the panel of a session apart from the editor's, with its own limits", () => {
    withStorage();
    expect(loadPanelPrefs(SESSION_PANEL)).toEqual({ side: "right", width: SESSION_PANEL.fallback });
    savePanelPrefs({ side: "left", width: 900 }, SESSION_PANEL);
    expect(loadPanelPrefs(SESSION_PANEL)).toEqual({ side: "left", width: 900 });
    // Wider than the editor's panel may be, and the editor's own choice is untouched.
    expect(clampPanelWidth(900, SESSION_PANEL)).toBe(900);
    expect(clampPanelWidth(100, SESSION_PANEL)).toBe(SESSION_PANEL.min);
    expect(loadPanelPrefs()).toEqual({ side: "right", width: 360 });
  });

  it("asks for approval unless the user chose to ignore permissions", () => {
    withStorage();
    expect(loadApproval()).toBe("ask");
    saveApproval("bypass");
    expect(loadApproval()).toBe("bypass");
    window.localStorage.setItem("agentfit.run.approval", "anything else");
    expect(loadApproval()).toBe("ask");
  });
});

describe("a message under a finished session", () => {
  it("is a question when it asks one", () => {
    for (const question of [
      "come apro il sito web?",
      "Come faccio ad aprire il sito",
      "perché i test falliscono",
      "Perché non parte",
      "cos'è index.html",
      "dove sono i file",
      "spiegami cosa hai fatto",
      "how do I open it",
      "What did you change",
      "puoi aggiungere una pagina contatti?",
    ]) {
      expect(looksLikeQuestion(question), question).toBe(true);
    }
  });

  it("is work for the team when it asks for work", () => {
    for (const request of [
      "aggiungi una pagina contatti",
      "Rifai il sito in React con un backend",
      "puoi aggiungere una pagina contatti",
      "comincia dalla home",
      "add a contact page",
      "",
      "   ",
    ]) {
      expect(looksLikeQuestion(request), request).toBe(false);
    }
  });
});
