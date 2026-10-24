<#
.SYNOPSIS
    Build the Will It Run Windows installer.

.DESCRIPTION
    1. Runs the Python and TypeScript checks (skip with -SkipTests).
    2. Freezes the Python core with PyInstaller into desktop\src-tauri\resources\willitrun-core.
    3. Builds the Tauri app and its NSIS installer.

    Output: dist\WillItRun-Setup-x64.exe

.EXAMPLE
    .\scripts\build.ps1
    .\scripts\build.ps1 -SkipTests
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$CoreOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$desktop = Join-Path $root "desktop"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$resources = Join-Path $desktop "src-tauri\resources\willitrun-core"

function Invoke-Step([string]$Name, [scriptblock]$Body) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path $python)) {
    throw "Python environment not found. Run: uv venv backend\.venv --python 3.13; uv pip install --python backend\.venv\Scripts\python.exe -e 'backend[dev]'"
}

if (-not $SkipTests) {
    Invoke-Step "Python lint" { & $python -m ruff check (Join-Path $backend "src") (Join-Path $backend "tests") }
    Invoke-Step "Python tests" { Push-Location $backend; try { & $python -m pytest -q } finally { Pop-Location } }
    Invoke-Step "Italian catalog" { & $python (Join-Path $root "scripts\build_locale.py") }
    Invoke-Step "Core strings translated" { & $python (Join-Path $root "scripts\list_core_strings.py") }
    Invoke-Step "TypeScript checks" { Push-Location $desktop; try { npm run typecheck; if ($LASTEXITCODE -eq 0) { npm run lint }; if ($LASTEXITCODE -eq 0) { npm run test } } finally { Pop-Location } }
}

Invoke-Step "Freeze the core (PyInstaller)" {
    & $python -m PyInstaller --noconfirm --clean --onedir --console --name willitrun-core `
        --distpath (Join-Path $backend "dist") --workpath (Join-Path $backend "build") --specpath (Join-Path $backend "build") `
        --collect-data agentfit `
        --collect-submodules uvicorn --collect-submodules langgraph --collect-submodules langchain_core `
        --collect-submodules anthropic --collect-submodules httpx2 `
        --copy-metadata langgraph --copy-metadata langchain-core --copy-metadata anthropic `
        --exclude-module tkinter --exclude-module pytest --exclude-module ruff `
        (Join-Path $backend "packaging\agentfit_core.py")
}

Invoke-Step "Smoke-test the frozen core" { & (Join-Path $backend "dist\willitrun-core\willitrun-core.exe") version }

Write-Host "==> Copy the core into the app resources" -ForegroundColor Cyan
if (Test-Path $resources) { Remove-Item -Recurse -Force $resources -Confirm:$false }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $resources) | Out-Null
Copy-Item -Recurse (Join-Path $backend "dist\willitrun-core") $resources

if ($CoreOnly) { Write-Host "Core built at $resources" -ForegroundColor Green; return }

Invoke-Step "Build the desktop app and installer (Tauri)" { Push-Location $desktop; try { npx tauri build } finally { Pop-Location } }

$installer = Get-ChildItem (Join-Path $desktop "src-tauri\target\release\bundle\nsis") -Filter "*-setup.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw "Installer not found after the Tauri build." }
$out = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$target = Join-Path $out "WillItRun-Setup-x64.exe"
Copy-Item $installer.FullName $target -Force
Write-Host ("Installer: {0} ({1:N1} MB)" -f $target, ((Get-Item $target).Length / 1MB)) -ForegroundColor Green
