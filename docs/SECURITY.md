# Security

Will It Run runs local models that can call tools on your computer. The design assumes a
model can be wrong, confused or manipulated by text it reads, and puts a human between
the model and anything consequential.

## Human approval

With *Require approval for high-risk actions* on (the default), these always pause the
run and show you exactly what is about to happen:

| Action | How it is recognised |
|---|---|
| Deleting files | `delete_file`; `Remove-Item`, `rm`, `del`, `rmdir` in PowerShell |
| Overwriting your files | `write_file` on a file that was already in the folder you chose for the run (on by default, per run) |
| Formatting or repartitioning drives | `Format-Volume`, `Clear-Disk`, `diskpart`, ... |
| Installing software | `winget`, `choco`, `scoop`, `msiexec`, `Install-Module`, `pip install`, `npm i -g` |
| Privilege escalation | `-Verb RunAs`, `runas`, `sudo`, `Start-Process -Credential` |
| Registry changes | `reg add/delete/import`, `Set-ItemProperty` on `HKLM:`/`HKCU:` |
| Changing system state | `Stop-Computer`, `Stop-Service`, `Stop-Process`, `Set-ExecutionPolicy`, `bcdedit`, firewall changes |
| Changing remote infrastructure | `kubectl apply/delete/scale/exec/...`, `helm install/upgrade/uninstall`, `terraform apply/destroy`, Ansible without `--check`, `ssh`, `Invoke-Command` |
| Publishing or sending | `git push`, `docker push`, HTTP POST/PUT/DELETE, `Send-MailMessage` |
| Rewriting history | `git reset --hard`, `git clean`, `git rebase` |
| Dynamically built code | `Invoke-Expression`, `-EncodedCommand`, `DownloadString` |
| Driving the desktop | `open_application`, `type_text`, `click` |

A command chain is as risky as its riskiest part: `kubectl get pods; kubectl apply -f x`
is high risk.

PowerShell commands that match a read-only allowlist (`Get-*`, `kubectl get`,
`terraform plan`, `git status`, ...) run directly. Anything unrecognised is medium risk:
it runs inside the workspace without a prompt. If you do not want that, remove the
`powershell` tool from the agent in the workflow editor.

Denying a call does not stop the run: the agent is told the action was denied and
continues without it.

**A decision applies to exactly the call you saw.** Each approval request has a
fingerprint of the run, agent, tool and arguments; a resume value is only accepted for
the matching request.

Workflows also place approval gates on plans (the Coding and DevOps templates pause
before implementation). Financial actions are out of scope: no tool can move money, and
the computer-use permission check refuses plans that need credentials or payments.

Turning the setting off makes high-risk calls run unprompted. The UI warns when you do.

## Sandboxing of file tools

`read_file`, `write_file`, `list_dir` and `delete_file` resolve every path and refuse
anything outside the run's workspace, including `..` traversal and absolute paths.
`run_python`, `run_tests`, `git` and `powershell` start in the workspace but are real
processes with your user's rights: they are not a sandbox. Treat a workflow with those
tools like running a script written by someone else.

### Working in your own folder

A run normally works in an empty workspace of its own. You can point it at a folder of
yours instead. Then the workspace *is* your project, and the file tools can change it.
What protects it:

- A whole drive, your user folder itself, and the folders of Windows and of installed
  programs are refused as a workspace.
- Deleting a file asks first, as always.
- Overwriting a file that was there before the run asks first, unless you turn that off
  for the run. Creating new files, and rewriting files the run itself created, does not ask.

What does not: code an agent runs with `run_python`, `run_tests` or an unrecognised
PowerShell command can change files without asking, in your folder as anywhere else. Keep
the folder under version control, or remove those tools from the agents, when that matters.

### GitHub repositories

A GitHub address is cloned with the Git installed on the PC into Will It Run's data
folder, and the run works on that copy. Only `https://github.com/owner/repository` is
accepted: no other host, no credentials in the address, and the address and branch reach
Git as separate arguments after `--`, never through a shell. Git's password prompts and
sign-in windows are turned off, so a clone either works with the access that exists or
fails with Git's own message. Nothing is pushed unless an agent calls `git push`, which is
high risk and waits for your approval.

**Your GitHub account.** Will It Run does not implement a GitHub sign-in and never holds a
GitHub password or token. It drives the GitHub CLI (`gh`), GitHub's own tool:

- Signing in runs `gh auth login --web`. You authorise on github.com in your browser; the
  app only reads the one-time code `gh` prints, to show it to you. The token is created
  and stored by `gh`, in the Windows credential store.
- Who is signed in is read from `gh`'s settings (`gh config get user`). The list of
  repositories is `gh api user/repos`: the app receives names, descriptions, dates and
  visibility, nothing else. `gh auth token` is never called.
- To clone a private repository, Git is given `gh` as the credential helper for that one
  command (`git -c credential.https://github.com.helper=... clone`). Your Git and `gh`
  configuration files are not changed.

The sign-in belongs to `gh`, not to this app: it is the same one your terminal uses, it
grants `gh`'s usual scopes (repositories, organisations, gists), and signing out is
`gh auth logout`. Will It Run has no sign-out button, so that it cannot sign your other
tools out by surprise.

Cloning a repository brings someone else's code onto the PC. Agents with execution tools
may run it (tests, scripts). Clone what you would be willing to run yourself.

## The local API

- Binds to `127.0.0.1` only.
- The desktop shell generates a random token per launch, passes it to the core in its
  environment (not on the command line), and the UI sends it as a bearer token. Other
  local processes cannot call the API without it. `/api/health` is the only open route.
