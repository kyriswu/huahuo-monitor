# huahuo-monitor

花火AI 中心监控：Prometheus、Grafana、Dozzle、Loki、Alloy 与 RDS 慢 SQL 采集器，包含完整 Compose 配置、9 张业务看板及看板生成源码。使用官方镜像并固定版本；告警引擎开启，监控容器按负载使用可用 CPU / 内存。

**迁移机器请先看 [MIGRATION.md](MIGRATION.md)**：代码由 Git 分发；账号凭据、证书和 Docker 卷通过独立的本地备份迁移。克隆仓库本身不会带回历史指标、日志、用户或告警配置。

**测试 Kubernetes 集群**见 [Kubernetes 部署与 GitHub Actions](k8s/README.md)。每个监控组件各运行一个 Pod，Grafana 提供 Kubernetes 工作负载看板。测试集群发布从 `main` 走 workflow。

下面记录的是原中心机配置，迁移时按迁移说明更新网络、节点地址与 DNS。

中心私网 IP：172.18.102.96；主机 i-2zegtoygiiked53yntmj；环境 test。

## 访问

- Grafana：https://grafana.chuda.cc
- Dozzle：https://dozzle.chuda.cc
- Prometheus：仅本机 http://127.0.0.1:9090
- 登录凭据：secrets/login.txt（仅 root 可访问目录）

电脑可执行：

```sh
ssh -N -o ExitOnForwardFailure=yes -L 3300:127.0.0.1:3300 -L 8888:127.0.0.1:8888 -L 9090:127.0.0.1:9090 root@112.126.81.226
```

Dozzle 可通过 http://localhost:8888，Prometheus 可通过 http://localhost:9090。
Grafana 的正式 URL 使用 HTTPS 且会话 Cookie 标记 Secure，请使用域名访问。
需要通过 SSH 访问 Grafana 的 HTTPS 域名时，可改用：

```sh
ssh -N -o ExitOnForwardFailure=yes -D 1080 root@112.126.81.226
```

将浏览器 SOCKS5 代理设为 localhost:1080 后访问正式域名。

## 镜像和存储

- prom/prometheus:v3.16.0；monitoring_prometheus_data，保留 30 天或 10GB。
- grafana/grafana:13.3.1；monitoring_grafana_data。
- amir20/dozzle:v11.4.3；monitoring_dozzle_data。
- grafana/loki:3.7.10、grafana/alloy:v1.22.0；日志与采集位置均持久化。

Prometheus、Grafana、Dozzle 均配置 restart: unless-stopped，网页端口绑定 127.0.0.1。
Dozzle 使用本机 socket，关闭 Actions、Shell，并启用 simple 登录。
按最新要求使用镜像内置的 Agent TLS 证书，无自定义 Agent 证书或私钥文件。
网站 HTTPS 使用 Certbot 证书，与 Agent TLS 分开。

## 节点接入

当前地址是中心机本身，Dozzle 已从本机 Docker socket 连接它，不重复登记为远端 Agent。
真正的其他节点 Agent 使用相同版本 amir20/dozzle:v11.4.3，运行 agent 子命令；
挂载 Docker socket 并关闭 DOZZLE_ENABLE_ACTIONS、DOZZLE_ENABLE_SHELL。
7007 仅监听节点私网，网络规则仅允许中心私网 IP 172.18.102.96 访问。
中心 .env 中以逗号分隔多个连接：

```dotenv
DOZZLE_REMOTE_AGENT=10.0.0.10:7007|app-01|prod,10.0.0.11:7007|app-02|test
```

修改后执行 `./monitoring.sh up -d dozzle`。
node_exporter 当前不部署，主机硬件指标使用云厂商监控；targets/hosts.yml 保留空模板。

## 业务和 Nginx 指标

业务实例写入 targets/apps.yml，每个实例一个组：

```yaml
- targets: ["10.0.0.10:9464"]
  labels:
    env: prod
    host: app-01
    service: api
    role: api-server
```

