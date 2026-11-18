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

# A program's own messages (uv writes its progress to stderr) must never stop this script: in Windows PowerShell
# 5.1 a redirected stderr line becomes an error, and "Stop" would end the install right after it worked. Run
# programs through Native, and judge them by their exit code.
function Native([scriptblock]$Run) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { & $Run } finally { $ErrorActionPreference = $old }
}
function Fail($Message) {
  Write-Host ""
  Write-Host "  $Message" -ForegroundColor Red
  Write-Host "  Your library wasn't touched. Running this installer again is always safe."
  exit 1
}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src = $env:RB_SOURCE
if (-not $Src) {
  if ((Test-Path (Join-Path $Here "pyproject.toml")) -and (Test-Path (Join-Path $Here "app"))) { $Src = $Here }
  elseif (Test-Path (Join-Path $Here "latest.json")) { $Src = $Here }   # a release / a shared update folder
  else { Write-Error "Set RB_SOURCE to a research_brain wheel, a release folder, or git+https://github.com/henrykanaskie/research-brain.git"; exit 2 }
}
$UpdateSource = ""
if ((Test-Path $Src -PathType Container) -and (Test-Path (Join-Path $Src "latest.json"))) {
  # a release folder (the unzipped installer): install the wheel latest.json names
  $UpdateSource = (Resolve-Path $Src).Path
  $Latest = Get-Content (Join-Path $UpdateSource "latest.json") -Raw | ConvertFrom-Json
  $Wheel = Join-Path $UpdateSource $Latest.wheel
  if (-not (Test-Path $Wheel)) { Write-Error "latest.json in $UpdateSource names $($Latest.wheel), which isn't there"; exit 2 }
  $Src = $Wheel
  if (Test-Path (Join-Path $UpdateSource "constraints.txt")) { $Here = $UpdateSource }
}
# Shared drives have spaces in their paths ("OneDrive - University", "My Drive"), and uv reads a wheel path with a
# space as two arguments. Install from a copy in a temporary folder without spaces.
if (($Src -match ' ') -and (Test-Path $Src -PathType Leaf)) {
  $RbTmp = Join-Path ([System.IO.Path]::GetTempPath()) ("rb-install-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
  New-Item -ItemType Directory -Path $RbTmp -Force | Out-Null
  Copy-Item $Src $RbTmp
  $Src = Join-Path $RbTmp (Split-Path $Src -Leaf)
  if (Test-Path (Join-Path $Here "constraints.txt")) { Copy-Item (Join-Path $Here "constraints.txt") $RbTmp; $Here = $RbTmp }
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
    Native { & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null | Out-Null }
    if ($LASTEXITCODE -eq 0) { return , $c }
  }
  Fail ("Couldn't get the tools research brain installs with. Usually that means no internet connection, or a " +
        "university or antivirus setting blocking the download. Connect to the internet and run this again. If it " +
        "still stops here, install Python 3.12 from python.org (tick 'Add python.exe to PATH') and run this again.")
}

