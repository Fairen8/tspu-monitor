# Развёртывание в LXC (Proxmox VE)

TSPU Monitor рассчитан на работу в LXC-контейнере Debian 12/13.
Особенность: часть проб использует `scapy` (анализ TTL/IP-ID), для чего
нужны capabilities `CAP_NET_RAW` и `CAP_NET_ADMIN`.

## Требования

| Параметр | Значение |
|---|---|
| Proxmox VE | 7.x / 8.x |
| Шаблон | `debian-12-standard` или `debian-13-standard` |
| Ресурсы | 1 vCPU, 512 МБ RAM, 4 ГБ диска (минимум) |
| Сеть | исходящий интернет; доступ к VPN-серверам |
| Привилегии | `net_raw`, `net_admin` (для raw-проб) |

> Без raw-capabilities всё работает, кроме проб `raw.ttl`
> (`packet.ttl_probe`); они автоматически пропускаются.

## Вариант A: автоматическое создание (рекомендуется)

На **узле Proxmox**:

```bash
# 1. Скачайте шаблон (если ещё не скачан)
pveam update
pveam available | grep debian-12-standard
pveam download local debian-12-standard_12.7-1_amd64.tar.zst

# 2. Создайте контейнер
bash proxmox-create.sh 210 tspu-monitor

# Скрипт сам:
#  - создаст привилегированный LXC (unprivileged=0);
#  - добавит lxc.cap.keep: net_raw net_admin net_bind_service;
#  - включит автозапуск (onboot=1).
```

Параметры можно переопределить переменными окружения:

```bash
STORAGE=local-zfs MEMORY=1024 CORES=2 DISK=8 BRIDGE=vmbr0 \
    bash proxmox-create.sh 210 tspu-monitor
```

## Вариант B: вручную

```bash
pct create 210 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
    --hostname tspu-monitor \
    --cores 2 --memory 1024 --swap 512 \
    --rootfs local-lvm:8 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --features nesting=1,keyctl=1 \
    --unprivileged 0 --onboot 1 --start 1

# Capabilities для raw-проб:
cat >> /etc/pve/lxc/210.conf <<'EOF'
lxc.cap.keep: net_raw net_admin net_bind_service
EOF

pct reboot 210
```

> **Почему privileged (unprivileged=0)?** В непривилегированных
> контейнерах `CAP_NET_RAW` недоступен из-за user namespace. Если
> привилегированный контейнер недопустим, оставьте `unprivileged=1` —
> монитор будет работать без raw-проб.

## Установка внутри контейнера

```bash
pct enter 210

# 1. Доставьте исходники (пример через pct push с узла):
#    pct push 210 tspu-monitor.tar.gz /root/tspu-monitor.tar.gz
tar -xzf /root/tspu-monitor.tar.gz -C /root
cd /root/tspu-monitor

# 2. Установите
bash install.sh

# 3. Заполните цели и Telegram
nano /opt/tspu-monitor/config/secrets.yaml

# 4. Проверьте окружение
tspu-monitor self-test

# 5. Запустите сервис
systemctl enable --now tspu-monitor
systemctl status tspu-monitor
```

## Проверка

```bash
tspu-monitor check                 # разовая диагностика
tspu-monitor status                # состояние и расписание
journalctl -u tspu-monitor -f      # живой журнал демона
```

## Обновление

```bash
cd /root/tspu-monitor
git pull                            # или распакуйте новый архив
bash install.sh                     # идемпотентно: конфиги и данные сохраняются
systemctl restart tspu-monitor
```

## Частые проблемы

### «ping: socket: Operation not permitted»

В непривилегированном LXC ping требует `net.ipv4.ping_group_range`.
Внутри контейнера:

```bash
sysctl -w net.ipv4.ping_group_range="0 2147483647"
# постоянно:
echo 'net.ipv4.ping_group_range = 0 2147483647' >> /etc/sysctl.d/99-tspu.conf
```

Либо используйте привилегированный контейнер (см. выше).

### raw-пробы пропускаются

Проверьте, что в `/etc/pve/lxc/<id>.conf` есть строка
`lxc.cap.keep: net_raw net_admin net_bind_service`, и что контейнер
привилегированный. Затем `pct reboot <id>`.

### Telegram не работает

Telegram Bot API блокируется DPI. Настройте zapret или прокси —
см. [`../zapret/README.md`](../zapret/README.md). Сетевые пробы при этом
остаются прямыми.

### Нет доступа к внутреннему VPN-серверу

Если сервер доступен только через VPN-интерфейс узла, подключите
контейнер в нужный bridge/VLAN (`--net0`) или запускайте монитор на
самом VPN-сервере.
