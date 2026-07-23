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

# stats is a plain 'docker' command (not compose). Prepend the EC2 hardware
# line (instance-type via IMDSv2 + total RAM + vCPU) so a RAM/CPU check always
# shows WHAT box the numbers are measured against — t3.small vs t3.medium etc.
cat > "$CMD/all/stats" <<'STATS'
#!/usr/bin/env bash
tok=$(curl -sX PUT http://169.254.169.254/latest/api/token \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 60" --max-time 1 2>/dev/null || true)
itype=$(curl -s -H "X-aws-ec2-metadata-token: $tok" --max-time 1 \
          http://169.254.169.254/latest/meta-data/instance-type 2>/dev/null || true)
mem=$(awk '/MemTotal/{printf "%.1f GiB", $2/1048576}' /proc/meminfo 2>/dev/null || true)
echo "EC2 instance-type: ${itype:-unknown}   RAM: ${mem:-?}   vCPU: $(nproc)"
exec sudo docker stats --no-stream "$@"
STATS
chmod +x "$CMD/all/stats"

# traces = the dev-only tracing pair (tempo + otel-collector), gated behind the
# `traces` compose profile so every stack-wide ./all/* command ignores them.
# This is the ONE command that drives them, on demand, for latency forensics.
cat > "$CMD/all/traces" <<'TRACES'
#!/usr/bin/env bash
cd /opt/fxvol
action="${1:-up}"; [ $# -gt 0 ] && shift
case "$action" in
  up)         exec sudo docker compose --profile traces up -d tempo otel-collector "$@" ;;
  down)       exec sudo docker compose --profile traces rm -sf tempo otel-collector "$@" ;;
  status|ps)  exec sudo docker compose --profile traces ps tempo otel-collector "$@" ;;
  logs)       exec sudo docker compose --profile traces logs -f --tail=100 tempo otel-collector "$@" ;;
  *) echo "usage: ./all/traces [up|down|status|logs]   (dev-only tempo + otel-collector)"; exit 2 ;;
esac
TRACES
chmod +x "$CMD/all/traces"

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
    ./all/stats         instance-type + RAM/CPU     docker stats
    ./all/alembic       DB migration                alembic upgrade
    ./all/traces A      dev-only tempo+otel         compose --profile traces
                        A = up|down|status|logs (default up)

  NOTE: the stack-wide ./all/* commands NEVER touch tempo + otel-collector
  (dev-only tracing, traces profile). Ops/data-check need only metrics+logs;
  spin traces up on demand with "./all/traces up", tear down with "... down".

  CONTAINERS (CREATE/START up = recreate ONE service, no build)
    ./containers/<svc>     e.g. ./containers/api, ./containers/vol-engine
TXT
HELP
chmod +x "$CMD/help"

# remote-deploy.sh runs this as root, and `chmod +x` only ORs the exec bit onto
# whatever the umask left — under a 077 umask that yields 0700 and `cd
# /opt/fxvol/cmd` fails with "Permission denied" for the ssm-user who connects
# via `ec2.ps1 connect`. Force world-readable/traversable explicitly: these are
# thin `sudo docker compose` wrappers, no secrets, and each one still re-asks
# for sudo when it runs.
chmod -R a+rX "$CMD"

echo "gen-commands: wrote $CMD (all: $(ls "$CMD/all" | wc -l), containers: $(ls "$CMD/containers" | wc -l))"
