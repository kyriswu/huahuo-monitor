#!/bin/bash
# Entry point for the fixed Cloud Assistant command on the test deployment ECS.
set -euo pipefail
umask 077

sha=${1:?Expected the full main commit SHA}
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid commit SHA" >&2; exit 1; }
: "${HUAHUO_TEST_ACK_CONTEXT:?Set the existing test ACK kubeconfig context}"

kubectl_test=(kubectl --context "$HUAHUO_TEST_ACK_CONTEXT" --request-timeout=30s)
workdir=$(mktemp -d)
trap 'rm -rf -- "$workdir"' EXIT
git -C "$workdir" init -q
git -C "$workdir" fetch -q --depth=1 https://github.com/kyriswu/huahuo-monitor.git refs/heads/main
[[ "$(git -C "$workdir" rev-parse FETCH_HEAD)" == "$sha" ]] || { echo "main moved; deploy the latest workflow run" >&2; exit 1; }
git -C "$workdir" checkout -q --detach "$sha"

cd "$workdir"
"${kubectl_test[@]}" get namespace ns-monitoring ns-applications -o name >&2
"${kubectl_test[@]}" -n ns-monitoring get secret grafana-admin rds-api -o name >&2
"${kubectl_test[@]}" -n ns-monitoring get configmap rds-slow-logs-settings -o name >&2

# Server's existing ingress policy already isolates these four roles.
kubectl kustomize . > rendered.yaml
files=(-f rendered.yaml -f k8s/application-access.yaml)
policy=$("${kubectl_test[@]}" -n ns-applications get networkpolicy huahuo-server-ingress --ignore-not-found -o json)
if [[ -n "$policy" ]]; then
    if jq -e '.spec.podSelector == {
        matchLabels: {"app.kubernetes.io/name": "huahuo-server"},
        matchExpressions: [
            {key: "app.kubernetes.io/component", operator: "In", values: ["api", "worker", "scheduler", "admin"]}
        ]
    } and ((.spec.policyTypes | index("Ingress")) != null)' <<< "$policy" >/dev/null; then
        files+=(-f k8s/server-metrics-policy.yaml)
    else
        echo "Server ingress selector changed; review the optional cross-namespace metrics policy" >&2
    fi
fi

"${kubectl_test[@]}" apply --server-side --dry-run=server --field-manager=huahuo-monitor "${files[@]}" >&2
"${kubectl_test[@]}" apply --server-side --field-manager=huahuo-monitor "${files[@]}" >&2
kubectl --context "$HUAHUO_TEST_ACK_CONTEXT" -n ns-monitoring rollout status --timeout=6m \
    deployment/prometheus deployment/grafana deployment/loki deployment/alloy \
    deployment/kube-state-metrics deployment/rds-slow-logs >&2
printf '{"status":"passed","sha":"%s"}\n' "$sha"
