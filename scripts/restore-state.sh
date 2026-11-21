#!/bin/sh
# Restore into a fresh checkout and empty monitoring volumes on the new host.
set -eu
umask 077
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$(id -u)" = 0 ] || { echo "Run as root (or with sudo)." >&2; exit 1; }
[ "$#" = 1 ] || { echo "Usage: $0 /path/to/private-backup" >&2; exit 2; }
for tool in docker tar sha256sum; do command -v "$tool" >/dev/null; done
src=$(CDPATH= cd -- "$1" && pwd)
[ -f "$src/SHA256SUMS" ] || { echo "Missing SHA256SUMS." >&2; exit 1; }
(
    cd "$src"
    sha256sum -c SHA256SUMS
)
[ -z "$(docker ps -q --filter label=com.docker.compose.project=monitoring)" ] || {
    echo "Monitoring is running here; use a fresh destination host." >&2; exit 1;
}
for name in .env secrets dozzle/users.yml; do
    if [ -e "$repo/$name" ]; then
        echo "Refusing to replace existing private config: $name" >&2; exit 1
    fi
done
volumes="prometheus_data grafana_data dozzle_data loki_data alloy_data rds_slow_logs_data"
for volume in $volumes; do
    name="monitoring_$volume"
    [ -f "$src/$name.tar.gz" ] || { echo "Missing archive: $name" >&2; exit 1; }
    if docker volume inspect "$name" >/dev/null 2>&1; then
        mountpoint=$(docker volume inspect --format '{{.Mountpoint}}' "$name")
        [ -z "$(ls -A "$mountpoint")" ] || { echo "Refusing non-empty volume: $name" >&2; exit 1; }
    fi
done
[ -f "$src/private-config.tar.gz" ] || { echo "Missing private-config.tar.gz" >&2; exit 1; }
for volume in $volumes; do
    name="monitoring_$volume"
    docker volume create --label com.docker.compose.project=monitoring \
        --label "com.docker.compose.volume=$volume" "$name" >/dev/null
    mountpoint=$(docker volume inspect --format '{{.Mountpoint}}' "$name")
    tar --same-owner -xzpf "$src/$name.tar.gz" -C "$mountpoint"
done
tar --same-owner -xzpf "$src/private-config.tar.gz" -C "$repo"
echo "Restored monitoring volumes and private configuration."
echo "Finish network, node addresses and HTTPS setup in MIGRATION.md, then run monitoring.sh up -d."
