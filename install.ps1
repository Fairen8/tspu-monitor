<#
.SYNOPSIS
    TSPU Monitor — установщик для Windows.

.DESCRIPTION
    Устанавливает CLI в %LOCALAPPDATA%\TSPU-Monitor: venv, зависимости и
    команду tspu-monitor. Python >= 3.11 ищется в PATH, у py-лаунчера,
    в реестре и стандартных каталогах; если не найден — ставится
    автоматически с python.org (без прав администратора), winget —
    запасной вариант.

    Сетевые пробы ориентированы на Linux; на Windows доступны CLI,
    конфигурация, отчёты и веб-дашборд.

.EXAMPLE
    irm https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.ps1 | iex

.EXAMPLE
    .\install.ps1 -Prefix D:\Tools\TSPU -NoTelemetry

.NOTES
    Переменные окружения (работают и в режиме irm | iex):
      TSPU_PREFIX, TSPU_VERSION, TSPU_REPO, TSPU_SRC,
      TSPU_WITH_WEB=1, TSPU_NO_TELEMETRY=1, TSPU_UNINSTALL=1
#>
[CmdletBinding()]
param(
    [string]$Prefix = $(if ($env:TSPU_PREFIX) { $env:TSPU_PREFIX } else { Join-Path $env:LOCALAPPDATA 'TSPU-Monitor' }),
    [string]$Version = $(if ($env:TSPU_VERSION) { $env:TSPU_VERSION } else { 'main' }),
    [string]$Repo = $(if ($env:TSPU_REPO) { $env:TSPU_REPO } else { 'Fairen8/tspu-monitor' }),
    [switch]$WithWeb,
    [switch]$NoTelemetry,
    [switch]$NoPause,
    [int]$WebPort = 8787,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$WithWeb = $WithWeb -or ($env:TSPU_WITH_WEB -eq '1')
$NoTelemetry = $NoTelemetry -or ($env:TSPU_NO_TELEMETRY -eq '1')
$Uninstall = $Uninstall -or ($env:TSPU_UNINSTALL -eq '1')

$InstallerVersion = '2.2.2'
$script:Step = 0
$LogFile = Join-Path $env:TEMP 'tspu-monitor-install.log'
try { Start-Transcript -Path $LogFile -Force | Out-Null } catch { $LogFile = '' }

function Step($m) { $script:Step++; Write-Host ("[{0}/6] {1}" -f $script:Step, $m) -ForegroundColor Cyan }
function Stop-TspuLog { try { Stop-Transcript | Out-Null } catch { } }

function Info($m) { Write-Host "      $m" }
function Ok($m)   { Write-Host "   ok $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   !! $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "   xx $m" -ForegroundColor Red; throw $m }

function Wait-OnExit {
    # Интерактивная пауза, чтобы окно PowerShell не закрывалось.
    Stop-TspuLog
    if ($NoPause) { return }
    if ($env:CI -or $env:TSPU_NO_PAUSE -or $env:TSPU_PAUSE_OWNED) { return }
    if (-not [Environment]::UserInteractive) { return }
    Write-Host ''
    try { Read-Host 'Нажмите Enter, чтобы закрыть окно' | Out-Null } catch { }
}

function Update-SessionPath {
    try {
        $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $user = [Environment]::GetEnvironmentVariable('Path', 'User')
        $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
    } catch {
        Warn "Не удалось обновить PATH: $($_.Exception.Message)"
    }
}

function Invoke-Native([string]$What, [scriptblock]$Action, [switch]$Capture) {
    # Запуск внешней программы без «NativeCommandError»: в PowerShell 5.1
    # при ErrorActionPreference=Stop любая запись в stderr становится
    # фатальной ошибкой (например, заглушка Python из Microsoft Store).
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = @(& $Action 2>&1)
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        if ($code -ne 0) {
            $output | ForEach-Object { Write-Host "      $_" }
            throw "$What завершился с кодом $code"
        }
        if ($Capture) { return $output }
        $output | ForEach-Object { Write-Host "      $_" }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-PythonVersion([string]$exe, [string[]]$extra) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & $exe @extra '-c' 'import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)' 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-PythonCandidates {
    # Все известные установки Python, свежие версии первыми. PATH не
    # обязателен: py-лаунчер, реестр и стандартные каталоги находятся
    # даже если Explorer ещё не подхватил обновлённый PATH.
    $found = [System.Collections.Generic.List[object]]::new()

    $launchers = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'),
        'C:\Windows\py.exe'
    )
    $command = Get-Command py -ErrorAction SilentlyContinue
    if ($command -and $command.Source) { $launchers += $command.Source }
    foreach ($launcher in ($launchers | Where-Object { $_ } | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $launcher)) { continue }
        $lines = @()
        try { $lines = @(& $launcher -0p 2>&1) } catch { }
        foreach ($line in $lines) {
            $text = [string]$line
            if ($text -match '^\s*(?:-V:)?(\d+)\.(\d+)(?:-\d+)?\s+\*?\s*(.+python\.exe)\s*$') {
                $path = $Matches[3].Trim()
                if (Test-Path -LiteralPath $path) {
                    $found.Add([pscustomobject]@{
                        Version = [version]::new([int]$Matches[1], [int]$Matches[2])
                        Path    = $path
                    })
                }
            }
        }
    }

    foreach ($base in @(
        'HKCU:\SOFTWARE\Python\PythonCore',
        'HKLM:\SOFTWARE\Python\PythonCore',
        'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore'
    )) {
        foreach ($key in (Get-ChildItem -LiteralPath $base -ErrorAction SilentlyContinue)) {
            if ($key.PSChildName -notmatch '^(\d+)\.(\d+)$') { continue }
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            $install = Get-ItemProperty `
                -LiteralPath (Join-Path $key.PSPath 'InstallPath') `
                -ErrorAction SilentlyContinue
            if (-not $install) { continue }
            $path = $null
            if ($install.PSObject.Properties.Name -contains 'ExecutablePath') {
                $path = $install.ExecutablePath
            }
            if (-not $path) {
                $default = $install.PSObject.Properties['(default)']
                if ($default -and $default.Value) {
                    $path = Join-Path ([string]$default.Value) 'python.exe'
                }
            }
            if ($path -and (Test-Path -LiteralPath $path)) {
                $found.Add([pscustomobject]@{
                    Version = [version]::new($major, $minor)
                    Path    = $path
                })
            }
        }
    }

    $globs = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'),
        'C:\Python3*\python.exe',
        'C:\Program Files\Python3*\python.exe',
        'C:\Program Files (x86)\Python3*\python.exe'
    )
    foreach ($pattern in $globs) {
        foreach ($item in (Get-Item -Path $pattern -ErrorAction SilentlyContinue)) {
            $found.Add([pscustomobject]@{ Version = [version]'0.0'; Path = $item.FullName })
        }
    }

    foreach ($name in @('python3.13', 'python3.12', 'python3.11', 'python', 'python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        # Заглушка Microsoft Store: запуск пишет в stderr и открывает Store.
        if ($command -and $command.Source -and $command.Source -notlike '*\WindowsApps\*') {
            $found.Add([pscustomobject]@{ Version = [version]'0.0'; Path = $command.Source })
        }
    }

    return ($found | Sort-Object Version -Descending | Select-Object -ExpandProperty Path -Unique)
}

function Find-Python {
    foreach ($exe in (Get-PythonCandidates)) {
        if (Test-PythonVersion $exe @()) { return $exe }
    }
    return $null
}

function Install-Python {
    Warn 'Python >= 3.11 не найден — установлю автоматически (без прав администратора)'

    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $url = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-$arch.exe"
    $installer = Join-Path $env:TEMP 'tspu-python-3.12.8.exe'
    Info "Скачиваю Python 3.12.8 (~25 МБ): $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        Info 'Тихая установка, 1-3 минуты (прогресс не отображается)...'
        $process = Start-Process -FilePath $installer -PassThru -ArgumentList @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=1',
            'Include_pip=1', 'Include_launcher=1'
        )
        if (-not $process.WaitForExit(900000)) {
            try { $process.Kill() } catch { }
            Warn 'Установщик python.org не завершился за 15 минут'
        } elseif ($process.ExitCode -ne 0) {
            Warn "Установщик python.org вернул код $($process.ExitCode)"
        } else {
            Remove-Item -Force $installer -ErrorAction SilentlyContinue
            Update-SessionPath
            return
        }
    } catch {
        Warn "Не удалось установить Python с python.org: $($_.Exception.Message)"
    } finally {
        Remove-Item -Force $installer -ErrorAction SilentlyContinue
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Info 'Пробую winget (вывод winget идёт ниже, может занять до 10 минут)...'
        try {
            $process = Start-Process -FilePath 'winget' -NoNewWindow -PassThru -ArgumentList @(
                'install', '--id', 'Python.Python.3.12', '--exact', '--silent',
                '--disable-interactivity', '--accept-package-agreements',
                '--accept-source-agreements'
            )
            if (-not $process.WaitForExit(600000)) {
                try { $process.Kill() } catch { }
                Warn 'winget не ответил за 10 минут'
            } elseif ($process.ExitCode -ne 0) {
                Warn "winget завершился с кодом $($process.ExitCode)"
            } else {
                Update-SessionPath
                return
            }
        } catch {
            Warn "winget не сработал: $($_.Exception.Message)"
        }
    }

    Warn 'Установите Python вручную (галочка Add to PATH): https://www.python.org/downloads/windows/'
}