默认 HTTP /metrics；HTTPS 或其他路径可在 labels 增加 __scheme__: https、__metrics_path__: /custom/metrics。
已有 Vector 的 Nginx prometheus_exporter 地址单独写入 targets/nginx.yml，保留 env、host、service 标签。
当前未发现本机 Vector 端点，targets/nginx.yml 保持空模板。
当前共有五个业务抓取实例：Server 的 api-server:9464、admin:9464、worker:9464、scheduler:9464，以及 Agent 的 agent:9464（宿主仅回环映射 127.0.0.1:9469）。Prometheus 经共享 Docker 网络直接抓取各进程的独立指标端点，不经过 Alloy，也不使用业务 HTTP 端口的 /metrics。
目标保留 project=huahuo、env=test、host=i-2zegtoygiiked53yntmj，并分别使用 service=huahuo-server / huahuo-agent 及真实 role。业务指标按应用自身 exported_env 筛选，目标 up 按静态 env 筛选；完整清单见 targets/apps.yml。
文件发现每 15 秒刷新，无需重启。每次修改后可执行：

```sh
cd /opt/monitoring
./monitoring.sh exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
```

Grafana Explore 选 Prometheus 数据源，验证：

```promql
up{job="application",env="test"}
http_requests_total{job="application",exported_env="test"}
```

有其他远端节点后，通过 Dozzle 对应主机打开一个实际容器日志验证。

## Dashboard

