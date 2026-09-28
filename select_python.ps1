<#
  select_python.ps1 - interactive Python interpreter picker for install_ping.bat

  Discovers installed python.exe candidates, shows a numbered menu, asks which
  interpreter to use for the ping monitors and for pathping, and offers to
  `pip install ping3` where it's missing.

  Menu and prompts use the console directly. The two chosen paths are written to
  the file given by -OutFile as:
      PING=<full path>
      PATHPING=<full path>
#>
param([Parameter(Mandatory = $true)][string]$OutFile)

$ErrorActionPreference = 'SilentlyContinue'

function Msg($text) { Write-Host $text }
function Ask($text) {
    # Read-Host, but if stdin is redirected/closed (piped install, < NUL) don't
    # block - just take the default.
    if ([Console]::IsInputRedirected) { Write-Host "$text(no console - using default)"; return '' }
    return (Read-Host -Prompt $text)
}

# ---- gather candidates -----------------------------------------------------
$paths = New-Object System.Collections.Generic.List[string]

foreach ($name in 'python.exe', 'python3.exe') {
    foreach ($c in (Get-Command $name -All)) { if ($c.Source) { $paths.Add($c.Source) } }
}

$py = Get-Command py.exe -ErrorAction SilentlyContinue
if ($py) {
    foreach ($line in (& $py.Source '-0p' 2>$null)) {
        $m = [regex]::Match($line, '([A-Za-z]:\\[^\r\n]*?python\.exe)')
        if ($m.Success) { $paths.Add($m.Groups[1].Value) }
    }
}

foreach ($glob in @(
    'C:\Program Files\Python3*\python.exe',
    'C:\Program Files (x86)\Python3*\python.exe',
    "$env:LocalAppData\Programs\Python\Python3*\python.exe",
    "$env:ProgramData\chocolatey\bin\python.exe"
)) {
    foreach ($f in (Get-ChildItem -Path $glob -ErrorAction SilentlyContinue)) { $paths.Add($f.FullName) }
}

if ($env:PYTHON_EXE -and (Test-Path $env:PYTHON_EXE)) { $paths.Add($env:PYTHON_EXE) }

# ---- normalise + dedupe + probe -----------------------------------------------
$seen  = @{}
$cands = @()
foreach ($p in $paths) {
    if (-not $p) { continue }
    try { $full = (Resolve-Path -LiteralPath $p -ErrorAction Stop).Path } catch { continue }
    $key = $full.ToLowerInvariant()
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true

    $ver = (& $full -c "import sys;print('%d.%d.%d'%sys.version_info[:3])" 2>$null)
    if (-not $ver) { continue }   # not a working interpreter
    & $full -c "import ping3" 2>$null | Out-Null
    $hasPing3 = ($LASTEXITCODE -eq 0)

    $cands += [pscustomobject]@{ Path = $full; Version = $ver; Ping3 = $hasPing3 }
}

if ($cands.Count -eq 0) {
    Msg "No working python.exe found. Install Python (python.org) and re-run install_ping.bat."
    exit 1
}

Msg ""
Msg "Detected Python interpreters:"
for ($i = 0; $i -lt $cands.Count; $i++) {
    $c   = $cands[$i]
    $tag = if ($c.Ping3) { "ping3 OK" } else { "ping3 MISSING" }
    Msg ("  {0}) {1}  (v{2}, {3})" -f ($i + 1), $c.Path, $c.Version, $tag)
}
Msg ""

function Pick-One($label, $default) {
    while ($true) {
        $ans = Ask $label
        if ([string]::IsNullOrWhiteSpace($ans)) { return $default }
        $n = 0
        if ([int]::TryParse($ans.Trim(), [ref]$n) -and $n -ge 1 -and $n -le $cands.Count) {
            return $cands[$n - 1]
        }
        Msg "  enter a number between 1 and $($cands.Count)"
    }
}

$pingPick = Pick-One "Python for the ping monitors [number]: " $cands[0]
$ppPick   = Pick-One "Python for pathping [number, Enter = same as ping]: " $pingPick

foreach ($pick in @($pingPick, $ppPick | Select-Object -Unique)) {
    if (-not $pick.Ping3) {
        $yn = Ask "ping3 missing for $($pick.Path) - run 'pip install ping3' now? [Y/N]: "
        if ($yn -match '^\s*[Yy]') {
            & $pick.Path -m pip install ping3 2>&1 | ForEach-Object { Msg $_ }
        }
    }
}

Set-Content -LiteralPath $OutFile -Encoding ASCII -Value @(
    "PING=$($pingPick.Path)"
    "PATHPING=$($ppPick.Path)"
)
