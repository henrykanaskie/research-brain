# Install or update research_brain on Windows as the `research-brain` command, separate from your data.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1          from a checkout (installs this folder)
#   $env:RB_SOURCE = "git+https://github.com/henrykanaskie/research-brain.git"; .\install.ps1
#
# Uses uv if you have it, else pipx, else a private virtual environment in your data folder whose Scripts
# folder is added to your user PATH. Re-running it updates the app. Your library is never touched: it
# lives in %APPDATA%\research_brain. Also adds "research brain" to the Start menu.
# Options (environment): RB_EMBEDDINGS=1 also installs sentence-transformers (better similarity, ~500 MB).
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src = $env:RB_SOURCE
if (-not $Src) {
  if ((Test-Path (Join-Path $Here "pyproject.toml")) -and (Test-Path (Join-Path $Here "app"))) { $Src = $Here }
  elseif (Test-Path (Join-Path $Here "latest.json")) { $Src = $Here }   # a release / the lab's update folder
  else { Write-Error "Set RB_SOURCE to a research_brain wheel, a release folder, or git+https://github.com/henrykanaskie/research-brain.git"; exit 2 }
}
$UpdateSource = ""
if ((Test-Path $Src -PathType Container) -and (Test-Path (Join-Path $Src "latest.json"))) {
  # a release folder: install the wheel latest.json names, and update from this folder from now on
  $UpdateSource = (Resolve-Path $Src).Path
  $Latest = Get-Content (Join-Path $UpdateSource "latest.json") -Raw | ConvertFrom-Json
  $Wheel = Join-Path $UpdateSource $Latest.wheel
  if (-not (Test-Path $Wheel)) { Write-Error "latest.json in $UpdateSource names $($Latest.wheel), which isn't there"; exit 2 }
  $Src = $Wheel
  if (Test-Path (Join-Path $UpdateSource "constraints.txt")) { $Here = $UpdateSource }
}
$Spec = $Src
if ($env:RB_EMBEDDINGS -eq "1") {
  $Url = if (Test-Path $Src) { "file:///" + ((Resolve-Path $Src).Path -replace '\\', '/') } else { $Src }
  $Spec = "research-brain[embeddings] @ $Url"
}
$Constraints = Join-Path $Here "constraints.txt"
if (-not (Test-Path $Constraints)) { $Constraints = "" }

function Find-Python {
  foreach ($c in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.10"), @("python"), @("python3"))) {
    $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { return , $c }
  }
  Write-Error "research_brain needs Python 3.10 or newer. Install it from python.org (tick 'Add python.exe to PATH') or run: winget install Python.Python.3.12"
  exit 1
}

function Add-UserPath($dir) {
  $cur = [Environment]::GetEnvironmentVariable("Path", "User")
  if (-not $cur) { $cur = "" }
  if (($cur -split ";") -notcontains $dir) {
    [Environment]::SetEnvironmentVariable("Path", ($cur.TrimEnd(";") + ";" + $dir).TrimStart(";"), "User")
    Write-Host "  Added $dir to your PATH. Open a new terminal to use research-brain there."
  }
  if (($env:Path -split ";") -notcontains $dir) { $env:Path = "$env:Path;$dir" }
}

# uv brings its own Python, so nobody has to install one first. Get it if it's missing (RB_NO_UV=1 skips).
if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and $env:RB_NO_UV -ne "1") {
  Write-Host "-> Installing uv (Astral's Python installer)"
  try {
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
    $UvBin = Join-Path $env:USERPROFILE ".local\bin"
    if (($env:Path -split ";") -notcontains $UvBin) { $env:Path = "$env:Path;$UvBin" }
  } catch { Write-Host "  Couldn't install uv; falling back to pipx or your Python." }
}
if (Get-Command uv -ErrorAction SilentlyContinue) {
  Write-Host "-> Installing with uv"
  # Python 3.12: what constraints.txt's pinned versions are built and tested for (uv downloads it if needed)
  if ($Constraints) { uv tool install --force --python 3.12 -c $Constraints $Spec } else { uv tool install --force --python 3.12 $Spec }
  uv tool update-shell 2>$null | Out-Null
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
  Write-Host "-> Installing with pipx"
  if ($Constraints) { pipx install --force --pip-args "-c `"$Constraints`"" $Spec } else { pipx install --force $Spec }
  pipx ensurepath | Out-Null
} else {
  $Py = Find-Python
  $Data = Join-Path $env:APPDATA "research_brain"
  $Venv = Join-Path $Data "app-venv"   # the app's code only; your libraries are in $Data\profiles
  Write-Host "-> Installing into $Venv"
  $exe = $Py[0]; $rest = @($Py | Select-Object -Skip 1)
  & $exe @rest -m venv $Venv
  $VPy = Join-Path $Venv "Scripts\python.exe"
  & $VPy -m pip install -q --upgrade pip
  # constraints.txt pins wheels built for Python 3.12+; an older Python gets the newest versions that fit it
  & $VPy -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
  if ($LASTEXITCODE -ne 0) { $Constraints = "" }
  if ($Constraints) { & $VPy -m pip install -q --upgrade -c $Constraints $Spec } else { & $VPy -m pip install -q --upgrade $Spec }
  Add-UserPath (Join-Path $Venv "Scripts")
}

$Cmd = Get-Command research-brain -ErrorAction SilentlyContinue
if (-not $Cmd) { Write-Error "research-brain was installed but isn't on PATH yet. Open a new terminal and run: research-brain"; exit 1 }
Write-Host "-> Installed: $(& $Cmd.Source --version)"
if ($UpdateSource) {
  & $Cmd.Source update --source $UpdateSource | Out-Null
  Write-Host "  Updates will come from $UpdateSource (research-brain update)."
}

# Start menu shortcut: opens research brain like an app only when you click it; it stops by itself a few
# minutes after its window closes (research-brain open). Nothing runs at login.
& $Cmd.Source install-app | ForEach-Object { Write-Host "  $_" }

$Agents = @("claude", "codex", "gemini", "copilot", "opencode", "ollama") | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue }
if (-not $Agents) {
  Write-Host "  No AI agent found yet (Claude Code, Codex, Gemini CLI, Copilot CLI or Ollama). Everything except"
  Write-Host "  critiques, explanations and Ask works without one. Claude Code: irm https://claude.ai/install.ps1 | iex"
}
Write-Host "  Start it with: research-brain        (or 'research brain' in the Start menu)"