function Uninstall-Tspu {
    Info "Удаление TSPU Monitor из $Prefix"
    if (Test-Path $Prefix) { Remove-Item -Recurse -Force $Prefix }
    Ok "Удалено: $Prefix"
}

function Invoke-Install {
    $script:Prefix = [IO.Path]::GetFullPath($Prefix)
    $Venv = Join-Path $Prefix 'venv'
    $ConfigDir = Join-Path $Prefix 'config'
    $BinDir = Join-Path $Prefix 'bin'
    $DataDir = Join-Path $Prefix 'data'

    Write-Host ("TSPU Monitor Installer {0}" -f $InstallerVersion) -ForegroundColor Cyan
    Step "Проверка окружения"
    Info "Каталог установки: $Prefix"
    Info ("ОС: {0}; PowerShell {1}; версия установщика {2}" -f [Environment]::OSVersion.VersionString, $PSVersionTable.PSVersion, $InstallerVersion)
    if (Test-Path $Prefix) { Info 'Найдена предыдущая установка — обновляю (конфиги сохраняются)' }

    if ($Uninstall) { Uninstall-Tspu; return }

    Step 'Поиск Python (>= 3.11)'
    $pyExe = Find-Python
    if (-not $pyExe) {
        Install-Python
        $pyExe = Find-Python
    }
    if (-not $pyExe) {
        Fail 'Python >= 3.11 недоступен. Установите вручную: https://www.python.org/downloads/windows/ (галочка Add to PATH), затем повторите.'
    }
    $pyVersion = (Invoke-Native 'python --version' { & $pyExe --version } -Capture) -join ' '
    Info ("Python: {0} ({1})" -f $pyVersion, $pyExe)

    Step 'Получение исходников'
    $src = Get-TspuSources
    New-Item -ItemType Directory -Force -Path $Prefix, $BinDir, $DataDir | Out-Null
    if ($src -ne $Prefix) {
        Info "Копирую файлы: $src -> $Prefix"
        Copy-Item -Recurse -Force (Join-Path $src '*') $Prefix
    }

    Step 'Установка приложения'
    if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
        Info "Создаю venv: $Venv"
        Invoke-Native 'Создание venv' { & $pyExe -m venv $Venv }
    }
    $venvPython = Join-Path $Venv 'Scripts\python.exe'
    Info 'Устанавливаю зависимости (pip)'
    Invoke-Native 'pip (обновление)' {
        & $venvPython -m pip install --upgrade --quiet pip wheel setuptools
    }
    Invoke-Native 'pip (установка пакета)' {
        & $venvPython -m pip install --upgrade --quiet --no-cache-dir "${Prefix}[raw]"
    }

    Step 'Настройка конфигурации'
    $settings = Join-Path $ConfigDir 'settings.yaml'
    $secrets = Join-Path $ConfigDir 'secrets.yaml'
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    if (-not (Test-Path $settings)) { Copy-Item (Join-Path $Prefix 'config\settings.yaml') $settings }
    if (-not (Test-Path $secrets))  { Copy-Item (Join-Path $Prefix 'config\secrets.yaml')  $secrets }

    Set-ConfigFlags -ConfigDir $ConfigDir

    $cmd = Join-Path $BinDir 'tspu-monitor.cmd'
    @"
