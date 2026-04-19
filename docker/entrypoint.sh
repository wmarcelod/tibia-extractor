#!/bin/sh
set -e

cd /app

echo "[entrypoint] TZ=$TZ  $(date)"

if [ ! -f /app/out/items.db ]; then
  echo "[entrypoint] out/items.db nao encontrada, rodando pipeline inicial..."
  python scripts/pipeline.py || echo "[entrypoint] pipeline falhou, iniciando viewer mesmo assim"
else
  echo "[entrypoint] out/items.db ja existe, pulando pipeline inicial"
fi

echo "[entrypoint] iniciando cron (TZ=$TZ)"
cron

echo "[entrypoint] iniciando Flask em 0.0.0.0:5000"
exec python visualizer/app.py --host 0.0.0.0 --port 5000
