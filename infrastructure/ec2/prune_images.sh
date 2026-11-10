#!/usr/bin/env bash
# Reclaim disk without losing the rollback target (#345, audit 08#6).
#
# Keeps: every image a container uses, and our 8 images at the CURRENT and the
# PREVIOUS deployed tag (state/current_tag, state/previous_tag, written by
# remote-deploy.sh), so a rollback to the previous release needs no pull.
# Removes: older sha- tags of our images, unused infra images left behind by a
# pin bump, dangling layers, and build cache.
#
# Called by remote-deploy.sh after the new stack is up and by the weekly cron
# (/etc/cron.weekly/fxvol-docker-prune). Best-effort: never fails its caller.
set -uo pipefail

STATE="/opt/fxvol/state"
current="$(tr -d '[:space:]' < "$STATE/current_tag" 2>/dev/null || true)"
previous="$(tr -d '[:space:]' < "$STATE/previous_tag" 2>/dev/null || true)"

kept=0
removed=0
while read -r ref; do
  [ -z "$ref" ] && continue
  tag="${ref##*:}"
  case "$ref" in
    *"/fx-options-"*)
      if [ -n "$tag" ] && { [ "$tag" = "$current" ] || [ "$tag" = "$previous" ]; }; then
        kept=$((kept + 1))
        continue
      fi ;;
  esac
  # In-use images refuse removal: that is the "keep what runs" rule.
  if docker rmi "$ref" >/dev/null 2>&1; then removed=$((removed + 1)); fi
done < <(docker images --format '{{.Repository}}:{{.Tag}}' | grep -v ':<none>$')

docker image prune -f >/dev/null 2>&1 || true
docker builder prune -af --filter "until=168h" >/dev/null 2>&1 || true
echo "prune_images: kept ${kept} rollback image(s) (current='${current}' previous='${previous}'), removed ${removed}"
exit 0
