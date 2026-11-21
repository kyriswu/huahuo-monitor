"""Build the HTTP overview from the deployment's existing metrics."""

from copy import deepcopy
import json


def build_http_overview(panel, table, field, query, semantic, thresholds,
                        request_bars, latency_line, click_link, DS, APP,
                        HTTP_APP, TARGET, request_count, zero, errors, p95,
                        slow_count, slow_url, error_url, slow_table, scopes,
                        classify_business):
    """Return overview panels; HTTP scope never filters the target-status table.

    The overall comparison uses APP plus each class's own route pattern.
    Other HTTP panels use HTTP_APP, with the selected scope/business patterns.
    """
    def with_scope(url):
        for name in ('service', 'traffic', 'business'):
            token = '${' + name + ':queryparam}'
            if token not in url:
                url += '&' + token
        return url

    rate = f'sum(rate(http_requests_total{{{HTTP_APP}}}[5m]))'
    slow_total = f'({slow_count(HTTP_APP)} or {zero(TARGET)})'
    duration_total = f'sum(increase(http_request_duration_seconds_count{{{HTTP_APP}}}[$__range]))'
    panels = [
        panel(3, '时段请求数', 'stat', (0, 0, 4, 4),
              request_count(HTTP_APP) + ' or ' + zero(TARGET),
              description='当前 HTTP 类别在所选时段内的已完成请求增量；Prometheus 采样估算，显示取整。'),
        panel(18, 'QPS · 5分钟均值', 'stat', (4, 0, 4, 4),
              rate + ' or ' + zero(TARGET), 'suffix:次/秒',
              '以所选时间范围结束时刻为准，计算此前 5 分钟的已完成请求平均速率。随 HTTP 类别筛选。'),
        panel(19, 'QPM · 5分钟均值', 'stat', (8, 0, 4, 4),
              '60 * ' + rate + ' or ' + zero(TARGET), 'suffix:次/分',
              '与 QPS 使用相同 5 分钟窗口，QPM = QPS × 60；它是每分钟平均速率，不是某个自然分钟的精确次数。'),
        semantic(panel(4, '时段 5xx 请求', 'stat', (12, 0, 4, 4),
                       errors(HTTP_APP, TARGET),
                       description='当前 HTTP 类别中服务端 5xx 响应数。401、404 等 4xx 不计入。点击查看状态码与接口。'),
                 [(None, 'green'), (0.5, 'red')]),
        panel(9, '时段慢请求', 'stat', (16, 0, 4, 4), slow_total,
              description='当前 HTTP 类别中严格超过顶部耗时阈值的已完成请求；采样估算。点击查看排行与已保留日志。'),
        panel(10, '慢请求占比', 'stat', (20, 0, 4, 4),
              f'100 * {slow_total} / ({duration_total} > 0)', 'percent',
              '当前 HTTP 类别中，严格超过顶部阈值的请求占全部已完成请求的比例；没有请求时留空。',
              no_value='无请求'),
    ]
    panels[1]['fieldConfig']['defaults']['decimals'] = 3
    panels[2]['fieldConfig']['defaults']['decimals'] = 2
    panels[5]['fieldConfig']['defaults']['decimals'] = 2
    click_link(panels[3], '查看当前类别的 5xx 状态码和接口', with_scope(error_url()))
    for p in (panels[4], panels[5]):
        click_link(p, '查看当前类别的慢请求', with_scope(slow_url(threshold_variable=True)))

    def donut(pid, title, pos, expr, legend, description):
        p = panel(pid, title, 'piechart', pos, expr, description=description)
        p['targets'] = [query(expr, legend, instant=True)]
        p['options'] = {
            'pieType': 'donut', 'displayLabels': ['percent'],
            'legend': {'displayMode': 'table', 'placement': 'bottom',
                       'showLegend': True, 'values': ['value', 'percent']},
            'tooltip': {'mode': 'single', 'sort': 'desc', 'hideZeros': False},
            'reduceOptions': {'calcs': ['lastNotNull'], 'values': False, 'fields': ''},
        }
        p['fieldConfig']['defaults'].update({'min': 0, 'decimals': 0})
        return p

    class_expressions = []
    for scope in scopes:
        scope_selector = APP + ',traffic=' + json.dumps(scope['pattern'])
        count = request_count(scope_selector) + ' or ' + zero(TARGET)
        class_expressions.append(
            'label_replace(label_replace((' + count + '),"http_class",'
            + json.dumps(scope['title'], ensure_ascii=False) + ',"__name__",".*"),'
            '"scope_pattern",' + json.dumps(scope['pattern']) + ',"__name__",".*")')
    classes = donut(21, 'HTTP 类别占比 · 点击筛选', (0, 4, 6, 9),
                    ' or '.join(class_expressions), '{{http_class}}',
                    '按应用上报的 traffic 分类，比较所选服务、环境、主机和时段的全部 HTTP 类别；不按 URL 猜分类。分类表示物理路由性质，不代表调用者来自公网或私网。旧版无 traffic 的历史指标不混入。点击扇区筛选并重置业务；顶部支持复选。')
    for scope in scopes:
        field(classes, scope['title'], {'color': {'mode': 'fixed', 'fixedColor': scope['color']}})
    switch_url = ('/d/monitoring-overview?from=${__from}&to=${__to}&${env:queryparam}'
                  '&${host:queryparam}&${service:queryparam}&${slow_seconds:queryparam}'
                  '&var-traffic=${__field.labels.scope_pattern:percentencode}&var-business=%24__all')
    click_link(classes, '切换到这个 HTTP 类别', switch_url)
    panels.append(classes)

    business_expr = ('sum by (business,business_pattern) (' + classify_business(
        f'increase(http_requests_total{{{HTTP_APP}}}[$__range])') + ') > 0')
    businesses = donut(22, '业务请求占比 · 点击下钻', (6, 4, 6, 9),
                       business_expr, '{{business}}',
                       '按路由模块细分当前所选 HTTP 类别与业务，仅显示所选时段有请求增量的业务。图例显示模块、请求量和占比；点击扇区继续筛选该业务，顶部复选框支持多选。')
    business_url = ('/d/monitoring-overview?from=${__from}&to=${__to}&${env:queryparam}'
                    '&${host:queryparam}&${service:queryparam}&${traffic:queryparam}&${slow_seconds:queryparam}'
                    '&var-business=${__field.labels.business_pattern:percentencode}')
    click_link(businesses, '查看这个业务的请求', business_url)
    panels.append(businesses)

    qps = latency_line(panel(6, 'QPS 趋势 · 5分钟均值', 'timeseries',
                             (12, 4, 12, 9), rate, 'suffix:次/秒',
                             '当前 HTTP 类别的已完成请求速率；每个点使用此前 5 分钟的滑动平均，QPM = QPS × 60。',
                             'QPS'))
    qps['fieldConfig']['defaults'].update({'decimals': 3, 'color': {'mode': 'fixed', 'fixedColor': 'blue'}})
    qps['options']['legend']['showLegend'] = False
    panels.append(qps)

    distribution = panel(7, '响应状态码 · 所选类别', 'bargauge', (0, 13, 8, 8),
                         f'sort_desc(sum by (status) (increase(http_requests_total{{{HTTP_APP}}}[$__range])) > 0)',
                         legend='{{status}}', description='所选时段内，当前 HTTP 类别各响应状态码的请求数；按数量降序。')
    distribution['options'] = {
        'orientation': 'horizontal', 'displayMode': 'basic', 'valueMode': 'color',
        'namePlacement': 'left', 'showUnfilled': False, 'minVizHeight': 24,
        'maxVizHeight': 52, 'reduceOptions': {'calcs': ['lastNotNull'], 'values': False, 'fields': ''},
    }
    distribution['fieldConfig']['defaults'].update({'min': 0, 'decimals': 0})
    for pattern, color in [('^2[0-9][0-9]$', 'green'), ('^3[0-9][0-9]$', 'blue'),
                           ('^4[0-9][0-9]$', 'orange'), ('^5[0-9][0-9]$', 'red')]:
        distribution['fieldConfig']['overrides'].append({
            'matcher': {'id': 'byRegexp', 'options': pattern},
            'properties': [{'id': 'color', 'value': {'mode': 'fixed', 'fixedColor': color}}],
        })
    panels.append(distribution)

    p95_expr = p95(HTTP_APP, '5m', '')
    latency = latency_line(panel(8, '响应延迟 · P50 / P95 / P99', 'timeseries',
                                 (8, 13, 16, 8), p95_expr.replace('0.95', '0.50', 1), 's',
                                 '当前 HTTP 类别中全部已完成请求的耗时分位数，按 5 分钟窗口从直方图估算；没有请求的窗口留空。',
                                 'P50', '无请求'))
    latency['targets'] = [query(p95_expr.replace('0.95', '0.50', 1), 'P50', ref='A'),
                          query(p95_expr, 'P95', ref='B'),
                          query(p95_expr.replace('0.95', '0.99', 1), 'P99', ref='C')]
    for name, color in [('P50', 'green'), ('P95', 'blue'), ('P99', 'orange')]:
        field(latency, name, {'color': {'mode': 'fixed', 'fixedColor': color}})
    panels.append(latency)

    panels.append({'id': 13, 'title': '慢请求 · 当前 HTTP 类别', 'type': 'row',
                   'collapsed': False, 'gridPos': {'x': 0, 'y': 21, 'w': 24, 'h': 1}})
    slow_trend = request_bars(panel(11, '慢请求速率 · 5分钟均值', 'timeseries',
                                    (0, 22, 10, 8),
                                    '60 * ' + slow_count(HTTP_APP, window='5m', by='role', rate=True),
                                    'suffix:次/分',
                                    '当前 HTTP 类别中严格超过顶部阈值的已完成请求，按角色堆叠；显示此前 5 分钟的每分钟平均速率。长连接耗时需结合接口用途判断。',
                                    '{{role}}'))
    slow_trend['fieldConfig']['defaults']['custom']['axisSoftMax'] = 1
    panels.append(slow_trend)

    rank = deepcopy(slow_table)
    rank.update({'id': 12, 'title': '慢接口排行 · 点击查看单次请求',
                 'gridPos': {'x': 10, 'y': 22, 'w': 14, 'h': 8},
                 'description': '按当前 HTTP 类别内的慢请求次数降序，最多 10 行。点击接口或数量，查看日志覆盖范围与已保留的逐条请求。'})
    rank['targets'] = [query(
        f'topk(10, label_replace(label_replace({slow_count(HTTP_APP, by="service,role,host,method,route")} > 0,"role_key","$1","role","(.+)"),"service_key","$1","service","(.+)"))',
        instant=True, table=True)]
    field(rank, '主机', {'custom.hidden': True})
    field(rank, '角色', {'custom.width': 100})
    field(rank, '方法', {'custom.width': 60})
    field(rank, '慢请求数', {'custom.width': 100})
    panels.append(rank)

    status_expr = (f'(up{{{TARGET}}} * on (job,instance) group_left(version) '
                   f'application_info{{{APP}}}) or up{{{TARGET}}}')
    status = table(5, '实例采集状态与版本', (0, 30, 24, 6), status_expr,
                   {'service': '服务', 'role': '角色', 'host': '主机', 'instance': '抓取地址', 'version': '应用版本', 'Value': '采集状态'},
                   '目标状态按环境和主机查询，不随 HTTP 类别筛选。持续离线的已配置实例仍会保留。',
                   sort='role', descending=False)
    field(status, '采集状态', {
        'mappings': [{'type': 'value', 'options': {
            '0': {'text': '异常', 'color': 'red'}, '1': {'text': '正常', 'color': 'green'}}}],
        'custom.cellOptions': {'type': 'color-text'}, 'custom.width': 110})
    field(status, '主机', {'custom.width': 300})
    panels.append(status)
    return panels
