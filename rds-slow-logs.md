# RDS 数据库慢日志

入口为 [数据库慢查询](https://grafana.chuda.cc/d/database-slow-sql)，也可从「Server 任务与依赖」打开。

## 接入范围与查看方式

- 采集目标由 `RDS_REGION_ID`、`RDS_INSTANCE_ID`、`RDS_DATABASE` 和 `RDS_ENV` 配置。测试部署使用已核验的测试实例。
- 数据库已有 `log_min_duration_statement=1000`，记录耗时达到 1 秒的 SQL。本次没有修改该参数、安装数据库扩展或修改 agent/server 代码与业务发布配置。
- 链路：RDS `DescribeSlowLogRecords` → 专用采集器 → Loki → Grafana。每 60 秒同步，RDS 自身也有分钟级延迟。
- 看板中的 RDS 实例和数据库选项来自所选环境、时段内的 Loki 日志。数据库列表会随实例选择更新。
- 默认“数据范围”为“业务慢SQL”；没有符合条件的记录时计数为 0，排行和明细为空。“验证探针”独立显示验收用只读查询，不计入业务排行。
- 采集状态按所选时间范围的结束时刻查询。早于接入时间或该环境无状态样本时显示“所选时刻无状态”；查看当前同步状态请选“最近 6 小时”并以 `now` 结束，环境选 `test`。
- 排行按每条 RDS 日志的耗时合计降序，展示前 30 个 SQL 的摘要和记录总耗时；顶部另有记录数、SQL 摘要数与最长记录耗时。点击 SQL 标识、摘要或总耗时，保留时间、环境、实例、数据库与数据范围，进入该 SQL 的明细。
- 明细展示所选时间内最近 500 条已采集记录，并在这些记录内按耗时降序；不是整个范围最慢的 500 条。RDS 的一条记录可能聚合多次执行，`execution_count` 保留 API 的 `QueryTimes`，不能把日志条数直接当成精确执行次数。
- Loki 保留 72 小时。发生时间使用 RDS 提供的 UTC 执行开始时间，Grafana 按浏览器时区显示。只有慢日志数据，未接入 HTTP traceId 与 SQL 的关联。

## 脱敏与持久化

SQL 的字符串、数值、dollar-quoted 字面值与注释在采集器内先脱敏，再写 SQLite 或 Loki；保留 SQL 结构和 `$1` 等绑定占位符。SQL 文本最多约 8 KiB。当前数据库 `standard_conforming_strings=on`；普通字符串、双引号标识符与 `E'...'` 的转义规则分别处理。

Loki 索引只使用服务、环境、RDS 实例、数据库、来源和 UTC 日志小时，SQL 文本及 SQLHash 不作为索引标签。按小时分 stream 是为了接收云端迟到/历史记录，避免改动 Loki 全局的乱序时间限制；每库在保留窗口中只有约 72 个小时值。

采集状态保存在 `monitoring_rds_slow_logs_data` 卷中的 SQLite。重叠窗口重复拉取不会重复统计；同秒同 SQL 的多条真实记录按出现序号保留。Loki 写入失败时保留待发送记录，以稳定时间戳和事件内容重传。

首次与每小时回溯 24 小时，其余轮询重扫最近 2 小时以接收迟到记录。停机超过 24 小时后，从最后成功同步时间减 2 小时追补，最多追补 71 小时；超出保留期的数据不再入库。分页/网络错误不会推进成功检查点。

## 运维

采集器：`compose.logs.yaml` 中的 `rds-slow-logs`，官方镜像 `python:3.13.11-alpine3.22`，无第三方 Python 依赖。按负载使用中心机可用 CPU / 内存，无固定配额，无宿主机端口、无 Docker socket、只读根文件系统。内部 `9129/metrics` 与 `/health` 由 Prometheus 与容器健康检查使用。

凭据：`/opt/monitoring/secrets/rds-api.json`（0600），从当前已配置 RAM 凭据复制为独立文件，只读挂载到采集器。采集器只调用 RDS 的只读 `DescribeSlowLogRecords` API；没有修改 RAM 策略或开启付费 SQL 洞察服务。轮换凭据时更新这个文件；每次请求重新读取。单独授权时所需权限为对应实例的 `rds:DescribeSlowLogRecords`。

```sh
/opt/monitoring/monitoring.sh ps rds-slow-logs
/opt/monitoring/monitoring.sh logs --tail 30 rds-slow-logs
/opt/monitoring/monitoring.sh restart rds-slow-logs
```

源代码：`rds-slow-logs/collector.py`；离线测试：`python3 -m unittest discover -s /opt/monitoring/rds-slow-logs -v`。
看板源：`grafana/database_dashboard.py`，由 `grafana/build_dashboards.py` 调用，`.rds-slow-logs-enabled` 控制启用。

## 验证

使用独立只读连接执行一次带 `monitoring_slow_log_probe` 标记的 `SELECT pg_sleep(1.2)`，没有读取或写入业务表。RDS 返回 `QueryTimeMS=1202`；Loki 保存为 `SELECT pg_sleep(?)`，`probe=true`。默认业务范围排除此记录。

官方 API：https://help.aliyun.com/zh/rds/developer-reference/api-rds-2014-08-15-describeslowlogrecords 。
