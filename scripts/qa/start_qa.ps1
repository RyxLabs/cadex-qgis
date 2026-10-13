# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
<#
Starts QGIS on a throwaway profile in %TEMP%\cdxqa, with CADex installed from a release zip
built from this folder and the demo data copied to %TEMP%\cdxqa\work\demo. The default
profile, the plugin folder and the data folders are never written to.

  powershell -ExecutionPolicy Bypass -File scripts\qa\start_qa.ps1            QGIS 4.0
  powershell -ExecutionPolicy Bypass -File scripts\qa\start_qa.ps1 -Qgis 3    QGIS 3.40 LTR
  ... -Scale 1.5  a 150% screen for this QGIS only; the Windows display settings stay as they are
  ... -Harness    also runs harness.py, so scripts\qa\send.py can drive QGIS without clicks
  ... -Front      brings the running QA QGIS window to the front, maximized
  ... -Stop       closes the QA QGIS
  ... -Clean      closes it and deletes %TEMP%\cdxqa with every output written there
#>
param(
    [ValidateSet("3", "4")][string]$Qgis = "4",
    [string]$QgisRoot = "",
    [string]$Demo = "D:\demo\cadex-demo",
    [double]$Scale = 0,
    [switch]$Harness,
    [switch]$Front,
    [switch]$Stop,
    [switch]$Clean
)
$ErrorActionPreference = "Stop"
$plugin = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$root = Join-Path $env:TEMP "cdxqa"
if (-not $QgisRoot) {
    $QgisRoot = @{ "3" = "C:\Program Files\QGIS 3.40.6"; "4" = "C:\Program Files\QGIS 4.0.0" }[$Qgis]
}
$launcher = Join-Path $QgisRoot (@{ "3" = "bin\qgis-ltr.bat"; "4" = "bin\qgis.bat" }[$Qgis])

function Stop-QaQgis {
    # only the QGIS started on the QA profile; any other QGIS window stays open
    Get-CimInstance Win32_Process -Filter "Name like 'qgis%bin.exe'" |
        Where-Object { $_.CommandLine -match 'cdxqa' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "Closed the QA QGIS (pid $($_.ProcessId))" }
}

function Show-QaQgis {
    # a computer-use "open application" starts qgis-bin.exe again, on the default profile, so
    # the QA window is raised by its process instead
    Add-Type -Namespace CdxQa -Name Win -MemberDefinition @"
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
"@
    $qa = Get-CimInstance Win32_Process -Filter "Name like 'qgis%bin.exe'" |
        Where-Object { $_.CommandLine -match 'cdxqa' } | Select-Object -First 1
    if (-not $qa) { throw "The QA QGIS is not running" }
    $window = (Get-Process -Id $qa.ProcessId).MainWindowHandle
    [CdxQa.Win]::ShowWindow($window, 3) | Out-Null
    [CdxQa.Win]::SetForegroundWindow($window) | Out-Null
    "QA QGIS (pid $($qa.ProcessId)) in front"
}

if ($Front) {
    Show-QaQgis
    return
}
if ($Stop -or $Clean) {
    Stop-QaQgis
    if ($Clean -and (Test-Path $root)) {
        Start-Sleep -Seconds 2
        Remove-Item -LiteralPath $root -Recurse -Force
        "Deleted $root"
    }
    return
}
if (-not (Test-Path $launcher)) { throw "QGIS not found at $launcher; pass -QgisRoot" }
Stop-QaQgis

$qaProfile = Join-Path $root "p\profiles\qa"
foreach ($dir in "python\plugins", "QGIS") { New-Item -ItemType Directory -Force (Join-Path $qaProfile $dir) | Out-Null }
foreach ($dir in "ctl\cmd", "ctl\out", "ctl\shots", "work") { New-Item -ItemType Directory -Force (Join-Path $root $dir) | Out-Null }

& (Join-Path $QgisRoot "apps\Python312\python.exe") (Join-Path $plugin "scripts\build_zip.py")
if ($LASTEXITCODE -ne 0) { throw "build_zip.py failed" }
$zip = Get-ChildItem (Join-Path $plugin "dist") -Filter "cadex.*.zip" | Sort-Object LastWriteTime | Select-Object -Last 1
$installed = Join-Path $qaProfile "python\plugins\cadex"
if (Test-Path $installed) { Remove-Item -LiteralPath $installed -Recurse -Force }
Expand-Archive -Path $zip.FullName -DestinationPath (Join-Path $qaProfile "python\plugins") -Force
"Installed $($zip.Name) in the QA profile"

# QGIS 3 reads QGIS3.ini, QGIS 4 QGIS4.ini
$ini = "[PythonPlugins]`r`ncadex=true`r`nprocessing=true`r`n`r`n[qgis]`r`ncheckVersion=false`r`n"
foreach ($name in "QGIS3.ini", "QGIS4.ini") {
    $path = Join-Path $qaProfile "QGIS\$name"
    if (-not (Test-Path $path)) { Set-Content -Path $path -Value $ini -Encoding ascii -NoNewline }
}
$work = Join-Path $root "work\demo"
if ((Test-Path $Demo) -and -not (Test-Path $work)) {
    Copy-Item -Path $Demo -Destination $work -Recurse
    "Copied the demo data to $work"
}

$arguments = @("--profiles-path", "`"$(Join-Path $root 'p')`"", "--profile", "qa", "--noversioncheck", "--nologo")
$log = Join-Path $root "ctl\messages.log"
if ($Harness) {
    "--- start $(Get-Date -Format 'HH:mm:ss') ---" | Out-File $log -Append -Encoding utf8
    $arguments += @("--code", "`"$(Join-Path $PSScriptRoot 'harness.py')`"")
}
if ($Scale -gt 0) { $env:QT_SCALE_FACTOR = "$Scale" }
Start-Process -FilePath $launcher -ArgumentList $arguments -WindowStyle Hidden
Remove-Item Env:QT_SCALE_FACTOR -ErrorAction SilentlyContinue
"Started QGIS $Qgis on the QA profile; it runs as qgis-bin.exe or qgis-ltr-bin.exe from the 8.3 path"
if ($Harness) {
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $text = Get-Content $log -Raw
        if ($text.Substring($text.LastIndexOf("--- start")) -match "harness ready") { "Harness ready"; return }
        Start-Sleep -Milliseconds 500
    }
    throw "No 'harness ready' in $log after 120 s"
}
