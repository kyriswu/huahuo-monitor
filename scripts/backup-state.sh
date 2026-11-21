#!/bin/sh
# Consistent offline backup of this stack; never write the result to Git.
set -eu
umask 077
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$(id -u)" = 0 ] || { echo "Run as root (or with sudo)." >&2; exit 1; }
for tool in docker tar sha256sum; do command -v "$tool" >/dev/null; done
dest=${1:-"$repo/state-backups/$(date -u +%Y%m%dT%H%M%SZ)"}
mkdir -p "$(dirname -- "$dest")"
mkdir "$dest"
dest=$(CDPATH= cd -- "$dest" && pwd)
volumes="prometheus_data grafana_data dozzle_data loki_data alloy_data rds_slow_logs_data"
for volume in $volumes; do docker volume inspect "monitoring_$volume" >/dev/null; done
running=$("$repo/monitoring.sh" ps --status running --services)
printf '%s\n' "$running" > "$dest/running-services.txt"
resume() {
    result=$?
    trap - EXIT INT TERM
    if [ -n "$running" ] && { [ "${KEEP_STOPPED:-0}" != 1 ] || [ "$result" != 0 ]; }; then
        # Service names come from this stack's Compose configuration.
        "$repo/monitoring.sh" start $running || result=1
    fi
    exit "$result"
}
trap resume EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [ -n "$running" ]; then "$repo/monitoring.sh" stop --timeout 60 $running; fi
for volume in $volumes; do
    name="monitoring_$volume"
    mountpoint=$(docker volume inspect --format '{{.Mountpoint}}' "$name")
    tar -czpf "$dest/$name.tar.gz" -C "$mountpoint" .
done
set --
for name in .env secrets dozzle/users.yml; do
    if [ -e "$repo/$name" ]; then set -- "$@" "$name"; fi
done
[ "$#" -gt 0 ] || { echo "No private configuration found." >&2; exit 1; }
tar -czpf "$dest/private-config.tar.gz" -C "$repo" "$@"
if [ -d /etc/letsencrypt ]; then
    tar -czpf "$dest/letsencrypt.tar.gz" -C /etc letsencrypt
fi
(
    cd "$dest"
    sha256sum ./*.tar.gz > SHA256SUMS
)
echo "Private migration backup: $dest"
if [ "${KEEP_STOPPED:-0}" = 1 ]; then echo "Monitoring stays stopped for cutover."; fi
