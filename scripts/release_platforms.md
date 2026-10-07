## Артефакты и платформы

| Артефакт | Платформа | Установка / примечание |
|---|---|---|
| `install-linux-macos.sh` | **Linux / macOS** | установщик одной командой; **в Windows НЕ работает** |
| `install-windows.ps1` | **Windows** | PowerShell; CLI, конфигурация, отчёты, дашборд (без сетевых проб); сам ставит Python 3.11+ |
| `install-windows.cmd` | **Windows** | запуск двойным кликом (окно не закроется; вызывает `install-windows.ps1`) |
| `tspu-monitor_X.Y.Z_all.deb` | **Debian / Ubuntu (Linux)** | `sudo apt install ./tspu-monitor_X.Y.Z_all.deb` |
| `tspu-monitor-X.Y.Z.pyz` | **любая ОС** с Python 3.11+ | переносимый; сетевые пробы — Linux/macOS |
| `tspu_monitor-X.Y.Z-py3-none-any.whl` | **любая ОС** с Python 3.11+ | `pip install`; пробы — Linux/macOS |
| `tspu_monitor-X.Y.Z.tar.gz` | **любая ОС** | исходники |
| Docker-образ `ghcr.io/fairen8/tspu-monitor` | **Linux** (amd64/arm64) | `docker run …` |
| `SHA256SUMS` | любая | контрольные суммы |

### Windows: ограничения (важно)

Полноценная диагностика в Windows **не поддерживается**:

* `install-linux-macos.sh`, `deploy/*`, systemd/OpenRC-скрипты в Windows
  **не работают**;
* сетевые пробы требуют Linux-утилит (`ping -M`, `traceroute`, `nmap`,
  `dig`) и `CAP_NET_RAW` — в Windows они не выполняются;
* `install-windows.ps1` / `install-windows.cmd` ставят только CLI, работу
  с конфигурацией, отчёты и веб-дашборд (просмотр данных); окно PowerShell
  остаётся открытым (пауза), для скриптов есть `-NoPause`;
* для реальных проверок используйте Linux: Docker, LXC (Proxmox),
  `.deb` или `install-linux-macos.sh`.

### Выбор способа установки

| ОС | Рекомендуется |
|---|---|
| **Linux** (Debian / Ubuntu) | `install-linux-macos.sh` или `.deb` |
| **Linux** (RHEL / Fedora / Rocky / Alma) | `install-linux-macos.sh` |
| **Linux** (Alpine / Arch / openSUSE) | `install-linux-macos.sh` |
| **macOS** | `install-linux-macos.sh` (Homebrew) |
| **Windows** | `install-windows.cmd` (двойной клик) или `install-windows.ps1` |
| Proxmox LXC | `deploy/lxc/proxmox-create.sh` |
| Любая с Docker | Docker-образ |
