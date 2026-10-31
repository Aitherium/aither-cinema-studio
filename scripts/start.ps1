# Start the Aither Cinema Studio backend on http://127.0.0.1:8765 -- one command (PowerShell):
#   irm https://raw.githubusercontent.com/Aitherium/aither-cinema-studio/main/scripts/start.ps1 | iex
# Re-run it any time: it updates the checkout in place and starts again.
# Needs git and Python 3.10+. ComfyUI is expected at http://127.0.0.1:8188 (set $env:COMFY_BASE_URL,
# or $env:COMFY_API_KEY for Comfy Cloud, before running to change that).
$ErrorActionPreference = 'Stop'

$dir = if ($env:CINEMA_HOME) { $env:CINEMA_HOME } else { Join-Path $HOME 'aither-cinema-studio' }
$repo = 'https://github.com/Aitherium/aither-cinema-studio.git'
$pages = 'https://aitherium.github.io'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host 'Aither Cinema needs git: https://git-scm.com/downloads' -ForegroundColor Red; return
}
$py = $null
foreach ($cand in @('py', 'python', 'python3')) {
    $c = Get-Command $cand -ErrorAction SilentlyContinue
    if ($c -and $c.Source -notmatch 'WindowsApps') { $py = $c.Source; break }
}
if (-not $py) { Write-Host 'Aither Cinema needs Python 3.10+: https://www.python.org/downloads/' -ForegroundColor Red; return }
& $py -c 'import sys; sys.exit(sys.version_info < (3, 10))'
if ($LASTEXITCODE -ne 0) { Write-Host "Aither Cinema needs Python 3.10+ (found $(& $py -V 2>&1))" -ForegroundColor Red; return }

if (Test-Path (Join-Path $dir '.git')) {
    Write-Host "updating $dir"
    git -C $dir pull --ff-only -q
    if ($LASTEXITCODE -ne 0) { Write-Host '(could not update; starting the copy you have)' }
} else {
    Write-Host "downloading to $dir"
    git clone -q --depth 1 $repo $dir
    if ($LASTEXITCODE -ne 0) { Write-Host 'git clone failed' -ForegroundColor Red; return }
}

Set-Location (Join-Path $dir 'server')
$venvPy = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { & $py -m venv .venv }
Write-Host 'installing (first run takes a minute)'
& $venvPy -m pip install -q --disable-pip-version-check -e .
if ($LASTEXITCODE -ne 0) { Write-Host 'install failed' -ForegroundColor Red; return }

# The studio page is served from $pages; the backend must allow it, or the browser refuses.
$origins = @(($env:CINEMA_CORS_ORIGINS -split ',') | Where-Object { $_ })
if ($origins -notcontains $pages) { $origins += $pages }
$env:CINEMA_CORS_ORIGINS = $origins -join ','

Write-Host 'Aither Cinema backend -> http://127.0.0.1:8765  (Ctrl+C stops it; go back to the studio page and press Test)' -ForegroundColor Green
$studio = Join-Path (Get-Location) '.venv\Scripts\cinema-studio.exe'
if (Test-Path $studio) {
    & $studio --host 127.0.0.1 --port 8765 --no-browser
} else {
    & $venvPy -m uvicorn cinema.app:app --host 127.0.0.1 --port 8765
}
