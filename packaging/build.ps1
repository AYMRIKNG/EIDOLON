# EIDOLON — builds the Windows app and its installer.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1            # exe + installer
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -NoInstaller
#
# Needs: the project's .venv with requirements.txt + pyinstaller installed, and
# Inno Setup 6 for the installer (winget install JRSoftware.InnoSetup).
# Output: dist\EIDOLON\ (the app) and dist\EIDOLON-Setup-<version>.exe

param([switch]$NoInstaller)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Environnement .venv introuvable — lancez d'abord : python setup.py" }

$version = (& $py packaging\make_version_info.py).Trim()
Write-Host "▶ EIDOLON $version — PyInstaller" -ForegroundColor Cyan
& $py -m PyInstaller packaging\eidolon.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller a échoué" }

# Nothing personal may ship: the build must never contain a key or user data.
# (Libraries ship public files such as certifi\cacert.pem — only the app's own
# config / memory folders are checked.)
$leaks = @(
  Get-ChildItem dist\EIDOLON -Recurse -Include api_keys.json, long_term.json, ide_bridge.json -ErrorAction SilentlyContinue
  Get-ChildItem dist\EIDOLON\config, dist\EIDOLON\memory -Recurse -Include *.pem, *.key, *.db -ErrorAction SilentlyContinue
) | Where-Object { $_ }
if ($leaks) { throw "Données personnelles dans le build : $($leaks.FullName -join ', ')" }

if ($NoInstaller) { Write-Host "✓ dist\EIDOLON\EIDOLON.exe"; exit 0 }

$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 introuvable — winget install JRSoftware.InnoSetup" }

Write-Host "▶ Inno Setup" -ForegroundColor Cyan
& $iscc "/DAppVersion=$version" packaging\eidolon.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup a échoué" }
Write-Host "✓ dist\EIDOLON-Setup-$version.exe" -ForegroundColor Green
