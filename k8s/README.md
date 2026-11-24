# Kubernetes 测试集群

监控运行在 `ns-monitoring`。6 个 Deployment 各运行 1 个单容器 Pod。Prometheus 发现 `ns-applications` 内花火 Server 和 Agent 的 Pod，直接抓取声明的 9464 端口。Alloy 读取业务 Pod 标准输出和两个命名空间的 Kubernetes Events。

| Pod | 用途 | 默认 PVC |
| --- | --- | --- |
| Prometheus | 抓取指标，保留 30 天或 10 GB | 20 GiB |
| Grafana | 业务和 Kubernetes 看板、告警 | 2 GiB |
| Loki | 存储和查询应用日志、慢 SQL 与 Events，保留 72 小时 | 20 GiB |
| Alloy | 通过 Kubernetes API 采集日志和 Events | 1 GiB |
| kube-state-metrics | 导出两个命名空间的 Pod 和工作负载状态 | — |
| RDS 慢 SQL 采集器 | 查询测试 RDS，向 Loki 推送脱敏慢 SQL | 1 GiB |

状态卷使用集群默认 StorageClass 和 ReadWriteOnce。各 Deployment 以 Recreate 更新，更新期间会短暂中断。Kustomize 复用仓库的 Loki 配置、看板、数据源和采集器源码，生成带内容哈希的 ConfigMap。确认测试节点能拉取清单列出的镜像；如集群已有镜像缓存或镜像仓库，通过 Kustomize 的 `images` 指向既有镜像地址。

## 组件选择