@echo off
set TSPU_CONFIG_DIR=$ConfigDir
"$venvPython" -m tspu_monitor %*
"@ | Set-Content -Encoding ASCII -Path $cmd

    $userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
    if ($userPath -notlike "*$BinDir*") {
        if ($userPath) { $userPath = "$userPath;$BinDir" } else { $userPath = $BinDir }
        [Environment]::SetEnvironmentVariable('PATH', $userPath, 'User')
        Ok "PATH обновлён (перезапустите терминал)"
    }

    Step 'Проверка установки'
    $version = (Invoke-Native 'tspu-monitor version' { & $venvPython -m tspu_monitor version } -Capture) -join ' '
    Ok "Проверено: $version"

    Write-Host ''
    Write-Host "=================================================================" -ForegroundColor Green
    Write-Host (" TSPU Monitor установлен ({0})" -f $version) -ForegroundColor Green
    Write-Host "=================================================================" -ForegroundColor Green
    Write-Host "  CLI:     $cmd"
    Write-Host "  Конфиг:  $ConfigDir\secrets.yaml"
    Write-Host "  Лог:     $LogFile"
    Write-Host ''
    Write-Host '  Что дальше:'
    Write-Host '    1) Проверить: tspu-monitor version'
    Write-Host '    2) Дашборд:   tspu-monitor web --open'
    Write-Host '    3) Удаление:  .\install.ps1 -Uninstall'
    Warn 'Windows: сетевые пробы и shell-скрипты НЕ поддерживаются.'
    Warn 'Для диагностики используйте Linux: Docker, LXC, .deb или install-linux-macos.sh.'
}

