# 当前监控标签与统计约定

## 采集与资源标签

| 标签 | 含义 / 当前值 |
| --- | --- |
| project | 运维项目标识 huahuo |
| env | 环境：test；production 规范化为 prod，development 保持原值 |
| host | 稳定主机名称 i-2zegtoygiiked53yntmj |
| service | huahuo-server / huahuo-agent |
| role | api-server / admin / worker / scheduler / agent |
| instance | 实际抓取地址，如 api-server:9464、agent:9464 |
| job | 业务指标为 application |

targets/apps.yml 配置五个业务实例。Prometheus 直接抓取每个进程的独立 /metrics；Agent 容器监听 9464，宿主只在 127.0.0.1:9469 映射。Alloy 仅采集日志，不是 Prometheus 的指标来源。

目标与应用同时提供 env/service/role，honor_labels=false 会保留应用值为 exported_env/exported_service/exported_role。业务查询使用 exported_env，up 按目标静态 env/role 查询，避免应用指标过期后离线目标消失。service 与 role 不互相替代，不将临时容器 ID 当主机名。

HTTP 看板没有计数样本时，只在所选范围内已有目标且全部抓取成功的情况下补零。5xx 比例要求所选时段内有已完成请求。首页的“抓取失败目标”只统计已发现的目标，所选范围内没有目标时显示“未发现目标”。Kubernetes 的预期副本和实际可用副本在工作负载看板查看。

规范资源标签是 env/service/role；version、service_instance_id 只在 application_info 及日志/OTel resource 中。当前未上报蓝绿 slot、部署 commit 或主机磁盘硬件指标，不能用进程 UUID 或 package 版本推断蓝绿发布状态。

## HTTP contract v1

HTTP 分类直接使用应用上报的 traffic，不再用 route 前缀猜测，也不表示调用者的公网/私网来源。

| 显示分类 | traffic 值 |
| --- | --- |
| 外部 API | public |
| 内部 API | internal |
| 管理端 | admin |
| 其他 HTTP | other |

UI“请求分类”对应变量 traffic；旧变量 http_scope 已停用，旧 URL 的 var-http_scope 被忽略。“服务”可多选 Server / Agent。规范类别集合为 public/internal/admin/other，缺 traffic 的旧历史系列不并入新口径。Prometheus 旧指标仍按原保留策略保存，本轮不删除指标数据。

查询组合为 `service=~"${service:pipe}",traffic=~"${traffic:pipe}",route=~"${business:pipe}"`。business 的值是查询时派生的路由正则，不能用会转义它的默认插值或以逗号拼接的 :raw。多选时打开菜单、取消“全部”、勾选后点击菜单外空白处应用；Escape 不提交选择。

业务分组是解释性导航，不是源指标中的业务标签。它从标准 route 模板派生：例如 gateway 为网关鉴权、auth 为账号认证、document-parser 为文档解析，Agent 对话与运行按固定路由组归类。新模块随已上报路由自动出现，不将当前业务清单写死。business/business_pattern 及图例、下钻辅助标签只在查询中生成，不写回历史序列或 Loki 索引。

总览保留两级环形图：总体类别按服务、环境、主机、时段显示全部分类；业务图按当前分类与业务条件显示有请求增量的分组。点击类别切换并重置业务为全部，点击业务继续筛选。up、当前 HTTP 并发、原生运行、后台任务与 RDS 慢 SQL 不受 HTTP 路由筛选影响。

## 请求、运行与用量

- `http_requests_total` 和 `http_request_duration_seconds` 均含 method/route/status/outcome/traffic；完整 body 结束才计数，SSE 包含连接全程。物理请求包含鉴权子请求，不等于用户操作次数。
- HTTP 中断独立计入 `http_aborted_requests_total` 及中断耗时，reason 区分 client_abort、connection_error、response_incomplete；HTTP reader 断开不代表原生运行取消。
- `agent_runs_total` 只统计原生终态；`agent_run_segments_total` 包含暂停、中断、恢复等执行段。执行段时长不含暂停等待，末次执行段时长不是跨恢复全程墙钟耗时；去重仅在进程内有界保留。
- `agent_time_to_first_text_seconds` 是初始运行到首个非空 text-delta，含此前工具等待；不同于 HTTP/SSE 寿命和单次模型推理耗时。非流式、无文本和恢复段不造首文本样本。
- 模型指标按 MODEL_INFERENCE 统计，工具指标按原生 TOOL_CALL/MCP_TOOL_CALL 统计。原始 input/output Token 缺失不补零；usage complete/partial/unknown 单独呈现，不据此虚构费用。

时段计数来自 increase，是 Prometheus 采样估算；QPS 为结束时刻前 5 分钟的已完成请求均值，QPM 为同一值乘 60，不是自然分钟精确次数。耗时分位数由直方图估算，无样本留空；新规范整数桶边界是 `le="1"`，旧 `1.0` 不再作为新阈值值。日志 durationMs 为毫秒，Prometheus duration 为秒。

Agent 约从北京时间 2026-11-17 20:37 开始被 Prometheus 采集；采集前进程累计计数只显示为基线，不当作窗口新增。尚无事件样本与已观测到零新增分别展示。

## 看板目录

| 目录 | UID / 内容 |
| --- | --- |
| 花火AI 根目录 | monitoring-overview：默认首页，两级 HTTP 环形图、请求/慢请求及四张 Agent/Server 摘要卡 |
| 花火AI / HTTP 排查 | huahuo-server：HTTP 接口分析；http-errors；slow-requests；http-request-details |
| 花火AI / Agent | agent-runtime：运行与模型 |
| 花火AI / Server | server-workloads：任务与依赖；database-slow-sql：数据库慢查询 |
| 平台运维 | prometheus-health：采集状态与监控组件资源 |

请求构成用环形图；速率和延迟用折线；状态与排行用横向条形图或表格。未接入的 Nginx/node_exporter 不占用空看板。JSON 位于 /opt/monitoring/grafana/dashboards 分目录，由文件 provisioning 加载；图表源由 build_dashboards.py 及独立模块维护。

## 日志与关联

链路为业务 Docker → Alloy → Loki → Grafana，已包含 Server 四角色和 Agent；RDS 慢日志另经只读采集器送入 Loki，保留均为 72 小时。Loki 索引只使用稳定的 env/host/service/role/container_name 等字段，traceId、trace_id、runId、请求路径、用户 ID 等只在查询时提取，不作为 Prometheus 或 Loki 索引标签。

HTTP 明细只读取规范完成事件 `http.server.completed`，单条 JSON 已有 method/route/traffic/statusCode/durationMs。明细和原始关联日志均要求新 schema 的 service_instance_id，不展示旧格式日志，不再关联旧入站/完成日志或保留旧版折叠区。出站为 `http.client.completed`，Agent 原生终态为 `agent.run.ended`。

`traceId` 是业务关联号；`trace_id`、`span_id` 是真实 W3C/OTel 标识，不能互换。点击 traceId 查看同一业务号已保留的日志，不按当前 HTTP 业务筛选截断原始上下文。当前未部署 Tempo，不承诺完整 span 时间线；未记录的正文、已经删除的旧容器日志和采集开始前的请求无法补出。RDS 慢 SQL 尚无 HTTP traceId 直接关联。

本轮仅调整监控采集与 Grafana 展示，未修改 Agent/server 业务源码或发布流程。

旧格式 Server 日志已提交 Loki 后台清理：请求 `9639a632`，仅匹配包含旧 `instanceId` 且没有新 `service_instance_id` 的 Server 记录；提交状态 `received` 不代表物理删除已完成。新规范日志继续保留。