- **工作负载状态**看 Grafana 的 **Kubernetes → Kubernetes 工作负载**。就绪状态、等待原因、容器重启、工作负载缺少副本来自 kube-state-metrics；调度、镜像和探针等事件来自 Alloy。需要一个能浏览 Kubernetes 对象、查看 YAML 或按 RBAC 执行操作的控制台时，可用 Headlamp 桌面版连接现有 kubeconfig。 [kube-state-metrics](https://github.com/kubernetes/kube-state-metrics/tree/v2.19.1) · [Headlamp](https://headlamp.dev/docs/latest/installation/)
- **日志**保留 Loki 和原有的 LogQL、HTTP 下钻、traceId 与慢 SQL 看板。测试规模下，Alloy Deployment 通过 Kubernetes API 读 Pod 日志，日志仍写入单实例 Loki。扩大到全量节点日志或 API 日志读取压力明显时，可将日志采集改为 Alloy DaemonSet 读取 CRI 文件；Events 可继续用单副本采集。Fluent Bit、Vector 和 OpenTelemetry Collector 也适合节点日志采集，已有统一采集器或需要跨后端的 OTLP 管道时可复用。 [Grafana Kubernetes 日志](https://grafana.com/docs/alloy/latest/collect/logs-in-kubernetes/) · [Kubernetes 日志架构](https://kubernetes.io/docs/concepts/cluster-administration/logging/) · [OpenTelemetry Collector](https://opentelemetry.io/docs/platforms/kubernetes/collector/components/)
- **资源用量**可先用集群已有的 Metrics Server 查看 `kubectl top`。需要 Pod 或容器 CPU、内存的历史曲线时，优先接入 ACK 已提供的 kubelet/cAdvisor 指标，再补 Grafana 面板。Node Exporter 适合补齐 Linux 节点磁盘、网卡和内核指标，每个节点运行一个 Pod。ACK 的 API Server、etcd、调度器等控制平面指标取决于集群类型及已有接口。 [Kubernetes 资源监控](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-usage-monitoring/) · [Node Exporter](https://prometheus.io/docs/guides/node-exporter/) · [ACK 监控](https://www.alibabacloud.com/help/en/ack/ack-managed-and-ack-dedicated/user-guide/monitoring-management/)
- **指标发现**当前由 Prometheus 原生 Kubernetes SD 处理。业务团队需要独立声明各自的监控目标和规则时，可接入现有 Prometheus Operator，用 PodMonitor 和 ServiceMonitor 分发配置。 [Prometheus Operator](https://prometheus-operator.dev/docs/developer/getting-started/)

## 首次集群配置

1. 用确认过的 test ACK context 创建监控命名空间。业务部署提供 `ns-applications`。kube-state-metrics v2.19.1 使用 Kubernetes 1.35 客户端；检查目标集群版本与准入策略。
2. 在 `ns-monitoring` 根据信任边界提供 `grafana-admin` Secret 的 `password` 键、`rds-api` Secret 的 `rds-api.json` 键，以及 `rds-slow-logs-settings` ConfigMap。沿用已有的测试账号和只读 RDS API 权限。ConfigMap 提供已核验的 `RDS_INSTANCE_ID`、`RDS_REGION_ID`、`RDS_DATABASE` 和 `RDS_ENV=test`。慢 SQL 看板从所选环境和时段的 Loki 日志读取实例、数据库选项。
3. 通过已有私有入口接入 Grafana，或先用端口转发验收。使用现有 Ingress Controller 时，参考 [ingress.yaml](ingress.yaml) 并设置测试域名、IngressClass 和 TLS Secret；将 [grafana.yaml](grafana.yaml) 的公开 URL 配置为同一域名后再切流。示例 Ingress 与旧环境使用同一域名，启用前确认 DNS 切换安排。

```sh
kubectl --context "$TEST_CONTEXT" apply -f k8s/namespace.yaml
kubectl --context "$TEST_CONTEXT" -n ns-monitoring create secret generic grafana-admin \
  --from-file=password=secrets/grafana_admin_password
kubectl --context "$TEST_CONTEXT" -n ns-monitoring create secret generic rds-api \
  --from-file=rds-api.json=secrets/rds-api.json
kubectl --context "$TEST_CONTEXT" -n ns-monitoring create configmap rds-slow-logs-settings \
  --from-literal=RDS_REGION_ID="$TEST_RDS_REGION_ID" \
  --from-literal=RDS_INSTANCE_ID="$TEST_RDS_INSTANCE_ID" \
  --from-literal=RDS_DATABASE="$TEST_RDS_DATABASE" --from-literal=RDS_ENV=test
```

首次创建 PVC 会得到空卷。需要迁移历史时，先从 [原中心机迁移文档](../MIGRATION.md) 获取一致性备份，将数据恢复到对应 PVC 后再启动工作负载。切换 RDS 慢日志采集时，先停用采集同一目标的旧实例。Grafana 管理员密码 Secret 用于空库初始化；已恢复的 Grafana 数据库沿用原有用户。

## GitHub Actions 发布

[Monitoring test ACK](../.github/workflows/test-ack.yml) 在 `main` 推送时检查 Kubernetes 清单、Prometheus/Loki/Alloy 配置和 RDS 采集器。校验成功且两个发布变量齐备时，工作流使用 GitHub OIDC 临时身份串行发布到 `test` 环境。也可在 Actions 中从 `main` 重新触发。

workflow 使用北京 Node01（`i-2zeaydxol26pp3dxg1ak`）和测试账号共享的 GitHub OIDC Provider（`acs:ram::1472289886746775:oidc-provider/github-actions`）。监控固定命令与 RAM 角色分别绑定本项目的脚本和 GitHub OIDC subject。 [Server test ACK 发布配置](https://github.com/kyriswu/huahuo-server/pull/57/files)

| GitHub 仓库变量 | 用途 |
| --- | --- |
| `TEST_ACK_MONITOR_DEPLOY_COMMAND_ID` | Node01 上监控专用固定云助手命令 ID |
| `TEST_ACK_MONITOR_ROLE_ARN` | 测试发布 RAM 角色 ARN |

在 GitHub 的 `test` 环境允许 `main` 发布，按项目约定保持无人工审批。本仓库的默认 GitHub OIDC subject 为 `repo:kyriswu@5120703/huahuo-monitor@1466054194:environment:test`。RAM 信任策略匹配该 subject，受众为 `sts.aliyuncs.com`，操作范围限定为目标 ECS 和监控固定命令的 `InvokeCommand`、`DescribeInvocationResults`。 [GitHub OIDC subject 格式](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims)

在能访问 test ACK 的 ECS 上，通过已具备 test ACK kubeconfig 的部署用户执行 `bash /path/to/deploy-test-from-ecs.sh --cloud-assistant '{{sha}}'`。固定云助手命令只接收完整提交参数 `sha`，可以复用 Server 发布机的内网连接。部署用户设置 `HUAHUO_TEST_ACK_CONTEXT`，宿主需要 Git、kubectl 和 jq，命令超时可设 600 秒。部署账号需要在监控范围内执行服务端 apply、观察 Deployment、读取必要的前置对象，并有权创建本清单声明的 RBAC 绑定。

云助手只接收与提交 SHA 对应的 JSON 回执。部署日志保存在 ECS 上，可通过 `HUAHUO_MONITOR_DEPLOY_LOG_DIR` 指向已有的私有日志目录，默认写入 `${TMPDIR:-/tmp}`。在已有 test 权限的终端直接运行脚本时，省略 `--cloud-assistant` 即可查看正常诊断输出。

脚本只发布仍为 `main` 最新版本的已验证 SHA，先做服务端 dry-run，再应用监控清单和业务命名空间内的只读 RoleBinding。发现 Server 使用项目既有的默认隔离规则时，单独放行来自监控 Prometheus 的 9464/TCP 指标流量。

## 验收

```sh
kubectl --context "$TEST_CONTEXT" -n ns-monitoring get deploy,pods,svc,pvc
kubectl --context "$TEST_CONTEXT" -n ns-monitoring port-forward svc/prometheus 9090:9090
```

在 Prometheus 检查 `up{job="kube-state-metrics"}` 和 `up{job="application"}`，在 Grafana Kubernetes 看板检查 Pod、工作负载与告警事件。Grafana Explore 可选 Loki 查看 `{job="kubernetes-events"} | logfmt | type="Warning"` 及业务日志。RDS 采集器的 /metrics 独立于外部 API 可用性，通过 `rds_slow_logs_last_success_timestamp_seconds` 检查真实同步。
