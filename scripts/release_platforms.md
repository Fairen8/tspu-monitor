## Артефакты и платформы

| Артефакт | Платформа | Установка / примечание |
|---|---|---|
| `tspu-monitor_X.Y.Z_all.deb` | Debian / Ubuntu | `sudo apt install ./tspu-monitor_X.Y.Z_all.deb` |
| `install.sh` | Linux / macOS | установщик одной командой; **в Windows НЕ работает** |
| `install.ps1` | Windows | CLI, конфигурация, отчёты, дашборд (без сетевых проб) |
| `tspu-monitor-X.Y.Z.pyz` | Linux / macOS | переносимый, нужен Python 3.11+ |
| `tspu_monitor-X.Y.Z-py3-none-any.whl` | Linux / macOS | `pip install`; в Windows — только CLI/дашборд |
| `tspu_monitor-X.Y.Z.tar.gz` | Linux / macOS | исходники |
| Docker-образ `ghcr.io/fairen8/tspu-monitor` | Linux (amd64/arm64) | `docker run …` |
| `SHA256SUMS` | любая | контрольные суммы |

### Windows: ограничения (важно)

Полноценная диагностика в Windows **не поддерживается**:

* `install.sh`, `deploy/*`, systemd/OpenRC-скрипты в Windows **не работают**;
* сетевые пробы требуют Linux-утилит (`ping -M`, `traceroute`, `nmap`,
  `dig`) и `CAP_NET_RAW` — в Windows они не выполняются;
* `install.ps1` ставит только CLI, работу с конфигурацией, отчёты и
  веб-дашборд (просмотр данных);
* для реальных проверок используйте Linux: Docker, LXC (Proxmox),
  `.deb` или `install.sh`.

### Выбор способа установки

| ОС | Рекомендуется |
|---|---|
| Debian / Ubuntu | `install.sh` или `.deb` |
| RHEL / Fedora / Rocky / Alma | `install.sh` |
| Alpine / Arch / openSUSE | `install.sh` |
| macOS | `install.sh` (Homebrew) |
| Proxmox LXC | `deploy/lxc/proxmox-create.sh` |
| Любая с Docker | Docker-образ |
| Windows | `install.ps1` (только просмотр/CLI) |
