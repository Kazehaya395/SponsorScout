$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Get-Version {
    $pyproject = Join-Path $Root 'pyproject.toml'

    if (-not (Test-Path $pyproject)) {
        throw "pyproject.toml not found: $pyproject"
    }

    $text = Get-Content -Raw -Path $pyproject

    if ($text -match '(?m)^\s*version\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }

    throw 'Unable to read version from pyproject.toml'
}

$AppName    = 'SponsorScout'
$Version    = Get-Version
$DistDir    = Join-Path $Root 'dist'
$BuildDir   = Join-Path $DistDir $AppName
$ExePath    = Join-Path $BuildDir "$AppName.exe"

# Detect python launcher
if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = 'py'
    $PythonArgs = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = 'python'
    $PythonArgs = @()
} else {
    throw 'python or py launcher not found in PATH'
}

Write-Host "[1/4] Installing build dependencies..." -ForegroundColor Cyan
# Repair pip if it is in a broken state (e.g. a half-finished self-upgrade
# left pip._internal.operations.build missing). Detect the breakage, delete
# the truncated package, then re-bootstrap pip from its bundled wheel.
$RepairNeeded = $true
try {
    & $Python @PythonArgs -c "import pip._internal.operations.build" 2>$null
    if ($LASTEXITCODE -eq 0) { $RepairNeeded = $false }
} catch { }
if ($RepairNeeded) {
    Write-Host "Repairing broken pip installation..." -ForegroundColor Yellow
    $Site = (& $Python @PythonArgs -c "import site,sys; print(site.getsitepackages()[0])" 2>$null)
    if ($Site) {
        Get-ChildItem -Path $Site -Filter 'pip*' -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
    & $Python @PythonArgs -m ensurepip --upgrade 2>$null
    if ($LASTEXITCODE -ne 0) { & $Python @PythonArgs -m ensurepip 2>$null }
}
& $Python @PythonArgs -m pip install -r requirements.txt | Out-Null

# Fail fast when a dependency installed but is not actually usable. A broken
# greenlet (its compiled extension missing) makes `playwright.sync_api` raise
# ModuleNotFoundError, which the scanner swallows and later reports as an opaque
# "Playwright is required for DOM fallback" for every target.
& $Python @PythonArgs -c "from playwright.sync_api import sync_playwright" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "playwright is not usable inside $Python (broken/missing dependency). Re-create the venv and retry."
}

Write-Host "[2/4] Building SponsorScout.exe with PyInstaller..." -ForegroundColor Cyan
# Only remove our own PyInstaller output: dist/ also holds the .deb and .rpm
# artifacts, which must survive so all three formats can be built side by side.
if (Test-Path $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}

