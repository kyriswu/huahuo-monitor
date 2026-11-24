"""Provision the Kubernetes workload status dashboard."""
def build_kubernetes_dashboard(save, panel, table, field, semantic, variable, loki):
    scrape = 'up{job="kube-state-metrics"}'
    scope = '{job="kube-state-metrics",namespace=~"$namespace"}'
    ready = '{job="kube-state-metrics",namespace=~"$namespace",condition="true"}'
    phase = 'kube_pod_status_phase' + scope + ' == 1'
    bad_pods = (f'(kube_pod_status_ready{ready} == 0) and on (namespace,pod,uid) '
                '(kube_pod_status_phase{phase=~"Pending|Running|Unknown|Failed"} == 1)')
    restarts = f'increase(kube_pod_container_status_restarts_total{scope}[$__range])'
    replica_gaps = []
    for kind, name, desired, available in (
        ('Deployment', 'deployment', 'kube_deployment_spec_replicas', 'kube_deployment_status_replicas_available'),
        ('StatefulSet', 'statefulset', 'kube_statefulset_replicas', 'kube_statefulset_status_replicas_ready'),
        ('DaemonSet', 'daemonset', 'kube_daemonset_status_desired_number_scheduled', 'kube_daemonset_status_number_available'),
    ):
        gap = f'({desired}{scope} - {available}{scope}) > 0'
        named = f'label_replace({gap}, "workload", "$1", "{name}", "(.+)")'
        replica_gaps.append(f'label_replace({named}, "kind", "{kind}", "workload", "(.+)")')
    gaps = ' or '.join(replica_gaps)
    zero_when_scraped = f' or on () (0 * ({scrape} == 1))'
    panels = [
        semantic(panel(1, '状态采集', 'stat', (0, 0, 6, 4), scrape),
                 [(None, 'red'), (1, 'green')]),
        semantic(panel(2, '未就绪 Pod', 'stat', (6, 0, 6, 4), f'count({bad_pods})' + zero_when_scraped),
                 [(None, 'green'), (1, 'red')]),
        semantic(panel(3, '副本不足的工作负载', 'stat', (12, 0, 6, 4), f'count({gaps})' + zero_when_scraped),
                 [(None, 'green'), (1, 'red')]),
        panel(4, '容器重启 · 所选时段', 'stat', (18, 0, 6, 4), f'sum({restarts})' + zero_when_scraped,
              description='Prometheus 根据所选时间范围内的采样估算重启次数。'),
        table(5, 'Pod 状态', (0, 4, 12, 10),
              f'({phase}) * on (namespace,pod,uid) group_left(node) kube_pod_info{scope}',
              {'namespace': '命名空间', 'pod': 'Pod', 'node': '节点', 'phase': '阶段'}, sort='pod', descending=False),
        table(6, '副本不足的工作负载', (12, 4, 12, 10), gaps,
              {'namespace': '命名空间', 'kind': '类型', 'workload': '工作负载', 'Value': '缺少副本'}),
        table(7, '容器等待原因', (0, 14, 12, 8), f'kube_pod_container_status_waiting_reason{scope} == 1',
              {'namespace': '命名空间', 'pod': 'Pod', 'container': '容器', 'reason': '原因'}, sort='pod', descending=False),
        table(8, '容器重启 · 所选时段', (12, 14, 12, 8), restarts + ' > 0',
              {'namespace': '命名空间', 'pod': 'Pod', 'container': '容器', 'Value': '次数'}),
        table(9, '运行中未就绪的 Pod', (0, 22, 24, 8),
              f'(kube_pod_status_ready{ready} == 0) and on (namespace,pod,uid) '
              '(kube_pod_status_phase{phase="Running"} == 1)',
              {'namespace': '命名空间', 'pod': 'Pod'}, sort='pod', descending=False),
        {'id': 10, 'title': 'Kubernetes 告警事件', 'type': 'logs', 'datasource': loki,
         'gridPos': {'x': 0, 'y': 30, 'w': 24, 'h': 12},
         'targets': [{'refId': 'A', 'datasource': loki, 'queryType': 'range', 'editorMode': 'code',
                      'expr': '{job="kubernetes-events",namespace=~"$namespace"} | logfmt | type="Warning"'}],
         'options': {'showTime': True, 'wrapLogMessage': True, 'enableLogDetails': True}},
    ]
    field(panels[0], 'Value', {'mappings': [{'type': 'value', 'options': {
        '0': {'text': '异常'}, '1': {'text': '正常'},
    }}]})
    namespace = variable('namespace', '命名空间', 'label_values(kube_pod_info{job="kube-state-metrics"}, namespace)')
    save('kubernetes-status', 'Kubernetes 工作负载', panels, 'kubernetes', [namespace],
         '查看监控和业务命名空间的 Pod、容器重启、工作负载副本与 Kubernetes 告警事件。')
