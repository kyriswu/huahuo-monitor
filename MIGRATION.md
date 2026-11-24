# 迁移中心监控

本仓库包含六个监控服务的配置、看板 JSON 与生成源码、RDS 慢 SQL 采集器、Nginx 和 Certbot 更新钩子。Compose 项目名固定为 `monitoring`，卷名固定为 `monitoring_*`；请沿用这个项目名。

Git 不保存 `.env`、Grafana 密码、Dozzle 用户哈希、阿里云 AccessKey、证书私钥、运行日志或 Docker 卷。**仅克隆仓库不会恢复已有账号、历史数据或 Grafana 在界面里保存的设置和告警。** 这些由下面的独立备份迁移。

## 1. 在原机器导出状态

需要 root、Docker Compose v2、GNU tar 和 sha256sum。脚本会短暂停止本套监控容器，复制六个持久化卷及私有配置，完成后自动恢复原本运行的服务；业务 Agent/Server 不会被停止。历史指标、Grafana SQLite 数据库/用户/偏好/告警、Loki 数据与 WAL、Alloy 读取位置、Dozzle 数据和慢 SQL 去重状态均随卷保存。

```sh
cd /opt/monitoring
./scripts/backup-state.sh /root/huahuo-monitor-state
```

正式切换窗口可以用下列命令在导出后保持旧监控停止，避免新旧中心同时采集；备份失败则脚本会尝试恢复旧服务。目标目录必须尚不存在。

```sh
KEEP_STOPPED=1 ./scripts/backup-state.sh /root/huahuo-monitor-cutover
```

输出目录权限仅 root 可读，含六个卷归档、`private-config.tar.gz`、可选的 `letsencrypt.tar.gz` 和校验清单。该目录包含真实账号和私钥，只通过 SSH/SCP 等私有链路传输，不能提交到 Git。迁移使用最后一次切换备份；不要使用仍在写入时直接打包出来的 Grafana/Prometheus/Loki 数据目录。

## 2. 准备新机器

安装 Docker Engine、Docker Compose v2、Git、Python 3、Nginx、Certbot、GNU tar 和 sha256sum。原机器使用 x86_64；迁移到其他 CPU 架构时，Grafana 卷内缓存的插件二进制需要重新安装，建议沿用 x86_64。

```sh
git clone git@github.com:kyriswu/huahuo-monitor.git /opt/monitoring
# 在原机器执行；将 NEW_HOST 替换为新机器的 SSH 地址。
scp -r /root/huahuo-monitor-cutover root@NEW_HOST:/root/
```

在新机器校验并恢复到全新目录与空卷：

```sh
cd /opt/monitoring
./scripts/restore-state.sh /root/huahuo-monitor-cutover
```

脚本不会覆盖已有的私有配置或非空卷，也不会自动启动服务；HTTPS 证书在下一步单独处理。恢复原 Grafana 卷后账号密码保持原样，`GF_SECURITY_ADMIN_PASSWORD` 只影响全新数据库的初次初始化。

## 3. 调整节点、网络和采集范围

- `.env.example` 列出可配置项；迁移已经恢复 `.env`，按需添加/修改域名、`DOZZLE_HOSTNAME`、`APPLICATION_NETWORK`、RDS 配置与 `DOZZLE_REMOTE_AGENT`。当前仅中心本机 Docker socket，没有真正远端 Dozzle Agent。
- `targets/apps.yml` 目前使用 `api-server:9464`、`admin:9464` 等同机 Docker DNS。业务也迁到新机器时，先部署业务并让 Prometheus 加入相同的外部 Docker 网络；默认网络为 `huahuo-server-dev_backend`。
- 业务仍留在原机器或在其他节点时，将抓取目标改为实际的 **节点私网地址和可达的指标端口**，保留 `env`、`host`、`service`、`role`。原业务端口映射只监听 `127.0.0.1`，新中心无法直接访问，需在业务节点提供受网络规则保护的私网指标入口。不要将每个角色都误指向同一个宿主机端口。
- 若新中心不与业务共用 Docker 网络，可在 `.env` 设置 `APPLICATION_NETWORK=monitoring-applications`，执行 `docker network create monitoring-applications`，并按上一条将目标改为私网地址。业务服务名不能跨机器解析。
- `alloy/config.alloy` 现在只发现中心本机 `huahuo-server-dev` 四个角色和 `huahuo-agent` 的 Docker 容器。若业务留在旧节点，需在业务节点部署日志采集器并配置到新 Loki 的私网链路；仅移动中心 Alloy 不会自动获得远端 Docker 日志。更新采集器中的实际 `host` / `env` 标签。
- 远端 Dozzle Agent 登记格式为 `节点私网IP:7007|主机名|环境`，多个用逗号分隔。节点镜像仍用 `amir20/dozzle:v11.4.3`、内置 TLS，关闭 Shell/Actions。7007 的来源限制更新为新中心私网 IP。
- RDS 的白名单、RAM 权限、出网访问和区域须适配新机器；恢复 `secrets/rds-api.json`，不将凭据写进 Compose 或 Git。

