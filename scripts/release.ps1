<#
.SYNOPSIS
    Publish a new version of Will It Run: bump the version, build the installer, commit, tag, push and make the GitHub release.

.DESCRIPTION
    1. Checks that the working tree is clean and that `gh` is signed in.
    2. Works out the next version from the last release (patch by default) and writes it in the five places that hold it
       (backend\pyproject.toml, backend\src\agentfit\branding.py, desktop\package.json and its lock file,
       desktop\src-tauri\Cargo.toml, desktop\src-tauri\tauri.conf.json).
    3. Runs scripts\build.ps1 (lint, tests, frozen core, Tauri installer).
    4. Copies the installer to dist\release\WillItRun-<version>-Setup-x64.exe with a .sha256 beside it.
    5. Commits the version files ("Release v<version>"), tags v<version>, pushes the branch and the tag.
    6. Creates the GitHub release with the installer and the checksum, and prints its address.

    Commit the work first: the script refuses to start on a dirty tree, so the release commit holds only the version.

.PARAMETER Bump
    patch (default), minor or major, applied to the last release's version.

.PARAMETER Version
    An exact X.Y.Z, instead of Bump.

.PARAMETER NotesFile
    A Markdown file for the "What's new" part of the release. Default: the subjects of the commits since the last release.

.PARAMETER CommitTrailer
    A line added at the end of the release commit message, e.g. "Co-Authored-By: Name <mail>".

.PARAMETER SkipTests
    Passed to build.ps1.

.PARAMETER DryRun
    Say what would be done and change nothing.

.EXAMPLE
    .\scripts\release.ps1 -DryRun
    .\scripts\release.ps1
    .\scripts\release.ps1 -Bump minor -NotesFile notes.md
#>
[CmdletBinding()]
param(
    [ValidateSet("patch", "minor", "major")][string]$Bump = "patch",
    [string]$Version,
    [string]$NotesFile,
    [string]$CommitTrailer,
    [switch]$SkipTests,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail([string]$Message) { throw $Message }
function Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Run-Native([string]$What, [scriptblock]$Body) {
    & $Body
    if ($LASTEXITCODE -ne 0) { Fail "$What failed with exit code $LASTEXITCODE" }
}
function Read-Text([string]$Path) { [System.IO.File]::ReadAllText((Join-Path $root $Path)) }
function Write-Text([string]$Path, [string]$Text) {
    # UTF-8 without a byte-order mark: Windows PowerShell 5.1's Set-Content would add one.
    [System.IO.File]::WriteAllText((Join-Path $root $Path), $Text, (New-Object System.Text.UTF8Encoding($false)))
}
function Replace-First([string]$Path, [string]$Pattern, [string]$Replacement) {
    $text = Read-Text $Path
    $regex = New-Object System.Text.RegularExpressions.Regex($Pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    if (-not $regex.IsMatch($text)) { Fail "No version found in $Path" }
    Write-Text $Path ($regex.Replace($text, $Replacement, 1))
}

# -- where we are ---------------------------------------------------------------------------------------------------------

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Fail "GitHub CLI (gh) is not installed." }
Run-Native "gh auth status" { gh auth status | Out-Null }

$dirty = git status --porcelain
if ($dirty) {
    if ($DryRun) { Write-Warning "The working tree has changes: a real release would stop here.`n$dirty" }
    else { Fail "The working tree has changes. Commit them first:`n$dirty" }
}
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -eq "HEAD") { Fail "Not on a branch." }

