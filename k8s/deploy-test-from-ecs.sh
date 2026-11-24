#!/bin/bash
# Entry point for the fixed Cloud Assistant command on the test deployment ECS.
set -euo pipefail
umask 077

cloud_assistant=false
if [[ ${1:-} == --cloud-assistant ]]; then
    cloud_assistant=true
    shift
fi
if [[ $# != 1 || ! "$1" =~ ^[0-9a-f]{40}$ ]]; then
    if [[ $cloud_assistant == true ]]; then
        printf '%s\n' '{"status":"failed","sha":null}'
    else
        printf '%s\n' 'Usage: bash deploy-test-from-ecs.sh [--cloud-assistant] MAIN_COMMIT_SHA' >&2
    fi
    exit 2
fi

sha=$1
workdir=
exec 3>&1
finish() {
    local result=$?
    trap - EXIT
    if [[ -n $workdir ]]; then
        rm -rf -- "$workdir" || true
    fi
    if (( result == 0 )); then
        printf '{"status":"passed","sha":"%s"}\n' "$sha" >&3
    elif [[ $cloud_assistant == true ]]; then
        printf '{"status":"failed","sha":"%s"}\n' "$sha" >&3
    fi
    exit "$result"
}
trap finish EXIT

if [[ $cloud_assistant == true ]]; then
    # Keep deployment diagnostics out of the Cloud Assistant JSON receipt.
    exec >/dev/null 2>&1
    log_dir=${HUAHUO_MONITOR_DEPLOY_LOG_DIR:-${TMPDIR:-/tmp}}
    log_file=$(mktemp "$log_dir/huahuo-monitor-test.$sha.XXXXXX")
    exec >>"$log_file" 2>&1
fi

: "${HUAHUO_TEST_ACK_CONTEXT:?Set the existing test ACK kubeconfig context}"

kubectl_test=(kubectl --context "$HUAHUO_TEST_ACK_CONTEXT" --request-timeout=30s)
workdir=$(mktemp -d)
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
