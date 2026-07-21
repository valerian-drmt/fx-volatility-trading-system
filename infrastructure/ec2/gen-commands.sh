#!/usr/bin/env bash
# Generate /opt/fxvol/cmd/ with runnable command scripts, so once connected you
# can:  cd /opt/fxvol/cmd ; ls ; ./help ; ./all/up ; ./containers/api
#
# Same lifecycle logic as the local PyCharm configs, with ONE difference: the
# VM never BUILDS (images are pulled pre-built from ghcr), so the BUILD phase is
# replaced by PULL.
#
#   local phase      VM command        docker compose
#   -----------      ----------        --------------
#   PULL up          ./all/pull        pull                 (get new images)
#   PULL down        ./all/rmi         down --rmi all       (remove images)
#   CREATE up        ./all/create      create               (make containers)
#   CREATE down      ./all/down        down                 (remove containers)
#   START up         ./all/start       start                (run containers)
#   START down       ./all/stop        stop                 (stop containers)
#   CREATE/START up  ./all/up          up -d                (recreate) <- daily
#   PULL/CREATE/START up  ./all/full-up   pull + up -d      (full)
#
# Called by remote-deploy.sh every deploy (regenerated, no drift).
set -eu

APP=/opt/fxvol
CMD="$APP/cmd"
rm -rf "$CMD"
mkdir -p "$CMD/all" "$CMD/containers"

# A stack-wide command: cd to the app dir, run 'docker compose <args>', pass
# through extra CLI args ($@ -> e.g. a service name for logs).
compose_cmd() {  # $1 = filename, $2 = compose subcommand
  printf '#!/usr/bin/env bash\ncd %s && exec sudo docker compose %s "$@"\n' "$APP" "$2" > "$CMD/all/$1"
  chmod +x "$CMD/all/$1"
}
compose_cmd status  'ps'                       # status + health
compose_cmd up      'up -d --remove-orphans'   # CREATE/START up (recreate) - daily
compose_cmd create  'create'                   # CREATE up (make, not started)
compose_cmd start   'start'                    # START up
compose_cmd stop    'stop'                      # START down
compose_cmd down    'down'                      # CREATE down (remove containers)
compose_cmd pull    'pull'                      # PULL up (get new images)
compose_cmd rmi     'down --rmi all'            # PULL down (remove images)
compose_cmd logs    'logs -f --tail=100'        # logs (takes a service name)
compose_cmd alembic 'exec -T api python -m alembic -c src/persistence/alembic.ini upgrade head'

# full-up = PULL/CREATE/START up : get the new images, then recreate.
printf '#!/usr/bin/env bash\ncd %s && sudo docker compose pull && exec sudo docker compose up -d --remove-orphans "$@"\n' "$APP" > "$CMD/all/full-up"
chmod +x "$CMD/all/full-up"

# stats is a plain 'docker' command (not compose)
printf '#!/usr/bin/env bash\nexec sudo docker stats --no-stream "$@"\n' > "$CMD/all/stats"
chmod +x "$CMD/all/stats"

# Per-container: CREATE/START up ONE service (recreate from its current image;
# no build on the VM). Profiles come from /opt/fxvol/.env which compose reads.
for svc in postgres redis api market-data vol-engine risk-engine db-writer \
           execution-engine frontend nginx ib-gateway \
           prometheus cadvisor loki promtail tempo otel-collector grafana; do
  printf '#!/usr/bin/env bash\ncd %s && exec sudo docker compose up -d --no-deps %s "$@"\n' "$APP" "$svc" > "$CMD/containers/$svc"
  chmod +x "$CMD/containers/$svc"
done

# help: list everything with the phase mapping
cat > "$CMD/help" <<'HELP'
#!/usr/bin/env bash
d="$(cd "$(dirname "$0")" && pwd)"
cat <<TXT

  fxvol server commands   (run from $d)
  ---------------------------------------------------------------
  ALL (stack-wide)      phase                       command
    ./all/pull          PULL up   (get images)      compose pull
    ./all/rmi           PULL down (remove images)   compose down --rmi all
    ./all/create        CREATE up (make containers) compose create
    ./all/down          CREATE down (remove them)   compose down
    ./all/start         START up  (run them)        compose start
    ./all/stop          START down (stop them)      compose stop
    ./all/up            CREATE/START up (recreate)  compose up -d      <- daily
    ./all/full-up       PULL/CREATE/START up (full) pull + up -d
    ./all/status        status + health             compose ps
    ./all/logs SVC      tail one service            compose logs -f
    ./all/stats         per-container RAM/CPU        docker stats
    ./all/alembic       DB migration                alembic upgrade

  CONTAINERS (CREATE/START up = recreate ONE service, no build)
    ./containers/<svc>     e.g. ./containers/api, ./containers/vol-engine
TXT
HELP
chmod +x "$CMD/help"

echo "gen-commands: wrote $CMD (all: $(ls "$CMD/all" | wc -l), containers: $(ls "$CMD/containers" | wc -l))"
