# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
<#
Installs QGIS from the OSGeo4W repository without questions, for a CI runner: it writes
C:\OSGeo4W and registers the installation with Windows.

  install_qgis.ps1                         the long-term release, with openpyxl for the tests
  install_qgis.ps1 -Packages qgis          the current release

The options are those of the installer's source (github.com/jef-n/OSGeo4W, src/setup) and
of its maintainers' own unattended call (scripts/build-helpers).
#>
param(
    [string[]]$Packages = @("qgis-ltr", "python3-openpyxl"),
    [string]$Root = "C:\OSGeo4W",
    [string]$Site = "https://download.osgeo.org/osgeo4w/v2/"
)
$ErrorActionPreference = "Stop"
$temp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$setup = Join-Path $temp "osgeo4w-setup.exe"
Invoke-WebRequest -Uri ($Site + "osgeo4w-setup.exe") -OutFile $setup

$arguments = @("--quiet-mode", "--autoaccept", "--arch", "x86_64", "--only-site", "--site", $Site,
               "--root", $Root, "--local-package-dir", (Join-Path $temp "osgeo4w-packages"),
               "--no-shortcuts")
foreach ($package in $Packages) { $arguments += @("--packages", $package) }
$process = Start-Process -FilePath $setup -ArgumentList $arguments -Wait -PassThru
$python = Get-Item (Join-Path $Root "bin\python-qgis*.bat") -ErrorAction SilentlyContinue
if ($process.ExitCode -ne 0 -or -not $python) {
    $log = Join-Path $Root "var\log\setup.log.full"
    if (Test-Path $log) { Get-Content $log -Tail 80 }
    throw "osgeo4w-setup ended with exit code $($process.ExitCode); bin\python-qgis*.bat found: $([bool]$python)"
}
"Installed $($Packages -join ', ') in $Root"
