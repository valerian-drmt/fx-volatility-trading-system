#!/usr/bin/env bash
# Generate /opt/fxvol/cmd/ with runnable command scripts, so once connected you
# can:  cd /opt/fxvol/cmd ; ls ; ./help ; ./all/ps ; ./containers/api
#
# Mirrors the local PyCharm layout: 'all' = stack-wide, 'containers' = one per
# service. Called by remote-deploy.sh on every deploy (regenerated, no drift).
# NOTE: the VM never builds (images come pre-built from ghcr), so there is no
# BUILD command here -- 'pull' fetches new images, 'up' recreates from them.
set -eu

APP=/opt/fxvol
CMD="$APP/cmd"
rm -rf "$CMD"
mkdir -p "$CMD/all" "$CMD/containers"

# A stack-wide command: cd to the app dir, run 'docker compose <args>', pass
# through any extra CLI args ($@ -> e.g. a service name for logs).
compose_cmd() {  # $1 = filename, $2 = compose subcommand
  printf '#!/usr/bin/env bash\ncd %s && exec sudo docker compose %s "$@"\n' "$APP" "$2" > "$CMD/all/$1"
  chmod +x "$CMD/all/$1"
}
compose_cmd ps      'ps'
compose_cmd up      'up -d --remove-orphans'
compose_cmd down    'down'
compose_cmd start   'start'
compose_cmd stop    'stop'
compose_cmd pull    'pull'
compose_cmd logs    'logs -f --tail=100'
compose_cmd alembic 'exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head'

# stats is a plain 'docker' command (not compose)
printf '#!/usr/bin/env bash\nexec sudo docker stats --no-stream "$@"\n' > "$CMD/all/stats"
chmod +x "$CMD/all/stats"

# Per-container: recreate ONE service from its current image (no build on the VM).
# Profiles come from /opt/fxvol/.env (COMPOSE_PROFILES) which compose auto-reads.
for svc in postgres redis api market-data vol-engine risk-engine db-writer \
           execution-engine frontend nginx ib-gateway \
           prometheus cadvisor loki promtail tempo otel-collector grafana; do
  printf '#!/usr/bin/env bash\ncd %s && exec sudo docker compose up -d --no-deps %s "$@"\n' "$APP" "$svc" > "$CMD/containers/$svc"
  chmod +x "$CMD/containers/$svc"
done

# help: list everything
cat > "$CMD/help" <<'HELP'
#!/usr/bin/env bash
d="$(cd "$(dirname "$0")" && pwd)"
echo
echo "  fxvol server commands   (run from $d)"
echo "  ------------------------------------------------------------"
echo "  ALL (stack-wide) :   ./all/<cmd>"
for f in "$d"/all/*; do printf "      ./all/%-10s\n" "$(basename "$f")"; done
echo
echo "  CONTAINERS (recreate one service) :   ./containers/<svc>"
for f in "$d"/containers/*; do printf "      ./containers/%s\n" "$(basename "$f")"; done
echo
echo "  logs takes a service:   ./all/logs api"
echo
HELP
chmod +x "$CMD/help"

echo "gen-commands: wrote $CMD (all: $(ls "$CMD/all" | wc -l), containers: $(ls "$CMD/containers" | wc -l))"
