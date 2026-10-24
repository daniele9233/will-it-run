import { Check, ExternalLink, Lock, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, openExternal } from "../lib/api";
import { timeAgo } from "../lib/format";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { isGithubUrl, repoName } from "../lib/runSource";
import { Button, Note, TextInput, cx } from "./ui";

interface SignIn {
  state: "idle" | "waiting" | "done" | "failed" | "cancelled";
  code: string | null;
  url: string;
  error: string | null;
}

interface GithubStatus {
  /** False when the GitHub CLI is not installed. */
  cli: boolean;
  signed_in: boolean;
  login: string | null;
  sign_in: SignIn;
}

export interface GithubRepo {
  full_name: string;
  private: boolean;
  description: string | null;
  pushed_at: string | null;
  default_branch: string | null;
  html_url: string;
  fork: boolean;
  archived: boolean;
}

interface Props {
  url: string;
  branch: string;
  /** `picked` is true when a repository was chosen outright, as opposed to an edit in progress. */
  onChange: (next: { url: string; branch: string }, picked?: boolean) => void;
  /** The user has just asked for GitHub: go straight to the sign-in page when not signed in. */
  signInNow: boolean;
}

/**
 * Sign in to GitHub in the browser, then choose one of the account's repositories.
 * The sign-in is GitHub's own (through the GitHub CLI): this app never sees a password or a token.
 */
