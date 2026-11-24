#!/bin/bash
set -euo pipefail

sha=${GITHUB_SHA:?}
instance=${TEST_ACK_MONITOR_ECS_INSTANCE_ID:?Set the test deployment ECS instance ID}
command=${TEST_ACK_MONITOR_DEPLOY_COMMAND_ID:?Set the fixed test monitoring deploy command ID}
region=${TEST_ACK_MONITOR_REGION_ID:-cn-beijing}
[[ "$sha" =~ ^[0-9a-f]{40}$ && "$instance" =~ ^i-[a-zA-Z0-9]+$ &&
   "$command" =~ ^c-[a-zA-Z0-9]+$ && "$region" =~ ^[a-z]+(-[a-z0-9]+)+$ ]] ||
    { echo "::error::Invalid test deployment configuration"; exit 1; }
: "${ALIBABA_CLOUD_SECURITY_TOKEN:?The test job requires temporary Alibaba Cloud credentials}"

parameters=$(jq -nc --arg sha "$sha" '{sha: $sha}')
client_token="monitor-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
invoke_id=$(aliyun ecs InvokeCommand --region "$region" --RegionId "$region" \
    --InstanceId.1 "$instance" --CommandId "$command" --Parameters "$parameters" \
    --ClientToken "$client_token" --RepeatMode Once | jq -er '.InvokeId')
[[ "$invoke_id" =~ ^t-[a-zA-Z0-9]+$ ]] || { echo "::error::Invalid Cloud Assistant invocation ID"; exit 1; }
echo "Cloud Assistant invocation: $invoke_id"

deadline=$((SECONDS + 720))
while (( SECONDS < deadline )); do
    result=$(aliyun ecs DescribeInvocationResults --region "$region" --RegionId "$region" \
        --CommandId "$command" --InstanceId "$instance" --InvokeId "$invoke_id" \
        --ContentEncoding PlainText --MaxResults 1)
    item=$(jq -ce '
        (.Invocation.InvocationResults.InvocationResult // []) as $rows
        | if ($rows | length) > 1 then error("unexpected invocation result")
          else $rows[0] // {} end
    ' <<< "$result")
    if [[ "$item" != '{}' ]]; then
        jq -e --arg i "$instance" --arg c "$command" --arg t "$invoke_id" \
            '.InstanceId == $i and .CommandId == $c and .InvokeId == $t' <<< "$item" >/dev/null
        case "$(jq -er '.InvocationStatus' <<< "$item")" in
            Pending|Running|Stopping) ;;
            Success)
                jq -e --arg sha "$sha" '
                    .ExitCode == 0 and ((.Dropped // 0) == 0)
                    and ((.Output | fromjson) == {status:"passed", sha:$sha})
                ' <<< "$item" >/dev/null
                printf 'Test ACK deployed commit `%s`.\n' "$sha" >> "${GITHUB_STEP_SUMMARY:?}"
                exit 0
                ;;
            *)
                echo "::error::Test ACK deployment failed; check the Cloud Assistant execution $invoke_id"
                exit 1
                ;;
        esac
    fi
    sleep 5
done
echo "::error::Timed out waiting for Cloud Assistant execution $invoke_id"
exit 1