- CORS allows only the app's own origins.
- Condition expressions in workflows are parsed by a small evaluator; there is no `eval`.
- The UI's content security policy allows connections only to itself and `127.0.0.1`.

Running `willitrun serve` yourself without `--token` starts the API unauthenticated on
localhost. That is intended for UI development only.

## Disk safety

No download starts unless, after it and its temporary overhead, the drive keeps its
safety reserve (15% of capacity, at least 10 GB, at most 30 GB; configurable). Space
already promised to queued downloads counts as used. A file whose size is unknown is
refused, because safety cannot be checked. When a download is refused Will It Run offers:
a smaller quantization, a smaller model, deleting unused models, or another drive.

Direct downloads are verified against the SHA-256 published by the repository; a
mismatch deletes the file.

The check always runs against the drive that will be written. When the user picks a
folder for a download, the path must be absolute and on an existing drive; Will It Run
creates only `<folder>/<publisher>/<repo>/` under it. Stopping a download deletes its
partial file and nothing else. To make LM Studio list a file saved outside its folder,
Will It Run adds a link inside LM Studio's models folder (see
[LM Studio](LM_STUDIO.md#saving-outside-lm-studios-folder)); it never replaces a folder
that already contains files.

## Model provenance

Models are third-party files. Will It Run shows publisher, repository, license, base model,
quantizer and file hash, marks missing metadata as Unknown, and downloads only from
`huggingface.co` or through LM Studio. GGUF files are data loaded by LM Studio's engine,
not executed as code by Will It Run. Will It Run does not vet model behaviour.

## Logging and secrets

Logs are JSON lines in `%LOCALAPPDATA%\Will It Run\Data\logs`, rotated at 2 MB. Before
writing, values are redacted by key name (password, secret, token, api key,
authorization, cookie, credential, private key, session id) and by pattern (bearer
tokens, `hf_`/`sk-`/`gh*_` style keys, PEM private keys, `key=value` secrets). The
Logs page applies the same redaction again.

The LM Studio API token, if you set one, is stored in your settings file, never returned
by the API (`lmstudio_api_token_set: true` instead) and never logged. Will It Run reads
only the `downloadsFolder` and `port` keys from LM Studio's own settings.

## The project memory

What a run learns about a project is kept and shown to later runs on the same project
(see [MEMORY.md](MEMORY.md)). That makes it a way for text to travel from one run to the
next, so:

- A memory belongs to one project (a folder, or a GitHub repository). Nothing recorded
  for one project is ever shown to a run on another.
- Entries reach an agent as notes from earlier sessions, under a heading that says they
  can be out of date and that the files win. They are not instructions, and they give an
  agent no tool or permission it did not have: a high-risk action asks you exactly as
  before.
- An entry an agent wrote can be wrong, or can repeat text it read in a file of a
  repository you do not trust. Open the Memory page to see who wrote each entry, and
  edit or delete it.
- Verification workflows neither read nor write the memory.
- Secrets are redacted from entries with the same patterns as the logs. As there, that
  is pattern matching, not a guarantee.
- The memory stays in Will It Run's database on this PC. The embedding model that makes
  it searchable by meaning runs in LM Studio, locally.

## Privacy

Local by default: inference, models, workflows, project state and benchmarks stay on
your machine. There is no telemetry and no account. The only outbound connections are
to `huggingface.co` for model search, metadata and direct downloads, which you can turn
off (Settings → Models → Search Hugging Face), plus whatever LM Studio itself does.

Two optional additions go further, only when you use them:

- **Cloud models.** With an API key saved (Settings → Cloud models), a request to that model
  goes to its provider (Anthropic, OpenAI, DeepSeek, Google) with everything the model reads:
  your message, earlier turns, tool results, file contents. Keys are encrypted with Windows
  DPAPI for your account in `%APPDATA%\Will It Run\secrets`, never written to settings, logs
  (Gemini, OpenAI, DeepSeek and Anthropic key shapes are redacted) or API answers, which only say
  whether a key is set and its last four characters. See [CLOUD.md](CLOUD.md).
- **Clone a site.** Things are fetched on first use. `playwright-core` (pinned version) comes from the npm
  registry into the app's own folder. The cloning kit comes from GitHub with `git fetch` of one pinned commit; Git
  checks the content against the commit's hash, and a different commit is refused. The kit is a Next.js project
  with scripts, and its scripts do run on your PC: the agent starts them with `run_command`, which is not a shell
  but a list of commands (`node scripts/*.mjs` inside the run's folder, `npm run <script>`, `npm install`,
  `npx tsc|eslint|next build|playwright install`, read-only `git`); pipes, redirects, `&`, variables, `node -e`,
  options that point npm at another registry or folder, and `npx` programs the project does not have are
  refused. The kit's `npm install` runs the packages' own install scripts, like any Next.js project, and `npm run
  setup` downloads Playwright's Chromium. The browser tools only open `http` and `https` addresses and write
  inside the run's folder; adding an npm package, deleting a file or overwriting a file that was in your folder
  asks first. What the agent reads (pages, screenshots) goes to the model: to a cloud provider when you chose
  one. See [CLONE.md](CLONE.md).

## Known limits

- The installer is not code-signed.
- Tool processes are not sandboxed beyond their working directory.
- Risk classification is pattern-based. It is a seatbelt, not a guarantee: review
  what agents are allowed to do, and do not point a team with shell tools at production
  credentials.