$current = [regex]::Match((Read-Text "backend\src\agentfit\branding.py"), '(?m)^APP_VERSION\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $current) { Fail "Could not read APP_VERSION from branding.py." }

Run-Native "git fetch" { git fetch --tags --quiet }
# The newest release tag by version number (not by date).
$lastTag = git tag --list "v*" --sort=-v:refname | Select-Object -First 1
$base = if ($lastTag) { $lastTag.Trim().TrimStart("v") } else { $current }

if ($Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') { Fail "Version must look like 1.2.3." }
    $next = $Version
} else {
    $parts = $base.Split(".") | ForEach-Object { [int]$_ }
    switch ($Bump) {
        "major" { $parts = @(($parts[0] + 1), 0, 0) }
        "minor" { $parts = @($parts[0], ($parts[1] + 1), 0) }
        default { $parts = @($parts[0], $parts[1], ($parts[2] + 1)) }
    }
    $next = $parts -join "."
}
$tag = "v$next"

git rev-parse -q --verify "refs/tags/$tag" | Out-Null
if ($LASTEXITCODE -eq 0) { Fail "The tag $tag already exists." }

# -- the notes ------------------------------------------------------------------------------------------------------------

if ($NotesFile) {
    if (-not (Test-Path $NotesFile)) { Fail "Notes file not found: $NotesFile" }
    $changes = (Get-Content -Raw $NotesFile).Trim()
} else {
    $range = if ($lastTag) { "$($lastTag.Trim())..HEAD" } else { "HEAD" }
    $subjects = git log --no-merges --pretty=format:"- %s" $range
    $changes = if ($subjects) { ($subjects -join "`n") } else { "- Maintenance release." }
}

$assetName = "WillItRun-$next-Setup-x64.exe"
$footer = @"

### Install

1. Download ``$assetName`` below and run it. It installs for the current user, no administrator rights.
2. The installer is **not code-signed**, so Windows SmartScreen warns on first run: *More info* -> *Run anyway*. To check that the file is the one published here, compare it with ``$assetName.sha256``:

   ``````powershell
   Get-FileHash .\$assetName -Algorithm SHA256
   ``````

### Needs

- Windows 11 x64.
- [LM Studio](https://lmstudio.ai/download) to run models locally (Will It Run never installs it) and the models themselves, which are downloaded separately.
- Node.js 22 or newer, npm and Git, only for *Clone a site*. Chrome or Edge as well (Edge ships with Windows 11).
- Optional, for stricter answer checking in the Arena: Terraform, ansible-lint, Node, Python, ``psql``.

See the [README](https://github.com/daniele9233/will-it-run#readme) and ``docs/`` for the rest.
"@
$notes = "## Will It Run $next`n`n### What's new`n`n$changes`n$footer"

$files = @(
    "backend\pyproject.toml", "backend\src\agentfit\branding.py", "desktop\package.json", "desktop\package-lock.json",
    "desktop\src-tauri\Cargo.toml", "desktop\src-tauri\Cargo.lock", "desktop\src-tauri\tauri.conf.json"
)

Write-Host ""
Write-Host "Version : $base -> $next  (tag $tag, branch $branch)" -ForegroundColor Green
Write-Host "Asset   : dist\release\$assetName (+ .sha256)"
Write-Host "What's new:"
Write-Host $changes
if ($DryRun) {
    Write-Host "`nDry run: nothing was changed." -ForegroundColor Yellow
    return
}

# -- bump -----------------------------------------------------------------------------------------------------------------

Step "Write the version $next"
Replace-First "backend\pyproject.toml" '^version\s*=\s*"[^"]+"' "version = `"$next`""
Replace-First "backend\src\agentfit\branding.py" '^APP_VERSION\s*=\s*"[^"]+"' "APP_VERSION = `"$next`""
Replace-First "desktop\src-tauri\Cargo.toml" '^version\s*=\s*"[^"]+"' "version = `"$next`""
Replace-First "desktop\src-tauri\tauri.conf.json" '^(\s*)"version":\s*"[^"]+"' "`${1}`"version`": `"$next`""
Push-Location (Join-Path $root "desktop")
try { Run-Native "npm version" { npm version $next --no-git-tag-version --allow-same-version | Out-Null } } finally { Pop-Location }

# -- build ----------------------------------------------------------------------------------------------------------------

$started = Get-Date
Step "Build the installer"
if ($SkipTests) { & (Join-Path $PSScriptRoot "build.ps1") -SkipTests } else { & (Join-Path $PSScriptRoot "build.ps1") }

$installer = Join-Path $root "dist\WillItRun-Setup-x64.exe"
if (-not (Test-Path $installer) -or (Get-Item $installer).LastWriteTime -lt $started) { Fail "The build did not produce a new installer." }
$bundle = Get-ChildItem (Join-Path $root "desktop\src-tauri\target\release\bundle\nsis") -Filter "*_${next}_x64-setup.exe" | Select-Object -First 1
if (-not $bundle) { Fail "The installer does not carry the version ${next}: the version was not picked up by the build." }

$out = Join-Path $root "dist\release"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$asset = Join-Path $out $assetName
Copy-Item $installer $asset -Force
$hash = (Get-FileHash $asset -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText("$asset.sha256", "$hash *$assetName`n", (New-Object System.Text.UTF8Encoding($false)))
$notesPath = Join-Path $out "notes-$next.md"
Write-Text "dist\release\notes-$next.md" $notes

# -- commit, tag, push, release -------------------------------------------------------------------------------------------

Step "Commit and tag $tag"
foreach ($f in $files) { Run-Native "git add $f" { git add -- $f } }
$message = "Release $tag"
if ($CommitTrailer) { $message = "$message`n`n$CommitTrailer" }
Run-Native "git commit" { git commit -m $message }
Run-Native "git tag" { git tag -a $tag -m "Will It Run $next" }

Step "Push"
Run-Native "git push" { git push origin $branch }
Run-Native "git push tag" { git push origin $tag }

Step "GitHub release"
Run-Native "gh release create" { gh release create $tag --verify-tag --title "Will It Run $next" --notes-file $notesPath --latest $asset "$asset.sha256" }
$url = (gh release view $tag --json url --jq ".url").Trim()
Write-Host "`nReleased $next : $url" -ForegroundColor Green