& $Python @PythonArgs -m PyInstaller `
    --clean `
    --noconfirm `
    --windowed `
    --onedir `
    --name $AppName `
    --icon sponsorscout/data/sponsorscout.ico `
    --collect-data sponsorscout `
    --collect-submodules sponsorscout `
    --collect-all playwright `
    --collect-submodules PySide6 `
    --hidden-import greenlet `
    --hidden-import pyee `
    --exclude-module pandas `
    --exclude-module PIL `
    --exclude-module bs4 `
    --exclude-module lxml `
    --exclude-module tkinter `
    --exclude-module pytest `
    sponsorscout/main.py

if (-not (Test-Path $ExePath)) {
    throw "PyInstaller did not produce $ExePath"
}
Write-Host "Built $ExePath (version $Version)" -ForegroundColor Green

# Install Playwright's Chromium DIRECTLY into the bundle's `_playwright`
# directory so it ships inside the installer and sponsorscout/paths.py can
# point PLAYWRIGHT_BROWSERS_PATH at it on the user's machine (first launch,
# offline-capable). Previously this downloaded to the build machine's cache
# only, so the installed app had no browser at all and every `provider=auto`
# career target failed with "Executable doesn't exist".
$BundledPlaywright = Join-Path $BuildDir '_playwright'
Write-Host "[3/4] Installing Chromium into bundle ($BundledPlaywright)..." -ForegroundColor Cyan
$env:PLAYWRIGHT_BROWSERS_PATH = $BundledPlaywright
try {
    & $Python @PythonArgs -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed (exit $LASTEXITCODE)" }
} finally {
    Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
}
if (-not (Test-Path $BundledPlaywright)) {
    throw "Playwright browsers were NOT installed into $BundledPlaywright - career scanning would be broken in the packaged app."
}
Write-Host "Bundled Chromium verified at $BundledPlaywright" -ForegroundColor Green

# ── Smoke test ──────────────────────────────────────────────────────────────
# Run the freshly built exe BEFORE packaging it. A bundle that cannot import
# Playwright (or cannot launch its bundled Chromium) used to sail through the
# build and then fail 200+ scan targets at runtime with
# "Playwright is required for DOM fallback". Fail here instead.
Write-Host "[3.5/4] Smoke-testing the packaged app (Playwright + Chromium)..." -ForegroundColor Cyan
$env:PLAYWRIGHT_BROWSERS_PATH = $BundledPlaywright
try {
    & $ExePath --self-check --self-check-browser
    $SelfCheckExit = $LASTEXITCODE
} finally {
    Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
}
if ($SelfCheckExit -ne 0) {
    throw "Packaged app failed --self-check (exit $SelfCheckExit). Career scanning would be broken in the installed app."
}
Write-Host "Packaged app self-check passed." -ForegroundColor Green

# ── Size reduction: caches / metadata only ─────────────────────────────────
# Same safe set as build_deb.sh. Deletes generated caches and package metadata
# that are never needed at runtime. NO binaries are stripped (stripping the
# *.pyd/*.dll C-extensions breaks PySide6/Playwright at runtime).
$InternalDir = Join-Path $BuildDir '_internal'

Get-ChildItem -Path $InternalDir -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.PSIsContainer -and $_.Name -eq '__pycache__' } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $InternalDir -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { -not $_.PSIsContainer -and $_.Extension -eq '.pyc' } |
    Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $InternalDir -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.PSIsContainer -and $_.Name -like '*.dist-info' } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Remove Playwright's bundled ffmpeg (video recording only — never used),
# ~3 MB.
Get-ChildItem -Path $BundledPlaywright -Directory -Filter 'ffmpeg*' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Bundle cleanup complete (caches, *.pyc, *.dist-info, _playwright/ffmpeg-*)."

# Prepare installer source directory with the entire onedir build output.
# Playwright's Chromium IS bundled for offline use — it will be installed to
# %LOCALAPPDATA%\ms-playwright automatically during first run from the bundled
# _playwright directory, or used directly if PLAYWRIGHT_BROWSERS_PATH is set.
$InstallerSrcDir = Join-Path $DistDir "$AppName-InstallerFiles"
if (Test-Path $InstallerSrcDir) {
    Remove-Item -Recurse -Force $InstallerSrcDir
}

# Create the directory explicitly and copy contents using a wildcard to prevent nesting
New-Item -ItemType Directory -Path $InstallerSrcDir -Force | Out-Null
Copy-Item -Path "$BuildDir\*" -Destination $InstallerSrcDir -Recurse -Force

# Shortcut / Add-Remove icon: installer.iss references {app}\sponsorscout.ico
# for the Start-menu + desktop shortcut (IconFilename) and the Add/Remove
# Programs entry (UninstallDisplayIcon = {app}\sponsorscout.ico). Without the
# .ico at the bundle root that path is missing, so Windows falls back to the
# generic blank-page icon. Copy it here so all three references resolve.
$BundleIco = Join-Path $InstallerSrcDir 'sponsorscout.ico'
$SourceIco = Join-Path $Root 'sponsorscout\data\sponsorscout.ico'
if (-not (Test-Path $SourceIco)) {
    throw "App icon not found at $SourceIco - shortcuts would install with no icon."
}
Copy-Item -Path $SourceIco -Destination $BundleIco -Force
Write-Host "Copied $SourceIco -> $BundleIco" -ForegroundColor Green

# Verify _playwright directory with Chromium binaries exists in the bundle
# (installed by step [3/4] above). The installer.iss sets
# PLAYWRIGHT_BROWSERS_PATH to {app}\_playwright and sponsorscout/paths.py
# falls back to exe_dir\_playwright, so this directory is mandatory.
$PlaywrightInBundle = Join-Path $InstallerSrcDir '_playwright'
if (-not (Test-Path $PlaywrightInBundle)) {
    throw "Playwright browsers missing from bundle at $PlaywrightInBundle - refusing to build an installer that cannot scan SPA career portals."
}

# Step 4: Build Inno Setup installer if ISCC.exe is available
$Iscc = $null

$cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
$CmdSource = $null
if ($cmd) {
    $CmdSource = $cmd.Source
}

$IsccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 7\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
    $CmdSource
)

foreach ($candidate in $IsccCandidates) {
    if ($candidate -and (Test-Path $candidate)) {
        $Iscc = $candidate
        break
    }
}

if ($Iscc) {
    Write-Host "[4/4] Building Inno Setup installer..." -ForegroundColor Cyan
    $installerOut = Join-Path $DistDir "sponsorscout-$Version-setup.exe"

    # /F flag must match OutputBaseFilename in installer.iss
    & $Iscc "/DMyAppVersion=$Version" "/O$DistDir" "/Fsponsorscout-$Version-setup" (Join-Path $Root 'installer.iss')

    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compiler failed (exit $LASTEXITCODE)."
    }

    if (Test-Path $installerOut) {
        Write-Host "Built $installerOut" -ForegroundColor Green
    } else {
        Write-Host "Inno Setup ran but expected output not found at $installerOut" -ForegroundColor Yellow
    }
}
else {
    Write-Host "[4/4] Inno Setup not found - installer skipped." -ForegroundColor Yellow
    Write-Host "      Install Inno Setup 6 from https://jrsoftware.org/isinfo.php to produce a" -ForegroundColor Yellow
    Write-Host "      proper Windows installer (dist\sponsorscout-$Version-setup.exe)." -ForegroundColor Yellow
    Write-Host "      The raw SponsorScout.exe and Chromium are still in $BuildDir." -ForegroundColor Yellow
}

if (Test-Path $InstallerSrcDir) {
    Remove-Item -Recurse -Force $InstallerSrcDir
}

Write-Host ""
Write-Host "Done. Artifacts in $DistDir :"
Get-ChildItem $DistDir | ForEach-Object { Write-Host ("  " + $_.Name) }
# Report the raw onedir bundle size so slim-down gains are measurable.
$bundleMB = (Get-ChildItem -Path $BuildDir -Recurse -Force -ErrorAction SilentlyContinue |
             Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("Raw onedir bundle size: " + [math]::Round($bundleMB, 1) + " MB")
