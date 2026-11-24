#!/bin/bash
set -euo pipefail

repo_root=$(pwd -P)
overlay_dir="$repo_root/k8s/overlays/test"
workdir=$(mktemp -d)
trap 'rm -rf -- "$workdir"' EXIT

# Stage the shared sources so the test Loki config gets its own content hash.
mkdir "$workdir/base" "$workdir/overlay"
for source in kustomization.yaml k8s loki grafana rds-slow-logs targets; do
    cp -R "$repo_root/$source" "$workdir/base/"
done
sed -E 's/^(  retention_period: ).*/\148h/' "$repo_root/loki/loki.yml" > "$workdir/base/loki/loki.yml"
kubectl kustomize "$workdir/base" > "$workdir/overlay/monitoring-base.yaml"
cp "$overlay_dir/kustomization.yaml" "$overlay_dir/local-storage.yaml" \
   "$overlay_dir/pvc-bindings.yaml" "$workdir/overlay/"
kubectl kustomize "$workdir/overlay"