# A research brain that's running holds its files open, and Windows won't replace open files: ask it to stop
# first (the window closes; your library is saved as you go), then wait for it to let go.
$Port = if ($env:RB_PORT) { $env:RB_PORT } else { "8765" }
$WasRunning = $false
try {
  Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$Port/api/quit" -ContentType "application/json" -Body "{}" -TimeoutSec 3 | Out-Null
  $WasRunning = $true
  Write-Host "-> Closing the research brain that's running, to update it"
} catch { }
if ($WasRunning) {
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 1 | Out-Null } catch { break }
  }
  Start-Sleep -Seconds 1
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
    Native { powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Out-Null }
    $UvBin = Join-Path $env:USERPROFILE ".local\bin"
    if (($env:Path -split ";") -notcontains $UvBin) { $env:Path = "$env:Path;$UvBin" }
  } catch { }
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  Couldn't download uv (no internet, or blocked by a university or antivirus setting). Trying Python instead."
  }
}
if (Get-Command uv -ErrorAction SilentlyContinue) {
  Write-Host "-> Installing with uv"
  # Python 3.12: what constraints.txt's pinned versions are built and tested for (uv downloads it if needed)
  # --compile-bytecode: compile now, so the first start after installing isn't seconds slower than the rest
  Native { if ($Constraints) { uv tool install --force --compile-bytecode --python 3.12 -c $Constraints $Spec } else { uv tool install --force --compile-bytecode --python 3.12 $Spec } }
  if ($LASTEXITCODE -ne 0) {
    Fail ("Installing didn't finish (what went wrong is just above). Usually: no internet connection, an antivirus " +
          "blocking the download, or research brain still running (close its window, wait a minute, and run this again).")
  }
  Native { uv tool update-shell 2>&1 | Out-Null }
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
  Write-Host "-> Installing with pipx"
  Native { if ($Constraints) { pipx install --force --pip-args "-c `"$Constraints`"" $Spec } else { pipx install --force $Spec } }
  if ($LASTEXITCODE -ne 0) { Fail "Installing didn't finish (what went wrong is just above). Check the internet connection and run this again." }
  Native { pipx ensurepath 2>&1 | Out-Null }
} else {
  $Py = Find-Python
  $Data = Join-Path $env:APPDATA "research_brain"
  $Venv = Join-Path $Data "app-venv"   # the app's code only; your libraries are in $Data\profiles
  Write-Host "-> Installing into $Venv"
  $exe = $Py[0]; $rest = @($Py | Select-Object -Skip 1)
  Native { & $exe @rest -m venv $Venv }
  if ($LASTEXITCODE -ne 0) { Fail "Couldn't set up research brain's own Python in $Venv (what went wrong is just above)." }
  $VPy = Join-Path $Venv "Scripts\python.exe"
  Native { & $VPy -m pip install -q --upgrade pip }
  # constraints.txt pins wheels built for Python 3.12+; an older Python gets the newest versions that fit it
  Native { & $VPy -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" }
  if ($LASTEXITCODE -ne 0) { $Constraints = "" }
  Native { if ($Constraints) { & $VPy -m pip install -q --upgrade -c $Constraints $Spec } else { & $VPy -m pip install -q --upgrade $Spec } }
  if ($LASTEXITCODE -ne 0) { Fail "Installing didn't finish (what went wrong is just above). Check the internet connection and run this again." }
  Add-UserPath (Join-Path $Venv "Scripts")
}

$Cmd = Get-Command research-brain -ErrorAction SilentlyContinue
if (-not $Cmd) { Fail "research brain was installed, but Windows can't find it yet. Restart the computer, then open 'research brain' from the Start menu." }
$Have = (Native { & $Cmd.Source --version 2>$null }) -join " "
Write-Host "-> Installed: $Have"
if ($Latest -and $Latest.version -and ($Have -notmatch [regex]::Escape([string]$Latest.version))) {
  Fail "This installer has version $($Latest.version), but $Have is still the one installed: the update didn't take. Close research brain, wait a minute, and run this again."
}
Write-Host "  To update later: run a newer version's installer the same way. Your library stays as it is."

# Start menu shortcut: opens research brain like an app only when you click it; it stops by itself a few
# minutes after its window closes (research-brain open). Nothing runs at login.
Native { & $Cmd.Source install-app 2>&1 | ForEach-Object { Write-Host "  $_" } }

$Agents = @("claude", "codex", "gemini", "copilot", "opencode", "ollama") | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue }
if (-not $Agents) {
  Write-Host "  No AI set up yet. That's fine: everything works without one except asking questions, critiques and"
  Write-Host "  explanations. The free option is Gemini: README, 'Add an AI' (or click the AI name at the top of the app)."
}
Write-Host "  Start it with: research-brain        (or 'research brain' in the Start menu)"