Prometheus、Loki、Alloy 的管理端口继续绑定本机回环，Loki 当前未启用认证。远端日志写入入口尚未在本仓库默认开放；节点接入时配置私网监听或代理、认证与网络规则。节点 exporter 依用户要求未部署。

## 4. 启动与恢复 Grafana 目录

```sh
cd /opt/monitoring
./monitoring.sh config --quiet
./monitoring.sh pull
./monitoring.sh up -d --wait --wait-timeout 180
./monitoring.sh ps
./monitoring.sh exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
```

仓库已包含 `grafana/.light-mode`、`.logs-enabled` 和 `.rds-slow-logs-enabled`，以及完整生成的九张看板。功能精简模式只关闭闲置插件，保留告警与 Explore；所有监控容器无固定 CPU/内存配额。看板源变更后执行 `python3 grafana/build_dashboards.py`。

从原 Grafana 卷恢复时，文件夹层级与默认首页也会保留。全新安装需在 Grafana 界面把 HTTP 排查、Agent、Server 文件夹移到“花火AI”下，并将“花火AI 服务总览”设为默认首页；文件 provisioning 已提供各文件夹和稳定的 UID。

## 5. 域名与 HTTPS

将 `grafana.chuda.cc`、`dozzle.chuda.cc` 的 DNS A 记录指向新公网 IP，有 AAAA 记录时同步处理。允许公网访问 80/443，SSH 按实际管理策略开放。Prometheus 不设公网域名。

已备份证书时，在新机器恢复整套 Certbot 目录（保留 `live` 到 `archive` 的符号链接），然后安装站点与钩子：

```sh
tar --same-owner -xzpf /root/huahuo-monitor-cutover/letsencrypt.tar.gz -C /etc
install -d /etc/nginx/sites-available /etc/nginx/sites-enabled /var/lib/letsencrypt
install -m 644 nginx/monitoring.conf /etc/nginx/sites-available/monitoring
ln -s /etc/nginx/sites-available/monitoring /etc/nginx/sites-enabled/monitoring
install -d /etc/letsencrypt/renewal-hooks/deploy
install -m 755 nginx/certbot-deploy.sh /etc/letsencrypt/renewal-hooks/deploy/monitoring-nginx.sh
nginx -t
systemctl reload nginx
```

未转移旧证书时，先用 `nginx/monitoring-http.conf` 作为站点配置，创建相同的软链接并重载 Nginx；DNS 生效后申请证书，再换成上面的 HTTPS 站点与钩子。`EMAIL` 替换为你的真实通知邮箱；`--cert-name` 与站点中的证书路径保持一致。

```sh
certbot certonly --webroot -w /var/lib/letsencrypt \
  --cert-name monitoring.chuda.cc -d grafana.chuda.cc -d dozzle.chuda.cc \
  --email EMAIL --agree-tos --non-interactive
```

Ubuntu/Debian 的 apt Certbot 通常使用 `certbot.timer`，Snap 安装使用 `snap.certbot.renew.timer`；在新机器启用与安装方式对应的定时器，避免重复安装。DNS 切换后验证自动续期和 Nginx 重载：

```sh
certbot renew --cert-name monitoring.chuda.cc --dry-run --run-deploy-hooks
```

## 6. 验收与切换

登录 https://grafana.chuda.cc 与 https://dozzle.chuda.cc，确认账号、九张看板与历史日志。Grafana Explore 分别查询：

```promql
up{job="application"}
http_requests_total{job="application"}
```

Loki 查询 `{service=~"huahuo-server|huahuo-agent"} |= "http.server.completed"`，从请求表点击 traceId 确认原始日志；检查慢 SQL 采集状态、告警规则和通知渠道（原中心此前未配置告警规则）。Dozzle 打开本机及每个远端节点的一条容器日志。

确认新中心能持续收到新数据后再退役旧机器。节点地址和标签以目标环境的实际配置为准。
