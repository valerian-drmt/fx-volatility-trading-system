#!/bin/sh
# IB Gateway nightly-reset watchdog.
#
# The gateway runs an internal IBC auto-restart at 23:59 server time. When that
# (or an IBKR-side event) drops the upstream session — Error 1100, "Connectivity
# between IBKR and Trader Workstation has been lost" — the container's socat API
# port stays up, so market-data / vol-engine keep their socket but every IB
# request times out and their cycle loop hangs. Their Docker healthcheck then
# flips to `unhealthy`, but Docker's `restart: unless-stopped` only reacts to a
# container *exit*, never to `unhealthy` — so nothing self-heals and the desk
# sits data-less until the gateway is restarted by hand.
#
# This runs every 2 min from /etc/cron.d/fxvol-ib-watchdog. When either engine
# is unhealthy it restarts the gateway (forces a clean IBC re-login — a plain
# engine restart is NOT enough, the dead upstream lives in the gateway) then the
# two engines (fresh reconnect via their own backoff). A cooldown file rate-
# limits to one recovery per COOLDOWN_S so a genuinely-down IBKR (weekend, IB
# outage, planned maintenance) is not hammered into a restart loop.
set -eu

COOLDOWN_S=900                              # 15 min between recovery attempts
STATE=/run/fxvol-ib-watchdog.last
GW=fxvol-ib-gateway
ENGINES="fxvol-market-data fxvol-vol-engine"

log() { echo "$(date -u +%FT%TZ) ib-watchdog: $*"; }

health() {
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        "$1" 2>/dev/null || echo missing
}

md=$(health fxvol-market-data)
ve=$(health fxvol-vol-engine)

# Act only on the specific stall signature: an engine reporting unhealthy.
# (`starting` during start_period, `healthy`, `none`, `missing` -> no action.)
case "$md $ve" in
    *unhealthy*) ;;
    *) exit 0 ;;
esac

now=$(date +%s)
if [ -f "$STATE" ]; then
    last=$(cat "$STATE" 2>/dev/null || echo 0)
    if [ $((now - last)) -lt "$COOLDOWN_S" ]; then
        log "unhealthy (md=$md ve=$ve) but within ${COOLDOWN_S}s cooldown; skip"
        exit 0
    fi
fi

log "unhealthy (md=$md ve=$ve) -> restart gateway then engines"
echo "$now" > "$STATE"
docker restart "$GW"
sleep 75                                    # IBC login + market-data farm reconnect
# shellcheck disable=SC2086  # word-splitting ENGINES into two args is intended
docker restart $ENGINES
log "recovery restart issued"
