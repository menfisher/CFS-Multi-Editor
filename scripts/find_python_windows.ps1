$ErrorActionPreference = 'SilentlyContinue'
$min = [version]'3.12.0'
$found = @()

function Get-PythonVersion {
    param([string]$Exe)
    try {
        $out = & $Exe -c 'import sys; print("{}.{}".format(sys.version_info[0], sys.version_info[1]))' 2>$null
        if (-not $out) { return $null }
        $v = [version]$out.ToString().Trim()
        if ($v.Major -eq 3 -and $v -ge $min) { return $v }
    } catch {}
    return $null
}

function Add-Candidate {
    param([string]$Exe, [version]$Version)
    $script:found += [pscustomobject]@{
        Version = $Version
        Command = $Exe.Trim()
    }
}

$legacyRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
$managerRoot = Join-Path $env:LOCALAPPDATA 'Python'
foreach ($item in @(
    @{ Root = $managerRoot; Filter = 'pythoncore-3*' },
    @{ Root = $legacyRoot; Filter = 'Python3*' },
    @{ Root = $env:ProgramFiles; Filter = 'Python3*' },
    @{ Root = ${env:ProgramFiles(x86)}; Filter = 'Python3*' }
)) {
    $root = $item.Root
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
    Get-ChildItem -LiteralPath $root -Directory -Filter $item.Filter -ErrorAction SilentlyContinue | ForEach-Object {
        $exe = Join-Path $_.FullName 'python.exe'
        if (Test-Path -LiteralPath $exe) {
            $v = Get-PythonVersion $exe
            if ($v) { Add-Candidate $exe $v }
        }
    }
}
$managerBin = Join-Path $managerRoot 'bin\python.exe'
if (Test-Path -LiteralPath $managerBin) {
    $v = Get-PythonVersion $managerBin
    if ($v) { Add-Candidate $managerBin $v }
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($line in (& py -0p 2>$null)) {
        if ($line -match '-V:\S+\s+(.+\.exe)\s*$') {
            $p = $Matches[1].Trim()
            if (Test-Path -LiteralPath $p) {
                $v = Get-PythonVersion $p
                if ($v) { Add-Candidate $p $v }
            }
        }
    }
    foreach ($minor in 15, 14, 13, 12) {
        $tag = "3.$minor"
        & py "-$tag" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $exe = (& py "-$tag" -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
            if ($exe) {
                $exe = $exe.ToString().Trim()
                $v = Get-PythonVersion $exe
                if ($v) { Add-Candidate $exe $v }
            }
        }
    }
}

if (Get-Command where.exe -ErrorAction SilentlyContinue) {
    foreach ($p in (& where.exe python 2>$null)) {
        $p = $p.Trim()
        if ($p -match '\\WindowsApps\\') { continue }
        if ($p -and (Test-Path -LiteralPath $p)) {
            $v = Get-PythonVersion $p
            if ($v) { Add-Candidate $p $v }
        }
    }
}

if ($found.Count -gt 0) {
    ($found | Sort-Object Version -Descending | Select-Object -First 1).Command
}
