# HTTP 错误与 traceId 排查

当前使用 Prometheus 指标排行、Alloy 采集、Loki 日志查询和 Grafana 下钻；Dozzle 保留为直接查看 Docker 原始日志的备用入口。本次没有修改 agent/server 代码、业务 Compose、业务镜像或业务容器。

## 查看顺序

1. 打开 https://grafana.chuda.cc/d/monitoring-overview ，点击“时段 5xx 请求”。
2. 进入“HTTP 错误分析”（/d/http-errors），按所选时段统计 500、501、502、503、504 等状态码。横向堆叠柱按总数降序；悬停可看到各角色、方法、接口的错误次数。
3. 点击柱子按状态码筛选；接口排行按数量降序。点击错误接口或数量进入本错误看板的逐条请求列表，保留环境、主机、角色、状态码、方法、接口及时间范围。慢请求排行进入专用“HTTP 请求日志详情”（/d/http-request-details，uid：`http-request-details`），保留环境、主机、角色、方法、接口、时间范围及慢请求阈值，并显示日志最早可查时间。
4. 两处列表点击 traceId，都在“HTTP 请求日志”的原始日志面板（panel 12）查看真实关联行，不再依赖 Explore。入站日志提供 req.method/route，完成日志提供 res.statusCode/responseTime；只展示实际已记录的 err/stack 等信息，不表示请求体、响应体或完整业务处理过程已记录。

## 数据口径与范围

- 卡片和接口排行使用 Prometheus 的 increase()，是采样估算，显示取整；按所选时间范围计算，不是进程累计。
- 逐条请求使用 Loki。主列表按 instanceId + traceId 关联入站和完成日志，以免同一 traceId 在不同实例间串联成多条错误。只用完成日志计算一次请求，不把独立错误行再算一次。
- 完成日志每次最多查询 500 条，入站日志最多 5000 条。主表支持方法/接口过滤；没有匹配入站日志的响应单独保存在页尾折叠区，可继续按 traceId 查看。
- 列表是已保留日志内的请求明细，不以日志行数代替 Prometheus 错误总数。数量可能因采集起点、旧容器删除、日志保留期、查询上限或请求跨时间边界而不同。
- 当前只采集本机 huahuo-server-dev 的 api-server/admin/worker/scheduler 四个服务。未接入其他生产节点，也不采集 huahuo-agent、Redis 或监控服务自身日志。
- 首次启动读取当前容器 Docker 仍保留的历史日志；已删除容器的旧日志无法恢复。Loki 保留 72 小时，这是保留期，不表示采集启动前已有完整历史；清理由 compactor 执行，非即时精确到秒的磁盘配额。查看详情页的最早可查时间来判断当前日志覆盖范围。
- 当前运行日志已含 traceId。入站、完成日志分行，通过关联查询显示在同一请求行；没有改业务代码来重新输出日志。
- 当前运行版本已包含经过脱敏的错误对象记录。可查看实际已写入日志的 err/stack，但本次没有人为制造业务 500 来测试堆栈。

## 组件职责

业务 /metrics → Prometheus → Grafana：数量、错误率、延迟。
Docker 日志 → Alloy → Loki → Grafana：时间、traceId、逐条请求和错误上下文。
Dozzle → Docker 原始日志：备用实时查看，不向 Prometheus/Grafana 提供数据。
traceId、请求路径、用户信息不作为 Loki 索引标签；它们在查询时从 JSON 提取。

## 资源与维护

Loki grafana/loki:3.7.10、Alloy grafana/alloy:v1.22.0 均按负载使用中心机可用 CPU / 内存，已取消试运行时的固定配额和 Go 内存/并发限制。Loki 的写入和查询调度恢复该固定版本的官方默认值，详见 README 的中心机资源说明。
Loki 数据卷 monitoring_loki_data；Alloy positions 卷 monitoring_alloy_data，持久化读取位置。
Loki 的 3100、Alloy 的 12345 只发布到本机回环。其他节点尚未配置写入入口；后续节点接入时单独配置私网写入链路和访问控制。

使用 /opt/monitoring/monitoring.sh 统一包含基础和日志 Compose 配置，例如：

```sh
/opt/monitoring/monitoring.sh ps
/opt/monitoring/monitoring.sh logs --tail 100 loki alloy
/opt/monitoring/monitoring.sh stop alloy loki
```

“平台运维 / 采集状态”可看两服务的抓取状态、进程内存和日志接收速率。进程 RSS 与 Docker 容器内存统计口径不同；容量判断同时参考 docker stats、磁盘余量和实测日增量。未来生产日志规模未知，本机试运行不等于全量容量承诺。

## 本次验证

真实历史 503 验证了数量排行、接口悬停和状态码点击。当前保留的业务日志没有 5xx 样本；此前使用实际 204 请求在浏览器测试会话中验证了请求关联及当时的 traceId 跳转。未修改线上状态码、业务数据或监控存量来制造错误；测试会话查询替换只用于验证相同格式的成功日志，生产错误面板仍默认筛选 5xx。

2026-11-17 数据核对：现存日志最早为北京时间 11 月 17 日 00:31:07。历史 `/admin/api/v1/document-trials/:trialId/events` 慢请求发生在 11 月 16 日凌晨，早于当前日志覆盖范围，只有指标统计，不能补回逐条明细。

可直接核对的真实慢请求为 `POST /api/v1/auth/sms-code`：11 月 17 日 00:34:41.704（北京时间）完成，200，581.67 ms，traceId `816eeb47-c137-402b-b3c3-ad15d6602e7e`，已有入站和完成两行原始日志。选择覆盖该时刻的范围和 500 毫秒阈值可查；1 秒阈值会正确排除它。该样本只有实际输出的请求方法/路由、状态码/耗时等日志字段，不能据此还原未记录的请求体、响应体或完整业务处理过程。
