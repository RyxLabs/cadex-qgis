# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
<#
Runs the CADex tests on Windows with the Python of a QGIS installation: every test module on
the checkout, then on the release zip extracted beside the tests, then the qgis_process check
of the zip.

  windows_tests.ps1 -Plugin <...\cadex> -Zip <...\cadex.<version>.zip>
  ... -QgisRoot "C:\Program Files\QGIS 3.40.6"   one installation; without it C:\OSGeo4W and
                                                 "C:\Program Files\QGIS *" are searched
  ... -Modules "test_roles*", "test_xlsx"        only these modules
#>
param(
    [Parameter(Mandatory)][string]$Plugin,
    [Parameter(Mandatory)][string]$Zip,
    [string[]]$QgisRoot = @("C:\OSGeo4W", (Join-Path $env:ProgramFiles "QGIS *")),
    [string[]]$Modules = @()
)
$ErrorActionPreference = "Stop"
$Plugin = (Resolve-Path $Plugin).Path
$Zip = (Resolve-Path $Zip).Path

# python-qgis-ltr.bat in a long-term release, python-qgis.bat in the others; with several
# installations the last by path, which is not always the newest: pass -QgisRoot to choose
$python = $QgisRoot |
    ForEach-Object { Get-Item (Join-Path $_ "bin\python-qgis*.bat") -ErrorAction SilentlyContinue } |
    Sort-Object FullName | Select-Object -Last 1
if (-not $python) { throw "No bin\python-qgis*.bat under: $($QgisRoot -join ', ')" }
$qgisProcess = Join-Path $python.DirectoryName ($python.Name -replace "^python-", "qgis_process-")
if (-not (Test-Path $qgisProcess)) { throw "$qgisProcess is missing" }
"QGIS Python: $($python.FullName)"

function Invoke-QgisPython {
    & $python.FullName @args
    if ($LASTEXITCODE -ne 0) { throw "$($args[0]) ended with exit code $LASTEXITCODE" }
}

$runner = Join-Path $Plugin ".github\scripts\run_tests.py"
Invoke-QgisPython $runner $Plugin @Modules

# the zip's files, with what the tests need from the checkout beside them
$work = Join-Path ([IO.Path]::GetTempPath()) ("cadex_zip_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
try {
    Expand-Archive -Path $Zip -DestinationPath $work
    $release = Join-Path $work "cadex"
    foreach ($name in "test", "scripts", ".github", "CHANGELOG.md") {
        Copy-Item -Path (Join-Path $Plugin $name) -Destination $release -Recurse
    }
    Invoke-QgisPython $runner $release @Modules
}
finally {
    if (Test-Path $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}

Invoke-QgisPython (Join-Path $Plugin ".github\scripts\qgis_process_smoke.py") $Zip $qgisProcess
