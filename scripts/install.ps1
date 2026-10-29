# Omo Space installer for Windows 10/11 (x64).
#
# From an unpacked release, in PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\install.ps1
# From a downloaded zip:
#     powershell -ExecutionPolicy Bypass -File .\install.ps1 omo-space-<ver>-windows.zip
#
# Minecraft, Java and Prism run natively on Windows. The session server
# (tmux), the bridge and Hermes run inside WSL2, so it must be set up once:
#     wsl --install            (admin PowerShell, then restart)
#     inside WSL: sudo apt install -y tmux python3, and Hermes Agent
#     /etc/wsl.conf: [boot] systemd=true, then `wsl --shutdown`
# The game reaches the bridge on 127.0.0.1 through WSL's localhost forwarding.
#
# Installs the app into %LOCALAPPDATA%\OmoResearch\app, adds a Start menu
# shortcut, then runs the launcher's setup. Needs no admin. Never touches your
# own Minecraft/Prism installs, worlds or Hermes configuration.
#
#   -NoSetup      install the files only; run setup later
#   -Uninstall    remove the app and shortcut (keeps worlds and workspaces)
param([string]$Source = "", [switch]$NoSetup, [switch]$Uninstall)
$ErrorActionPreference = "Stop"

$Data = Join-Path $env:LOCALAPPDATA "OmoResearch"
$App = Join-Path $Data "app"
$Programs = [Environment]::GetFolderPath("Programs")
if (-not $Programs) { $Programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs" }
$Shortcut = Join-Path $Programs "Omo Space.lnk"
$OldShortcut = Join-Path $Programs "Omo Research.lnk"  # the name before the rename
# ASCII only: Windows PowerShell 5.1 reads a BOM-less script as ANSI.
function Banner($sub) { Write-Host ""; Write-Host "  * " -ForegroundColor DarkYellow -NoNewline; Write-Host "Omo Space" -ForegroundColor White; Write-Host "    $sub" -ForegroundColor DarkGray; Write-Host "" }
function Say($m) { Write-Host "  > " -ForegroundColor DarkYellow -NoNewline; Write-Host $m }
function Ok($m) { Write-Host "  + " -ForegroundColor Green -NoNewline; Write-Host $m }
function Warn($m) { Write-Host "  ! " -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Note($m) { Write-Host "    $m" -ForegroundColor DarkGray }
function Die($m) { Write-Host ""; Write-Host "  x $m" -ForegroundColor Red; Write-Host ""; exit 1 }

if ($Uninstall) {
    Remove-Item -Recurse -Force $App, $Shortcut, $OldShortcut -ErrorAction SilentlyContinue
    Banner "Uninstalling. Your worlds and workspaces stay."
    Ok "Removed the app and the Start menu shortcut"
    Note "Kept $Data (game, worlds, token)."
    Note "The services in WSL keep running. Stop them with:"
    Note "  wsl systemctl --user disable --now omo-bridge omo-tmux"
    Write-Host ""
    exit 0
}

if (-not [Environment]::Is64BitOperatingSystem) { Die "64-bit Windows is required." }
# The Python launcher `py` if present, else `python` (or `python3` elsewhere).
$PyExe = $null; $PyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $PyExe = "py"; $PyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $PyExe = "python" }
elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $PyExe = "python3" }
function Py { & $PyExe @PyArgs @args }
if (-not $PyExe) { Die "Python 3.11+ is required: winget install Python.Python.3.12" }
Py -c "import sys; sys.exit(sys.version_info < (3, 11))" 2>$null
if ($LASTEXITCODE -ne 0) { Die "Python 3.11+ is required: winget install Python.Python.3.12" }
if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
    Die "WSL2 is required for the session server: run 'wsl --install' in an admin PowerShell, restart, then run this again."
}

$Here = $PSScriptRoot
$Tmp = $null
if ($Source) {
    if (-not (Test-Path $Source)) { Die "no such file: $Source" }
    $Tmp = Join-Path ([IO.Path]::GetTempPath()) ("omo-" + [guid]::NewGuid())
    Expand-Archive -Path $Source -DestinationPath $Tmp
    $Here = (Get-ChildItem $Tmp -Directory | Select-Object -First 1).FullName
}
if (-not (Test-Path (Join-Path $Here "launcher\omo_setup.py"))) { Die "run this from an unpacked release, or pass the release .zip" }

Banner "Your AI workspace, built in Minecraft"
Ok ("Windows x64 - " + (Py -c "import platform; print('Python', platform.python_version())"))
Say "Installing the app into $App"
$New = "$App.new"
Remove-Item -Recurse -Force $New, "$App.old" -ErrorAction SilentlyContinue
Copy-Item -Recurse $Here $New
if (Test-Path $App) { Rename-Item $App "$App.old" }
Rename-Item $New $App
Remove-Item -Recurse -Force "$App.old" -ErrorAction SilentlyContinue
if ($Tmp) { Remove-Item -Recurse -Force $Tmp }
Ok "App installed"

Remove-Item -Force $OldShortcut -ErrorAction SilentlyContinue
$pyw = (Py -c "import sys, pathlib; p = pathlib.Path(sys.executable); w = p.with_name('pythonw.exe'); print(w if w.exists() else p)").Trim()
try {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($Shortcut)
    $lnk.TargetPath = $pyw
    $lnk.Arguments = '"' + (Join-Path $App "launcher\omo-launcher.py") + '"'
    $lnk.WorkingDirectory = $App
    $lnk.Description = "Hermes and your terminals, inside Minecraft"
    $lnk.Save()
    Ok "Added 'Omo Space' to the Start menu"
} catch {
    Warn "could not add the Start menu shortcut ($($_.Exception.Message)); start it with: $pyw `"$App\launcher\omo-launcher.py`""
}

if (-not $NoSetup) {
    Say "Setup: downloading the pinned game runtime (~650 MB on first run)"
    Note "Minecraft, Fabric, CC: Tweaked, MCEF and Java, each from its official source, hash-checked."
    Write-Host ""
    Py (Join-Path $App "launcher\omo_setup.py") setup
    if ($LASTEXITCODE -ne 0) { Die "setup failed; the last line above says why. Fix it, then run: omo_setup.py setup" }
}
Write-Host ""
Write-Host "  + Omo Space is installed." -ForegroundColor White
Note "Open 'Omo Space' from the Start menu."
if ($NoSetup) { Note "Setup was skipped (-NoSetup). The launcher offers it the first time you open it." }
Write-Host ""
