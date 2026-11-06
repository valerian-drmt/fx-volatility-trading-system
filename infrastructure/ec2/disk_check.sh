#!/bin/sh
# Host disk guard, independent of the obs profile (#342, audit 08#15).
#
# Hourly cron (installed by setup.sh / re-installed by remote-deploy.sh).
# Above WARN_PCT it publishes one message to the SNS topic (instance role,
# sns:Publish) and holds a 6 h cooldown so a full disk does not mail every
# hour; above CRIT_PCT it also prunes dangling images on its own, because a
# full disk is what makes the next deploy fail (prod 2026-07-21).
set -eu

TOPIC="${ALARM_TOPIC_ARN:-arn:aws:sns:eu-west-1:552269855056:fxvol-alarms}"
REGION="${AWS_REGION:-eu-west-1}"
WARN_PCT="${DISK_WARN_PCT:-80}"
CRIT_PCT="${DISK_CRIT_PCT:-90}"
STATE=/run/fxvol-disk-check.last
COOLDOWN_S=21600

pct="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
[ "$pct" -ge "$WARN_PCT" ] || exit 0

if [ "$pct" -ge "$CRIT_PCT" ]; then
  docker image prune -af >/dev/null 2>&1 || true
  pct_after="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
else
  pct_after="$pct"
fi

now="$(date +%s)"
if [ -f "$STATE" ] && [ $((now - $(cat "$STATE" 2>/dev/null || echo 0))) -lt "$COOLDOWN_S" ]; then
  exit 0
fi
echo "$now" > "$STATE"

host="$(hostname)"
body="fxvol host ${host}: root disk at ${pct}% (threshold ${WARN_PCT}%)."
[ "$pct_after" != "$pct" ] && body="${body} Pruned dangling images: now ${pct_after}%."
body="${body}
$(df -hP / | tail -1)
$(docker system df 2>/dev/null | head -5)"
aws sns publish --region "$REGION" --topic-arn "$TOPIC" \
  --subject "[fxvol WARNING] disk ${pct}% on ${host}" --message "$body" >/dev/null \
  || echo "disk_check: sns publish failed (role missing sns:Publish?)" >&2
echo "disk_check: ${pct}% -> alert sent"
