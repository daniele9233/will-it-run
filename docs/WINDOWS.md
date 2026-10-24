# Windows

Will It Run version 1 targets Windows 11 x64 and nothing else. There are no cross-platform
abstractions: where Windows has a native answer, Will It Run uses it.

## Where things live

| What | Where | Why |
|---|---|---|
| Application | `%LOCALAPPDATA%\Will It Run\` (per-user install, no admin rights) | NSIS current-user install |
| Database, checkpoints, caches, logs, run workspaces | `%LOCALAPPDATA%\Will It Run\Data\` | Machine-local and regenerable. Kept in its own folder so it never mixes with program files. |
| Settings, workflows | `%APPDATA%\Will It Run\` | User-authored; roams with the profile |
| Models | LM Studio's models folder, or the directory you set | Will It Run never keeps a second copy |

The first start after the rename from *AgentFit* copies `%LOCALAPPDATA%\AgentFit\Data`
and `%APPDATA%\AgentFit` to the folders above, if those do not exist yet, so settings,
benchmarks, teams and run history come along. The old folders are left as they are and
can be deleted by hand.

Paths come from the `LOCALAPPDATA` and `APPDATA` environment variables. No user profile
path is hard-coded; a test scans the source for `C:\Users\`. Set `AGENTFIT_HOME` to move
both data roots, for portable use or tests.

Uninstalling removes the application. Your data in `Data\` and `%APPDATA%\Will It Run`
stays; delete those folders to remove everything.

## Windows facilities used

| Need | Mechanism |
|---|---|
| Hardware inventory | WMI/CIM through one PowerShell call (`Get-CimInstance`) |
| True VRAM | Display-class registry key (`HardwareInformation.qwMemorySize`) |
| NVIDIA details | `nvidia-smi`, installed with the driver |
| CPU instruction sets | `kernel32!IsProcessorFeaturePresent` |
| Free space for the current user | `kernel32!GetDiskFreeSpaceExW` |
| Disk media and bus type | `Get-PhysicalDisk`, `Get-Partition` |
| Live memory | `nvidia-smi`, `psutil` |
| Window enumeration, active window | `user32!EnumWindows`, `GetForegroundWindow` |
| Mouse | `user32!SetCursorPos`, `mouse_event` (approval required) |
| Keyboard | `System.Windows.Forms.SendKeys` (approval required) |
| Screenshot | `System.Drawing` `CopyFromScreen`, saved to the run workspace |
| Clipboard | `Get-Clipboard` |
| Agent shell tool | `powershell -NoProfile -NonInteractive` |
| Notifications | Windows toast notifications through the Tauri notification plugin |
| UI rendering | WebView2 (part of Windows 11) |

Child processes are started with `CREATE_NO_WINDOW`, so no console windows flash.

## The desktop shell and the core

`Will It Run.exe` (Tauri, about 3 MB) starts `willitrun-core\willitrun-core.exe` (the frozen
Python core) with a random per-launch token in its environment, reads the port the core
chose from its first line of output, and gives both to the UI. When the window closes,
the core is stopped.

The core listens on `127.0.0.1` only. Windows Firewall does not prompt because nothing
listens on a network interface.

`willitrun-core.exe` is also the command-line tool: run it from the install folder with
any `agentfit` command, for example `willitrun-core.exe doctor`.

## Installer

`scripts\build.ps1` produces `dist\WillItRun-Setup-x64.exe` (NSIS, built by Tauri):

- installs for the current user, no elevation
- creates a Start Menu entry, and offers a desktop shortcut
- bundles the Python runtime and all Python dependencies inside `willitrun-core\`
- bootstraps WebView2 if it is missing (it is present on Windows 11)
- **does not install LM Studio.** If LM Studio is absent, the first-run screen says
  "LM Studio is required for local model execution" and offers *Open LM Studio Download*
  or *Continue without runtime*.

The installer is not code-signed. Windows SmartScreen will warn on first run until the
project signs its releases.

## Notifications

When Will It Run is in the background and notifications are enabled (Settings → General):
model download complete or failed, benchmark complete, workflow requires approval,
workflow complete. In the foreground the same events appear as in-app toasts.

## Windows 10

Not a target. It should work where WebView2 and PowerShell 5.1 are present;
`willitrun doctor` reports a WARNING for builds below 22000.
