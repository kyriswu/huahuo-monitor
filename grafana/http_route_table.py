"""Build a route table with volume, throughput and latency in one row."""


def build_route_metrics_table(panel, table, field, query, HTTP_SELECTED, DS,
                              pos=(0, 11, 24, 11)):
    """Combine six instant table queries using their shared route identity."""
    group = 'service,role,method,route'
    count = (f'topk(30, sum by ({group}) '
             f'(increase(http_requests_total{{{HTTP_SELECTED}}}[$__range])) > 0)')
    qps = f'sum by ({group}) (rate(http_requests_total{{{HTTP_SELECTED}}}[5m]))'

    def quantile(value):
        return (f'histogram_quantile({value}, sum by (le,{group}) '
                f'(increase(http_request_duration_seconds_bucket{{{HTTP_SELECTED}}}[$__range])))')

    measures = [('A', 'requests', count), ('B', 'qps', qps),
                ('C', 'qpm', f'60 * ({qps})'),
                ('D', 'p50', quantile('0.50')),
                ('E', 'p95', quantile('0.95')),
                ('F', 'p99', quantile('0.99'))]
    columns = {'service': '服务', 'service_key': '服务标识', 'role': '角色', 'role_key': '角色标识', 'method': '方法', 'route': '接口',
               'Value #A': '时段请求数', 'Value #B': 'QPS', 'Value #C': 'QPM',
               'Value #D': 'P50', 'Value #E': 'P95', 'Value #F': 'P99'}
    p = table(7, '接口明细 · 请求量、吞吐与响应耗时', pos, count, columns,
              description=('按所选时段请求数降序，最多 30 个接口。QPS/QPM 是所选结束时刻之前 5 分钟的平均速率；'
                           'P50/P95/P99 根据整个所选时段的耗时直方图估算，无样本时留空。'
                           '请求数为采样估算并显示取整。点击接口可按角色、方法和路由继续筛选本页。'),
              sort='Value #A')
    p['datasource'] = DS
    p['targets'] = [
        query(f'label_replace(label_replace(({expr}),"role_key","$1","role","(.+)"),"service_key","$1","service","(.+)")',
              instant=True, table=True, ref=ref)
        for ref, _name, expr in measures]

    # All queries are instant evaluations at the same dashboard end time.
    # Grafana merges their shared Time/role/method/route identity and preserves
    # their numeric fields as Value #A ... #F. Rename only after this merge;
    # renaming individual frames before merging loses the ref-specific names.
    p['transformations'] = [
        {'id': 'merge', 'options': {}},
        {'id': 'filterByValue', 'options': {'type': 'include', 'match': 'all', 'filters': [
            {'fieldName': 'Value #A', 'config': {'id': 'isNotNull', 'options': {}}}]}},
        {'id': 'filterFieldsByName', 'options': {'include': {'names': list(columns)}}},
        {'id': 'organize', 'options': {'excludeByName': {},
                                      'indexByName': {name: i for i, name in enumerate(columns)},
                                      'renameByName': columns}},
    ]
    p['fieldConfig']['defaults']['noValue'] = '—'
    p['options']['sortBy'] = [{'displayName': '时段请求数', 'desc': True}]
    field(p, '角色', {'custom.width': 95})
    field(p, '角色标识', {'custom.hidden': True})
    field(p, '服务标识', {'custom.hidden': True})
    field(p, '方法', {'custom.width': 65})
    field(p, '时段请求数', {'decimals': 0, 'custom.width': 100})
    field(p, 'QPS', {'unit': 'suffix:次/秒', 'decimals': 3, 'custom.width': 95})
    field(p, 'QPM', {'unit': 'suffix:次/分', 'decimals': 2, 'custom.width': 95})
    for name in ['P50', 'P95', 'P99']:
        field(p, name, {'unit': 's', 'decimals': 2, 'custom.width': 95,
                        'mappings': [{'type': 'special', 'options': {
                            'match': 'nan', 'result': {'text': '无样本'}}}]})
    url = ('/d/huahuo-server?from=${__from}&to=${__to}&${env:queryparam}'
           '&${host:queryparam}&${traffic:queryparam}&${business:queryparam}'
           '&var-service=${__data.fields["服务标识"]:percentencode}'
           '&var-role=${__data.fields["角色标识"]:percentencode}'
           '&var-method=${__data.fields["方法"]:percentencode}'
           '&var-route=${__data.fields["接口"]:percentencode}')
    field(p, '接口', {'links': [{'title': '按这个接口筛选吞吐和延迟', 'url': url, 'oneClick': True}]})
    return p