“花火AI”根目录保留默认首页 [服务总览](https://grafana.chuda.cc/d/monitoring-overview)：两级请求环形图、HTTP 请求量/速率/状态/延迟和慢请求；底部四张摘要卡链接到 Agent 与 Server 专页。

| 目录 | 看板 |
| --- | --- |
| 花火AI / HTTP 排查 | HTTP 接口分析（/d/huahuo-server）、HTTP 错误分析（/d/http-errors）、慢请求分析（/d/slow-requests）、HTTP 请求日志（/d/http-request-details） |
| 花火AI / Agent | Agent 运行与模型（/d/agent-runtime）：原生运行、执行段、首文本、模型推理、原始用量、工具及 Server 依赖 |
| 花火AI / Server | Server 任务与依赖（/d/server-workloads）、数据库慢查询（/d/database-slow-sql） |
| 平台运维 | 采集状态（/d/prometheus-health） |

HTTP 页的“服务”支持 Server / Agent 多选。请求与业务构成用环形图，速率和延迟用折线，结果/状态与排行用横向条形图或表格；任务与模型细节放在各自专页，不重复堆入首页。环境默认 test；未接入的 node_exporter、Nginx 不显示空看板。

配置源为 grafana/build_dashboards.py 及同目录独立模块。执行 `python3 grafana/build_dashboards.py` 后生成 grafana/dashboards 下的分目录 JSON，由 Grafana 文件 provisioning 自动加载；请修改源文件后重新生成。标签与统计边界见 [labels.md](labels.md)。

### Grafana 功能精简与中心机资源（2026-11-18）

当前启用功能精简模式，用作各服务的统一监控入口。监控组件按负载使用整机可用 CPU 和内存，没有固定的容器资源配额。告警引擎、规则执行与内置 Alertmanager 保持开启；现有两套数据源 Prometheus / Loki、自建看板、Explore、HTTP/traceId 日志下钻、RDS 慢 SQL 均保留。当前尚无告警规则；开启引擎本身不会发送业务告警，后续需配置规则和通知渠道。

- 关闭未配置的云监控、SQL、Elasticsearch、InfluxDB、Tempo、Jaeger、Pyroscope 等数据源，以及 Advisor 和各类 Drilldown app；不影响基础 Explore 或自建看板下钻。保留 Alertmanager 数据源插件。
- 关闭 Grafana Live 推流、插件预安装/自动更新和新闻；普通 Prometheus/Loki 查询不依赖 Live。
- 总览、HTTP 接口、Agent、Server 和采集状态默认每分钟刷新；错误、慢请求、HTTP 日志、数据库慢查询默认手动刷新。页面打开、修改筛选与点击刷新时仍查询，也可选 1/5/15 分钟自动刷新。该设置不改变 Prometheus 抓取、日志采集或告警评估频率。
- 逐条 HTTP / 慢 SQL 默认最多最近 200 条；traceId 关联原始日志最多 500 条。查询上限不删除数据、不改变指标排行统计或 72 小时日志保留，可缩小时间范围继续排查。HTTP 明细不再自动扫描全部 72 小时日志计算最早时间。
- Grafana、Loki、Alloy、RDS 慢日志采集器均已移除 Docker CPU / 内存 / 进程数配额；Go 服务也不再手动设置 GOMEMLIMIT、GOGC、GOMAXPROCS。Prometheus 和 Dozzle 原本就没有设置这些资源配额。ECS 升配并重启服务后，组件可使用新的可用资源。
- Loki 取消试运行时的 1 MB/s 写入、单查询并发和 256 KiB 块大小配置，使用固定镜像版本的官方默认值：总写入 4 MB/s、突发 6 MB，单 stream 3 MB/s / 突发 15 MB，querier 并发 4，普通/TSDB 查询拆分并发 32/128。这些是日志流量与查询调度设置，后续可按实际接入量独立调整。指标仍保留 30 天或 10 GB，日志仍保留 72 小时。

模式切换命令会重新生成看板并只重建 Grafana，保留持久化卷：

```sh
/opt/monitoring/grafana-mode.sh standard  # 启用完整插件功能与原刷新默认值，无固定 CPU/内存配额
/opt/monitoring/grafana-mode.sh light     # 启用功能精简模式，告警仍开启，无固定 CPU/内存配额
```

`monitoring.sh` 通过 `grafana/.light-mode` 标记自动合并 `compose.grafana-light.yaml`。模式名称 light 只表示关闭闲置功能，不再表示限制资源。请统一用该脚本管理。以后只需启用某个插件时，从功能精简配置的 `GF_PLUGINS_DISABLE_PLUGINS` 中移除对应 ID，再用 `./monitoring.sh up -d --no-deps grafana` 应用即可；无需整体恢复。

本轮变更前备份：`/opt/monitoring/backups/grafana-memory-20261118T061607Z`。只调整中心监控配置，没有修改 Agent / Server 代码、业务容器或发布流程。

此前固定资源预算模式的实测：变更前 Docker 容器工作集约 606 MiB；打开现有 9 张看板后，3 分钟采样为 330–357 MiB，继续验证 Explore 和告警页面后约 361 MiB。该数据不代表当前取消配额后的内存预算。闲置数据源后台进程仍从 14 个减为 2 个。本次取消监控资源配额前的配置备份在 `backups/monitoring-unrestricted-20261118T084014Z`。

### HTTP 分类与统计口径

遵循两仓统一的观测 contract v1。“请求分类”直接使用应用上报的 traffic：public（外部 API）、internal（内部 API）、admin（管理端）、other（其他 HTTP）。这是物理路由性质，不按调用方 IP 判断来源，也不再靠 URL 前缀猜分类。新图仅统计带规范 traffic 的指标，不混入旧版缺该字段的历史系列；Prometheus 旧指标按原保留策略保存，本轮不清理指标数据。

筛选变量已从 http_scope 改为 traffic，UI 仍叫“请求分类”；旧 URL 中的 var-http_scope 不再参与筛选，刷新后重新选择即可。总体分类环形图对比所选服务、环境、主机和时段的所有类别；点击扇区会选中该类并恢复业务为全部。

“业务分组”仍是按标准 route 模板派生的解释性导航，例如网关鉴权、账号认证、文档解析及 Agent 对话与运行，不是源指标的业务标签。选项随服务、分类与时段生成，新模块自动出现；业务环形图只显示有请求增量的分组，支持继续点击下钻。

多选操作：打开“请求分类”→取消“全部”→勾选“外部 API”“内部 API”等→点击菜单外的空白处应用。“业务分组”操作相同。当前 Grafana 版本在菜单打开时暂存选择，按 Escape 不会提交，需点击空白处。业务选项从所选时段内已上报的路由生成，新增模块会自动出现；业务环形图仅显示有请求增量的分组。

traffic 采用源指标值，业务导航标签仅在查询时派生。`up`、HTTP 当前并发、原生运行、后台任务与 RDS 慢 SQL 不受路由业务筛选影响；各页说明相应变量的作用范围。

时段请求量来自 `increase` 的采样估算；QPS 是结束时刻之前 5 分钟的已完成请求平均速率，QPM = QPS × 60，不是自然分钟的精确次数。HTTP 表示物理请求，包含鉴权子请求，不等同于用户操作次数。新规范的 HTTP 计数和耗时直方图都有 status/outcome/traffic，可按实际状态分析，4xx 不混入 5xx。

HTTP 完成在完整响应 body 结束时计数，SSE 耗时包含整条连接寿命；未完整结束的连接归独立 abort 指标，不能视为 Agent 运行取消。Agent 运行终态、可恢复执行段、模型推理与首文本分别统计；首文本包含此前工具等待，不能拿 HTTP/SSE 耗时代替 TTFT。P50/P95/P99 均为直方图估算，无样本留空。慢请求阈值严格“大于”，新整数桶边界使用 `le="1"`，不是旧格式 `1.0`。

Agent 约从北京时间 2026-11-17 20:37 开始由 Prometheus 采集。此前已有的进程累计值只作为基线，在 Agent 专页折叠区显示，不伪造为窗口新增。原始模型 usage 缺失不填零，不根据不完整 Token 计算费用。

## HTTPS 与维护

Nginx 配置：/etc/nginx/sites-available/monitoring。
证书：/etc/letsencrypt/live/monitoring.chuda.cc/fullchain.pem、privkey.pem。
覆盖 dozzle.chuda.cc、grafana.chuda.cc，到期日 2027-02-13。
自动续期：snap.certbot.renew.timer。
续期重载钩子：/etc/letsencrypt/renewal-hooks/deploy/monitoring-nginx.sh。
已通过 certbot renew --cert-name monitoring.chuda.cc --dry-run --run-deploy-hooks。

```sh
cd /opt/monitoring
./monitoring.sh ps
./monitoring.sh logs --tail 100
```

备份 compose/config/targets、各服务持久化卷和 secrets；避免 docker compose down -v（会删除数据卷）。


## 日志下钻（2026-11-17）

“服务总览”的 5xx 卡片已链接到 /d/http-errors：状态码数量排行 → 悬停看接口贡献 → 点击状态码/接口 → 已保留的逐条请求 → 点击 traceId 查看关联日志。
日志链路为 Docker → Alloy → Loki → Grafana，指标链路仍为业务 /metrics → Prometheus → Grafana。
Alloy 已采集本机四个 Server 角色和 Agent 容器的 JSON 日志，Loki 保留 72 小时。Alloy 负责日志，Prometheus 直接抓指标。Loki、Alloy 按负载使用中心机可用 CPU / 内存，持久化卷继续保留。Dozzle 保留为独立实时容器日志入口。

HTTP 明细只读取规范事件 `http.server.completed`，单条记录包含 method/route/traffic/statusCode/durationMs；日志毫秒、Prometheus 耗时秒。明细和原始关联日志均要求新 schema 的 service_instance_id，不再展示旧格式日志或保留旧版关联折叠区。

业务关联号 `traceId` 与 W3C/OTel 的 `trace_id`、`span_id` 是不同字段。点击 traceId 只查询实际保留的新规范关联日志；当前未部署 Tempo，不把日志关联页面当作完整分布式 trace。已删除日志或日志采集前的请求无法补回；72 小时是保留上限，不代表采集前已有历史。

新增日志配置位于 compose.logs.yaml、loki/loki.yml、alloy/config.alloy。
运维统一使用 ./monitoring.sh，它合并 compose.yaml 和 compose.logs.yaml。
./monitoring.sh stop alloy loki 可只停止日志采集/查询服务；不删除持久化卷。
采集状态页新增日志服务进程内存和接收速率；完整范围、统计口径与限制见 error-investigation.md。
磁盘硬件指标与蓝绿版本标签尚未接入；本次没有改 agent/server 代码或业务发布流程。

## RDS 慢 SQL 日志

已接入测试实例 `pgm-2ze7o54d25m8kn53` 的 `huahuo` 库。“花火AI / Server / 数据库慢查询”入口为 https://grafana.chuda.cc/d/database-slow-sql ，可看采集状态、SQL 耗时排行和逐条脱敏日志。

使用 RDS 已有的 1 秒慢日志阈值，经只读 API 每分钟同步到 Loki，保留 72 小时。默认只显示业务记录；只读验收探针单独显示。没有修改 agent/server 代码、业务容器或数据库配置。尚无 HTTP traceId 与 SQL 的直接关联。

新增 `rds-slow-logs` 采集器：官方 `python:3.13.11-alpine3.22`，无固定 CPU/内存配额，持久化卷 `monitoring_rds_slow_logs_data`，不发布端口。继续使用 `./monitoring.sh` 管理；配置、凭据路径、统计口径与重试机制见 [rds-slow-logs.md](rds-slow-logs.md)。

旧格式 Server 日志已提交 Loki 后台清理：请求 `9639a632`，仅匹配包含旧 `instanceId` 且没有新 `service_instance_id` 的 Server 记录；提交状态 `received` 不代表物理删除已完成。新规范日志继续保留。