export function GithubPicker({ url, branch, onChange, signInNow }: Props) {
  const status = useLoad(() => api.get<GithubStatus>("/api/github"));
  const [signIn, setSignIn] = useState<SignIn | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const asked = useRef(false);

  const data = status.data;
  const flow = signIn ?? data?.sign_in ?? null;
  const waiting = flow?.state === "waiting" && !data?.signed_in;
  const repos = useLoad(
    () => (data?.signed_in ? api.get<{ repos: GithubRepo[] }>("/api/github/repos") : Promise.resolve({ repos: [] as GithubRepo[] })),
    [data?.signed_in, data?.login],
  );

  const start = () => {
    setProblem(null);
    api
      .post<SignIn>("/api/github/login")
      .then((next) => {
        setSignIn(next);
        void openExternal(next.url);
      })
      .catch((err: unknown) => setProblem(errorMessage(err)));
  };
  const cancel = () => {
    void api.del<SignIn>("/api/github/login").then(setSignIn).catch(() => undefined);
  };

  // Clicking GitHub while signed out opens the sign-in page at once, as a link would.
  useEffect(() => {
    if (signInNow && data && data.cli && !data.signed_in && data.sign_in.state !== "waiting" && !asked.current) {
      asked.current = true;
      start();
    }
  }, [signInNow, data]);

  // While the browser sign-in is open, watch for it to finish.
  useInterval(
    () => {
      status.reload();
      setSignIn(null);
    },
    waiting ? 2000 : null,
  );

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const list = repos.data?.repos ?? [];
    return needle && !isGithubUrl(filter) ? list.filter((r) => r.full_name.toLowerCase().includes(needle)) : list;
  }, [repos.data, filter]);

  if (status.loading && !data) return <p className="text-xs text-ink-3">{t("Checking the GitHub sign-in...")}</p>;

  const manual = (
    <TextInput
      value={url}
      onChange={(event) => onChange({ url: event.target.value, branch })}
      placeholder="https://github.com/owner/repository"
      aria-label={t("GitHub repository address")}
      aria-invalid={url.trim() !== "" && !isGithubUrl(url)}
      className={cx("h-8 flex-1 font-mono text-xs", url.trim() !== "" && !isGithubUrl(url) && "border-bad")}
    />
  );

  if (!data?.cli) {
    return (
      <div className="grid gap-2">
        <Note tone="warn" action={<Button size="sm" icon={<ExternalLink size={12} />} onClick={() => void openExternal("https://cli.github.com/")}>cli.github.com</Button>}>
          {t("Signing in to GitHub and listing your repositories needs the GitHub CLI (gh), which is not installed on this PC. Until then you can paste the address of a public repository.")}
        </Note>
        <div className="flex gap-2">{manual}</div>
      </div>
    );
  }

  if (!data.signed_in) {
    return (
      <div className="grid gap-2">
        {waiting && flow ? (
          <div className="grid gap-2 rounded-md border border-line bg-bg px-3 py-2.5">
            <div className="text-sm text-ink-2">{t("GitHub is open in your browser. Sign in there and enter this code:")}</div>
            <div className="flex flex-wrap items-center gap-3">
              <span className="selectable font-mono text-xl font-semibold tracking-[0.12em] text-ink">{flow.code ?? "····-····"}</span>
              {flow.code && <span className="text-xs text-ink-3">{t("already copied: paste it with Ctrl+V")}</span>}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" icon={<ExternalLink size={12} />} onClick={() => void openExternal(flow.url)}>
                {t("Open the page again")}
              </Button>
              <Button size="sm" variant="ghost" onClick={cancel}>
                {t("Cancel")}
              </Button>
              <span className="text-xs text-ink-3">{t("Waiting for you to finish on github.com...")}</span>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2.5">
            <Button size="sm" variant="primary" icon={<ExternalLink size={12} />} onClick={start}>
              {t("Sign in to GitHub")}
            </Button>
            <span className="text-xs text-ink-3">{t("Opens github.com in your browser. Your password stays there: this app never sees it.")}</span>
          </div>
        )}
        {(problem || flow?.state === "failed") && <p className="text-xs text-bad">{problem ?? t("The sign-in did not complete: {reason}", { reason: flow?.error ?? "" })}</p>}
      </div>
    );
  }

  const selected = isGithubUrl(url) ? repoName(url).toLowerCase() : "";
  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" aria-hidden />
          <TextInput
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              if (isGithubUrl(event.target.value)) onChange({ url: event.target.value.trim(), branch }, true);
            }}
            placeholder={t("Search your repositories, or paste an address")}
            aria-label={t("Search your repositories, or paste an address")}
            className="h-8 w-full pl-8 text-xs"
          />
        </div>
        <span className="flex shrink-0 items-center gap-1.5 text-xs text-ink-2">
          <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden />
          {data.login}
        </span>
        <Button size="sm" variant="ghost" onClick={start} title={t("Sign in with a different GitHub account")}>
          {t("Change account")}
        </Button>
      </div>
      {waiting && flow && (
        <p className="text-xs text-ink-2">
          {t("GitHub is open in your browser. Sign in there and enter this code:")} <span className="selectable font-mono font-semibold text-ink">{flow.code}</span>
        </p>
      )}
      {repos.loading && !repos.data && <p className="text-xs text-ink-3">{t("Loading your repositories...")}</p>}
      {repos.error && (
        <p className="text-xs text-bad">
          {repos.error}{" "}
          <button type="button" className="text-accent hover:underline" onClick={start}>
            {t("Sign in to GitHub")}
          </button>
        </p>
      )}
      {repos.data && (
        <ul role="listbox" aria-label={t("Your repositories")} className="max-h-[230px] overflow-y-auto rounded-md border border-line bg-bg p-1">
          {shown.length === 0 && <li className="px-2.5 py-2 text-xs text-ink-3">{repos.data.repos.length === 0 ? t("This account has no repositories yet.") : t("No repository matches.")}</li>}
          {shown.map((repo) => {
            const active = repo.full_name.toLowerCase() === selected;
            return (
              <li key={repo.full_name} role="option" aria-selected={active}>
                <button
                  type="button"
                  onClick={() => onChange({ url: repo.html_url, branch: "" }, true)}
                  className={cx("grid w-full grid-cols-[14px_minmax(0,1fr)_auto_auto] items-center gap-2.5 rounded-sm px-2 py-1.5 text-left text-xs transition-colors duration-150", active ? "bg-raised text-ink" : "text-ink-2 hover:bg-surface")}
                >
                  {active ? <Check size={13} className="text-accent" aria-hidden /> : repo.private ? <Lock size={12} className="text-ink-3" aria-hidden /> : <span />}
                  <span className="truncate" title={repo.description ?? repo.full_name}>
                    <span className="text-ink-3">{repo.full_name.split("/")[0]}/</span>
                    <span className="font-medium">{repo.full_name.split("/").slice(1).join("/")}</span>
                  </span>
                  <span className="text-2xs text-ink-3">{repo.private ? t("private") : t("public")}</span>
                  <span className="tabular w-[92px] text-right text-2xs text-ink-3">{repo.pushed_at ? timeAgo(repo.pushed_at) : ""}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {selected && (
        <div className="flex items-center gap-2">
          <span className="selectable min-w-0 flex-1 truncate font-mono text-xs text-ink">{repoName(url)}</span>
          <TextInput value={branch} onChange={(event) => onChange({ url, branch: event.target.value })} placeholder={t("branch (optional)")} aria-label={t("Branch")} className="h-7 w-[170px] font-mono text-xs" />
        </div>
      )}
    </div>
  );
}
