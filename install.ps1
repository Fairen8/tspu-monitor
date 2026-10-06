<#
.SYNOPSIS
    TSPU Monitor — установщик для Windows.

.DESCRIPTION
    Устанавливает CLI в %LOCALAPPDATA%\TSPU-Monitor: venv, зависимости и
    команду tspu-monitor. Сетевые пробы ориентированы на Linux; на Windows
    доступны CLI, отчёты, конфигурация и веб-дашборд.

.EXAMPLE
    irm https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.ps1 | iex

.EXAMPLE
    .\install.ps1 -WithWeb -WebPort 8787
#>
[CmdletBinding()]
param(
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'TSPU-Monitor'),
    [string]$Version = 'main',
    [string]$Repo = 'Fairen8/tspu-monitor',
    [switch]$WithWeb,
    [switch]$WithTelemetry,
    [int]$WebPort = 8787,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

function Info($m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[+] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

$Venv = Join-Path $Prefix 'venv'
$ConfigDir = Join-Path $Prefix 'config'
$BinDir = Join-Path $Prefix 'bin'
$DataDir = Join-Path $Prefix 'data'

function Find-Python {
    $candidates = @(
        'py -3.13', 'py -3.12', 'py -3.11', 'python3.13', 'python3.12',
        'python3.11', 'python', 'python3'
    )
    foreach ($candidate in $candidates) {
        $parts = $candidate.Split(' ')
        $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $extra = @()
        if ($parts.Count -gt 1) { $extra = $parts[1..($parts.Count - 1)] }
        & $parts[0] @extra '-c' 'import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            return ,@($parts[0], $extra)
        }
    }
    return $null
}

function Uninstall-Tspu {
    Info "Удаление TSPU Monitor"
    if (Test-Path $BinDir) {
        $userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
        $clean = ($userPath -split ';' | Where-Object { $_ -and $_ -ne $BinDir }) -join ';'
        [Environment]::SetEnvironmentVariable('PATH', $clean, 'User')
    }
    if (Test-Path $Prefix) { Remove-Item -Recurse -Force $Prefix }
    Ok "Удалено: $Prefix (конфиги удалены вместе с каталогом)"
}

function Get-Sources {
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'pyproject.toml'))) {
        return $PSScriptRoot
    }
    $tmp = Join-Path $env:TEMP ("tspu-src-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $url = "https://github.com/$Repo/archive/$Version.tar.gz"
    $tar = Join-Path $tmp 'src.tar.gz'
    Info "Скачиваю исходники: $url"
    Invoke-WebRequest -Uri $url -OutFile $tar -UseBasicParsing
    $extract = Join-Path $tmp 'src'
    New-Item -ItemType Directory -Path $extract | Out-Null
    tar -xzf $tar -C $extract --strip-components=1
    if ($LASTEXITCODE -ne 0) { Fail 'Не удалось распаковать архив (нужен tar из Windows 10+)' }
    return $extract
}

if ($Uninstall) { Uninstall-Tspu; exit 0 }

Info "Установка TSPU Monitor в $Prefix"
$python = Find-Python
if (-not $python) {
    Fail 'Не найден Python >= 3.11. Установите: winget install Python.Python.3.12'
}
$pyExe = $python[0]
$pyArgs = $python[1]
Info ("Python: {0}" -f (& $pyExe @pyArgs --version))

$src = Get-Sources
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
if ($src -ne $Prefix) {
    Copy-Item -Recurse -Force (Join-Path $src '*') $Prefix
}

if (-not (Test-Path $Venv)) {
    Info "Создаю venv: $Venv"
    & $pyExe @pyArgs -m venv $Venv
}
$venvPython = Join-Path $Venv 'Scripts\python.exe'
Info 'Устанавливаю зависимости'
& $venvPython -m pip install --upgrade --quiet pip wheel setuptools
& $venvPython -m pip install --upgrade --quiet --no-cache-dir "$Prefix[raw]"

New-Item -ItemType Directory -Force -Path $ConfigDir, $BinDir, $DataDir | Out-Null
$settings = Join-Path $ConfigDir 'settings.yaml'
$secrets = Join-Path $ConfigDir 'secrets.yaml'
if (-not (Test-Path $settings)) { Copy-Item (Join-Path $Prefix 'config\settings.yaml') $settings }
if (-not (Test-Path $secrets))  { Copy-Item (Join-Path $Prefix 'config\secrets.yaml')  $secrets }

if ($WithWeb) {
    $webScript = Join-Path $env:TEMP ("tspu-web-" + [guid]::NewGuid().ToString('N') + '.py')
    @'
import sys
from pathlib import Path

import yaml

config_dir, port = Path(sys.argv[1]), int(sys.argv[2])
settings_path = config_dir / "settings.yaml"
settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
settings.setdefault("web", {})
settings["web"].update({"enabled": True, "host": "127.0.0.1", "port": port})
settings_path.write_text(
    yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
'@ | Set-Content -Encoding UTF8 -Path $webScript
    & $venvPython $webScript $ConfigDir $WebPort
    Remove-Item -Force $webScript
}

if ($WithTelemetry) {
    $telScript = Join-Path $env:TEMP ("tspu-tel-" + [guid]::NewGuid().ToString('N') + '.py')
    @'
import sys
from pathlib import Path

import yaml

settings_path = Path(sys.argv[1]) / "settings.yaml"
settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
settings.setdefault("telemetry", {})
settings["telemetry"]["enabled"] = True
settings_path.write_text(
    yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
'@ | Set-Content -Encoding UTF8 -Path $telScript
    & $venvPython $telScript $ConfigDir
    Remove-Item -Force $telScript
    Ok 'Анонимная статистика включена (выключить: tspu-monitor telemetry disable)'
}

$cmd = Join-Path $BinDir 'tspu-monitor.cmd'
@"
@echo off
set TSPU_CONFIG_DIR=$ConfigDir
"$venvPython" -m tspu_monitor %*
"@ | Set-Content -Encoding ASCII -Path $cmd

$userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable('PATH', "$userPath;$BinDir", 'User')
    Ok "PATH обновлён (перезапустите терминал)"
}

Write-Host ''
Ok 'TSPU Monitor установлен'
Write-Host "  CLI:    $cmd"
Write-Host "  Конфиг: $ConfigDir\secrets.yaml"
Write-Host '  Проверка: tspu-monitor version ; tspu-monitor web --open'
Warn 'Сетевые пробы полностью работают на Linux; на Windows доступны CLI, дашборд и отчёты.'
