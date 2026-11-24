# 测试监控本地存储

测试 ACK 的五个状态卷复用北京 Node01 上已挂载到 `/var/lib/container` 的 ext4 磁盘。Node01 的 `kubernetes.io/hostname` 为 `cn-beijing.172.16.71.173`，对应 ECS `i-2zeaydxol26pp3dxg1ak`。各 PV 通过节点亲和性和预留 PVC 固定到相应目录。无状态的 kube-state-metrics 仍由调度器选择节点。

StorageClass `huahuo-monitor-test-local` 使用 `kubernetes.io/no-provisioner`、`WaitForFirstConsumer` 和 `Retain`。五个卷共声明 12 GiB，Prometheus 4 GiB，Loki 5 GiB，其余各 1 GiB。test 的指标和 Loki 日志保留 30 天，Prometheus TSDB 另设 3 GB 保留上限。卷目录共享同一文件系统，实际用量以磁盘监控为准。迁移或重装节点前备份数据，Node01 不可用时需恢复目录后再调度依赖这些卷的 Pod。 [Kubernetes 本地卷](https://kubernetes.io/docs/concepts/storage/volumes/#local)

## 首次准备磁盘目录

在 Node01 用有权限的宿主账号确认 `/var/lib/container` 已挂到预期的数据盘且空间足够。开新目录前核对下列路径及其父路径均为真实目录。已有数据目录时检查现有权限并保留数据。

```sh
findmnt --mountpoint /var/lib/container
df -h /var/lib/container
```

首次创建专用目录时使用对应容器的 UID/GID。父目录由 root 管理，工作负载通过各自的 PV 访问数据子目录。

```sh
sudo install -d -m 0750 -o root -g root /var/lib/container/huahuo-monitor
sudo install -d -m 0700 -o 65534 -g 65534 /var/lib/container/huahuo-monitor/prometheus
sudo install -d -m 0700 -o 472 -g 472 /var/lib/container/huahuo-monitor/grafana
sudo install -d -m 0700 -o 10001 -g 10001 /var/lib/container/huahuo-monitor/loki
sudo install -d -m 0700 -o 473 -g 473 /var/lib/container/huahuo-monitor/alloy
sudo install -d -m 0700 -o 1000 -g 1000 /var/lib/container/huahuo-monitor/rds-slow-logs
```

如果 `getenforce` 返回 `Enforcing`，在应用 PV 前核对容器 SELinux 策略。宿主采用 `container-selinux` 策略时，首次为专用目录建立持久的 `container_file_t` 规则并恢复标签。已有规则时核对现有标签，避免变更其他容器目录的规则。

```sh
sudo semanage fcontext -a -t container_file_t '/var/lib/container/huahuo-monitor(/.*)?'
sudo restorecon -Rv /var/lib/container/huahuo-monitor
```

## 首次准备集群对象

测试集群管理员使用已确认的 context 创建监控命名空间，再应用 StorageClass 和五个 PV。保留 [测试集群首次配置](../../README.md#首次集群配置) 中的 Secret 与 ConfigMap 步骤。部署时用本目录的渲染脚本叠加仓库根配置，五个 PVC 会按名称绑定各自的 PV。

```sh
kubectl --context "$TEST_CONTEXT" get nodes -l kubernetes.io/hostname=cn-beijing.172.16.71.173
kubectl --context "$TEST_CONTEXT" apply -f k8s/namespace.yaml
kubectl --context "$TEST_CONTEXT" apply -f k8s/overlays/test/local-storage.yaml
bash k8s/overlays/test/render.sh
```

已有 PVC 如果绑定其他卷，先核对并迁移数据后再切换。首次发布结束后检查五个 PVC 均为 `Bound`，对应工作负载在 Node01 就绪。

test 镜像固定到现有内网 ACR 的 ARM64 digest。ACK 的 ACR 免密组件在 `ns-monitoring` 为 `huahuo-monitor-images` ServiceAccount 自动轮换 `acr-credential-secret-aggregation`，监控 Pod 使用同命名空间的该凭据拉取镜像。