function Get-TspuSources {
    if ($env:TSPU_SRC -and (Test-Path (Join-Path $env:TSPU_SRC 'pyproject.toml'))) {
        return $env:TSPU_SRC
    }
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
    Invoke-Native 'Распаковка архива (tar)' { tar -xzf $tar -C $extract --strip-components=1 }
    return $extract
}

function Set-ConfigFlags($ConfigDir) {
    $telemetryEnabled = -not $NoTelemetry
    $flags = @{
        web       = [bool]$WithWeb
        web_port  = $WebPort
        telemetry = $telemetryEnabled
    }
    $scriptFile = Join-Path $env:TEMP ("tspu-cfg-" + [guid]::NewGuid().ToString('N') + '.py')
    @'
import sys
from pathlib import Path

import yaml

config_dir = Path(sys.argv[1])
web_enabled = sys.argv[2] == "1"
web_port = int(sys.argv[3])
telemetry_enabled = sys.argv[4] == "1"

settings_path = config_dir / "settings.yaml"
settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
settings.setdefault("web", {})
settings["web"]["enabled"] = web_enabled
settings["web"]["port"] = web_port
settings.setdefault("telemetry", {})
settings["telemetry"]["enabled"] = telemetry_enabled
settings_path.write_text(
    yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
'@ | Set-Content -Encoding UTF8 -Path $scriptFile

    $venvPython = Join-Path $Prefix 'venv\Scripts\python.exe'
    Invoke-Native 'применение флагов конфигурации' {
        & $venvPython $scriptFile $ConfigDir `
            $(if ($flags.web) { '1' } else { '0' }) `
            $flags.web_port `
            $(if ($flags.telemetry) { '1' } else { '0' })
    }
    Remove-Item -Force $scriptFile -ErrorAction SilentlyContinue

    if ($flags.web) { Ok "Веб-дашборд включён: http://127.0.0.1:$WebPort/" }
    if ($flags.telemetry) {
        Ok 'Анонимная статистика включена (выключить: tspu-monitor telemetry disable)'
    } else {
        Warn 'Анонимная статистика отключена (-NoTelemetry)'
    }
}

$script:IsFileMode = [bool]$PSScriptRoot
if ($env:TSPU_FUNCS_ONLY -eq '1') { Stop-TspuLog; return }
try {
    Invoke-Install
    Wait-OnExit
} catch {
    Warn "Установка прервана: $($_.Exception.Message)"
    Warn 'Если нужна помощь — пришлите этот вывод: https://github.com/Fairen8/tspu-monitor/issues'
    Wait-OnExit
    if ($script:IsFileMode) { exit 1 } else { return }
}
