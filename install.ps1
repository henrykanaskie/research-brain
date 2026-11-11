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
  else { Write-Error "Set RB_SOURCE to a research_brain wheel, or git+https://github.com/henrykanaskie/research-brain.git"; exit 2 }
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

if (Get-Command uv -ErrorAction SilentlyContinue) {
  Write-Host "-> Installing with uv"
  if ($Constraints) { uv tool install --force --python ">=3.10" -c $Constraints $Spec } else { uv tool install --force --python ">=3.10" $Spec }
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
  if ($Constraints) { & $VPy -m pip install -q --upgrade -c $Constraints $Spec } else { & $VPy -m pip install -q --upgrade $Spec }
  Add-UserPath (Join-Path $Venv "Scripts")
}

$Cmd = Get-Command research-brain -ErrorAction SilentlyContinue
if (-not $Cmd) { Write-Error "research-brain was installed but isn't on PATH yet. Open a new terminal and run: research-brain"; exit 1 }
Write-Host "-> Installed: $(& $Cmd.Source --version)"

# Start menu shortcut: starts the app and opens it in your browser (close the window to stop it)
try {
  $Programs = [Environment]::GetFolderPath("Programs")
  $Lnk = Join-Path $Programs "research brain.lnk"
  $Shell = New-Object -ComObject WScript.Shell
  $S = $Shell.CreateShortcut($Lnk)
  $S.TargetPath = $Cmd.Source
  $S.WorkingDirectory = $env:USERPROFILE
  $S.Description = "research brain: your research library"
  $S.WindowStyle = 7   # minimized
  $S.Save()
  Write-Host "  Added 'research brain' to the Start menu."
} catch { Write-Host "  (Couldn't add a Start menu shortcut: $($_.Exception.Message))" }

$Agents = @("claude", "codex", "gemini", "copilot", "opencode", "ollama") | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue }
if (-not $Agents) {
  Write-Host "  No AI agent found yet (Claude Code, Codex, Gemini CLI, Copilot CLI or Ollama). Everything except"
  Write-Host "  critiques, explanations and Ask works without one. Claude Code: irm https://claude.ai/install.ps1 | iex"
}
Write-Host "  Start it with: research-brain        (or 'research brain' in the Start menu)"
