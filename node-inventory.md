# 本机监控核对

看板与标签更新时间：2026-11-17。

- 节点和中心私网 IP：172.18.102.96。
- 用户指定主机标签：i-2zegtoygiiked53yntmj；系统 hostname：iZ2zegtoygiiked53yntmjZ。
- 本机即中心；使用现有 Docker socket，无需 Dozzle Agent，不开放 7007。
- 按用户要求不部署 Node Exporter，不添加 hosts.yml 目标。

## Dozzle

镜像 amir20/dozzle:v11.4.3。DOZZLE_HOSTNAME=i-2zegtoygiiked53yntmj，DOZZLE_ENABLE_ACTIONS=false，DOZZLE_ENABLE_SHELL=false，Docker socket 只读挂载。已通过健康检查并确认连接 Docker。

## 业务指标（更新后）

业务发布已提供四个角色的独立指标端点；旧的业务 HTTP 端口 /metrics 返回 404 不再代表未提供指标。
运行时 METRICS_ENABLED=true、METRICS_SERVICE=huahuo-server、METRICS_HOST=0.0.0.0、METRICS_PORT=9464。
已核实实际 APP_ENV=test。原 Prometheus 目标 env=development 是误配；当前抓取清单已纠正为 test，并为每个目标静态设置 role。

Prometheus Compose 持久加入外部网络 huahuo-server-dev_backend，通过 Docker 服务名抓取，避免固定容器 IP。现有 apps 任务已更名为 application，继续使用 /etc/prometheus-targets/apps.yml。
宿主机指标端口保持 127.0.0.1:9464–9467，不发布到私网或公网；Docker 网络内每个业务容器的指标端口均为 9464。
本次未修改业务容器或业务 .env，未重启业务服务，仅重建 Prometheus 以应用网络配置和任务配置。Compose 校验、promtool check config 均通过。

实际 targets/apps.yml：

```yaml
- targets: ["api-server:9464"]
  labels:
    project: "huahuo"
    env: "test"
    host: "i-2zegtoygiiked53yntmj"
    service: "huahuo-server"
    role: "api-server"
- targets: ["admin:9464"]
  labels:
    project: "huahuo"
    env: "test"
    host: "i-2zegtoygiiked53yntmj"
    service: "huahuo-server"
    role: "admin"
- targets: ["worker:9464"]
  labels:
    project: "huahuo"
    env: "test"
    host: "i-2zegtoygiiked53yntmj"
    service: "huahuo-server"
    role: "worker"
- targets: ["scheduler:9464"]
  labels:
    project: "huahuo"
    env: "test"
    host: "i-2zegtoygiiked53yntmj"
    service: "huahuo-server"
    role: "scheduler"
```

验收：

- 四个目标 up{job="application"} 均为 1。
- 四个 application_info 均为 1，role 分别为 api-server、admin、worker、scheduler；当前目标 env=test、service=huahuo-server。
- http_requests_total、http_request_duration_seconds_count 已抓取到 API 和 Admin 时间序列。
- task_processing_attempts_total 已抓取到 Worker 时间序列。
- 四个端点均暴露以上三类指标的 HELP/TYPE；具体角色无事件时可能尚无带业务标签的时间序列。
- Prometheus 自身抓取正常。

历史说明：标签修正前的业务时间序列保留 env=development、exported_env=test；业务图表按 exported_env=test 查询这部分真实 test 历史，未删除历史数据。在线状态按目标静态标签查询，不依赖 application_info。

## 看板入口

- “花火AI / 服务总览”：/d/monitoring-overview，默认首页。
- “花火AI / 接口与任务”：/d/huahuo-server，任务明细默认折叠。
- “平台运维 / 采集状态”：/d/prometheus-health。

重复业务 HTTP 看板已备份并移除。当前没有 Nginx 指标端点或 node_exporter 目标。
请求量按角色用堆叠柱状图，P95 用带数据点折线，状态码请求数用排序横向条形图；实例与接口明细用表格，后台任务详情默认折叠。

## 后续事项

Redis 无已部署 exporter；huahuo-agent 暂无已运行容器。这些不在此次 huahuo-server 四角色指标接入范围内。
业务网络名须保持 huahuo-server-dev_backend；如未来发布改名或跨机器部署，需同步更新 Prometheus 网络或改用受访问控制的私网抓取入口。


## 本机日志试运行（2026-11-17）

四个本机业务角色日志由 Alloy 读取 Docker socket，发往 Loki；范围限定 huahuo-server-dev 项目及 api-server/admin/worker/scheduler，不包括 huahuo-agent、Redis、监控容器，也没有接入其他生产节点。
Loki 与 Alloy 通过 compose.logs.yaml 启动，并配置资源上限、72 小时保留、持久化数据/读取位置卷。统一运维入口 monitoring.sh。
Grafana 现有 Prometheus 之外新增 Loki 数据源。“HTTP 错误分析”提供状态码、接口及 traceId 下钻。当前运行 huahuo-agent 已由业务发布流程部署，但未加入本轮日志采集；本轮没有更改业务代码或容器。
