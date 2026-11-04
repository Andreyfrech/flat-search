#!/usr/bin/env bash
# Настройка сервера для сбора объявлений. Запускать под root на Ubuntu 22.04/24.04.
#
#   bash setup-server.sh check          — проверить, отдаёт ли Циан данные этому серверу
#   bash setup-server.sh install ТОКЕН  — подключить сервер к GitHub как раннер
#
set -euo pipefail
REPO="Andreyfrech/flat-search"
SRC=/opt/flat-search
RUNNER_DIR=/home/runner/actions-runner
MODE="${1:-}"

if [ "$(id -u)" != "0" ]; then echo "Запустите под root"; exit 1; fi

echo "→ Ставлю нужные пакеты…"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl python3 jq tar >/dev/null

if [ -d "$SRC/.git" ]; then git -C "$SRC" pull -q; else git clone -q "https://github.com/$REPO" "$SRC"; fi

case "$MODE" in
  check)
    echo "→ Проверяю доступ к Циану…"
    python3 "$SRC/scraper/update.py" --check
    ;;
  install)
    TOKEN="${2:-}"
    if [ -z "$TOKEN" ]; then echo "Укажите токен: bash setup-server.sh install ТОКЕН"; exit 1; fi
    echo "→ Проверяю доступ к Циану…"
    if ! python3 "$SRC/scraper/update.py" --check; then
      echo "Циан не пускает этот сервер, раннер ставить нет смысла."; exit 1
    fi
    id runner >/dev/null 2>&1 || useradd -m -s /bin/bash runner
    mkdir -p "$RUNNER_DIR"
    cd "$RUNNER_DIR"
    if [ ! -f config.sh ]; then
      VER=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name)
      VER="${VER#v}"
      echo "→ Скачиваю GitHub Actions runner $VER…"
      curl -fsSL -o runner.tgz "https://github.com/actions/runner/releases/download/v$VER/actions-runner-linux-x64-$VER.tar.gz"
      tar xzf runner.tgz && rm runner.tgz
    fi
    ./bin/installdependencies.sh >/dev/null
    chown -R runner:runner "$RUNNER_DIR"
    echo "→ Подключаю к GitHub…"
    su runner -c "cd $RUNNER_DIR && ./config.sh --unattended --replace --url https://github.com/$REPO --token $TOKEN --name cian-vps"
    ./svc.sh install runner >/dev/null
    ./svc.sh start >/dev/null
    echo
    echo "ГОТОВО: сервер подключён к GitHub и будет запускаться сам после перезагрузки."
    ;;
  *)
    sed -n '2,6p' "$0"; exit 1 ;;
esac
