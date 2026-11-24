"""Build the HTTP overview from the deployment's existing metrics."""

from copy import deepcopy
import json


def build_http_overview(panel, table, field, query, semantic,
                        request_bars, latency_line, click_link, APP,
                        HTTP_APP, TARGET, request_count, zero, p95,
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
    request_total = request_count(HTTP_APP)
    error_total = f'sum(increase(http_requests_total{{{HTTP_APP},status=~"5.."}}[$__range]))'
    targets = f'up{{{TARGET}}}'
    undiscovered = f'(absent({targets}) and on () (up{{job="prometheus"}} == 1))'
    slow_total = f'({slow_count(HTTP_APP)} or {zero(TARGET)})'
    duration_total = f'sum(increase(http_request_duration_seconds_count{{{HTTP_APP}}}[$__range]))'
    panels = [
        panel(18, 'QPS · 5分钟均值', 'stat', (0, 0, 6, 4),
              rate + ' or ' + zero(TARGET), 'suffix:次/秒',
              '以所选时间范围结束时刻为准，计算此前 5 分钟的已完成请求平均速率。随 HTTP 类别筛选。',
              no_value='无采集样本'),
        panel(4, '时段 5xx 比例', 'stat', (6, 0, 6, 4),
              f'100 * ({error_total} or (0 * {request_total})) / ({request_total} > 0)', 'percent',
              '所选时段的 5xx 占全部已完成请求的比例。点击查看接口、状态码与日志。',
              no_value='无有效请求样本'),
        panel(28, '时段 P95 耗时', 'stat', (12, 0, 6, 4),
              p95(HTTP_APP, '$__range', ''), 's',
              '所选时段已完成请求的直方图估算。SSE 耗时包含连接寿命，Agent 首文本等待单独统计。',
              no_value='无耗时样本'),
        semantic(panel(29, '抓取失败目标', 'stat', (18, 0, 6, 4),
                       f'sum(1 - {targets}) or (-1 * {undiscovered})',
                       description='按当前时间范围结束时刻统计已发现但抓取失败的目标，明细见实例采集状态。',
                       no_value='无采集样本'),
                 [(None, 'green'), (1, 'red')]),
    ]
    panels[0]['fieldConfig']['defaults']['decimals'] = 3
    panels[1]['fieldConfig']['defaults']['decimals'] = 2
    panels[2]['fieldConfig']['defaults']['decimals'] = 3
    panels[3]['fieldConfig']['defaults']['mappings'] = [{
        'type': 'value', 'options': {'-1': {'text': '未发现目标', 'color': 'orange'}},
    }]
    click_link(panels[1], '查看 5xx 状态码与接口', with_scope(error_url()))
    depth_panels = [
        panel(3, '时段请求数', 'stat', (0, 28, 8, 4),
              request_total + ' or ' + zero(TARGET),
              description='所选时段内已完成请求的采样估算，显示取整。', no_value='无采集样本'),
        panel(9, '时段慢请求', 'stat', (8, 28, 8, 4), slow_total,
              description='当前 HTTP 类别中严格超过顶部耗时阈值的已完成请求；采样估算。点击查看排行与已保留日志。'),
        panel(10, '慢请求占比', 'stat', (16, 28, 8, 4),
              f'100 * {slow_total} / ({duration_total} > 0)', 'percent',
              '当前 HTTP 类别中，严格超过顶部阈值的请求占全部已完成请求的比例；没有请求时留空。',
              no_value='无请求'),
    ]
    depth_panels[2]['fieldConfig']['defaults']['decimals'] = 2
    for p in depth_panels[1:]:
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
    classes = donut(21, 'HTTP 类别占比 · 点击筛选', (0, 32, 8, 8),
                    ' or '.join(class_expressions), '{{http_class}}',
                    '按应用上报的 traffic 分类，比较所选服务、环境、主机和时段的全部 HTTP 类别；不按 URL 猜分类。分类表示物理路由性质，不代表调用者来自公网或私网。旧版无 traffic 的历史指标不混入。点击扇区筛选并重置业务；顶部支持复选。')
    for scope in scopes:
        field(classes, scope['title'], {'color': {'mode': 'fixed', 'fixedColor': scope['color']}})
    switch_url = ('/d/monitoring-overview?from=${__from}&to=${__to}&${env:queryparam}'
                  '&${host:queryparam}&${service:queryparam}&${slow_seconds:queryparam}'
                  '&var-traffic=${__field.labels.scope_pattern:percentencode}&var-business=%24__all')
    click_link(classes, '切换到这个 HTTP 类别', switch_url)
    depth_panels.append(classes)

    business_expr = ('sum by (business,business_pattern) (' + classify_business(
        f'increase(http_requests_total{{{HTTP_APP}}}[$__range])') + ') > 0')
    businesses = donut(22, '业务请求占比 · 点击下钻', (8, 32, 8, 8),
                       business_expr, '{{business}}',
                       '按路由模块细分当前所选 HTTP 类别与业务，仅显示所选时段有请求增量的业务。图例显示模块、请求量和占比；点击扇区继续筛选该业务，顶部复选框支持多选。')
    business_url = ('/d/monitoring-overview?from=${__from}&to=${__to}&${env:queryparam}'
                    '&${host:queryparam}&${service:queryparam}&${traffic:queryparam}&${slow_seconds:queryparam}'
                    '&var-business=${__field.labels.business_pattern:percentencode}')
    click_link(businesses, '查看这个业务的请求', business_url)
    depth_panels.append(businesses)

    qps = latency_line(panel(6, 'QPS 趋势 · 5分钟均值', 'timeseries',
                             (0, 4, 12, 8), rate, 'suffix:次/秒',
                             '当前 HTTP 类别的已完成请求速率，每个点为此前 5 分钟的平均值。',
                             'QPS'))
    qps['fieldConfig']['defaults'].update({'decimals': 3, 'color': {'mode': 'fixed', 'fixedColor': 'blue'}})
    qps['options']['legend']['showLegend'] = False
    panels.append(qps)

    distribution = panel(7, '响应状态码 · 所选类别', 'bargauge', (16, 32, 8, 8),
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
    depth_panels.append(distribution)

    p95_expr = p95(HTTP_APP, '5m', '')
    latency = latency_line(panel(8, 'P95 响应延迟趋势', 'timeseries',
                                 (12, 4, 12, 8), p95_expr, 's',
                                 '按 5 分钟窗口从直方图估算。没有请求的窗口留空。',
                                 'P95', '无耗时样本'))
    panels.append(latency)

    slow_trend = request_bars(panel(11, '慢请求速率 · 5分钟均值', 'timeseries',
                                    (0, 40, 10, 8),
                                    '60 * ' + slow_count(HTTP_APP, window='5m', by='role', rate=True),
                                    'suffix:次/分',
                                    '当前 HTTP 类别中严格超过顶部阈值的已完成请求，按角色堆叠；显示此前 5 分钟的每分钟平均速率。长连接耗时需结合接口用途判断。',
                                    '{{role}}'))
    slow_trend['fieldConfig']['defaults']['custom']['axisSoftMax'] = 1
    depth_panels.append(slow_trend)

    rank = deepcopy(slow_table)
    rank.update({'id': 12, 'title': '慢接口排行 · 点击查看单次请求',
                 'gridPos': {'x': 10, 'y': 40, 'w': 14, 'h': 8},
                 'description': '按当前 HTTP 类别内的慢请求次数降序，最多 10 行。点击接口或数量，查看日志覆盖范围与已保留的逐条请求。'})
    rank['targets'] = [query(
        f'topk(10, label_replace(label_replace({slow_count(HTTP_APP, by="service,role,host,method,route")} > 0,"role_key","$1","role","(.+)"),"service_key","$1","service","(.+)"))',
        instant=True, table=True)]
    field(rank, '主机', {'custom.hidden': True})
    field(rank, '角色', {'custom.width': 100})
    field(rank, '方法', {'custom.width': 60})
    field(rank, '慢请求数', {'custom.width': 100})
    depth_panels.append(rank)
    version = f'topk by (job,instance) (1, timestamp(application_info{{{APP}}}))'
    status_expr = (f'({targets} + on (job,instance) group_left(version) (0 * {version})) '
                   f'or on (job,instance) {targets}')
    status = table(5, '实例采集状态与版本', (0, 17, 24, 10), status_expr,
                   {'service': '服务', 'role': '角色', 'host': '主机', 'instance': '抓取地址', 'version': '应用版本', 'Value': '采集状态'},
                   '按环境、服务和主机筛选抓取目标。版本为该目标最近一次上报的值。',
                   sort='role', descending=False)
    field(status, '采集状态', {
        'mappings': [{'type': 'value', 'options': {
            '0': {'text': '抓取失败', 'color': 'red'}, '1': {'text': '抓取成功', 'color': 'green'}}}],
        'custom.cellOptions': {'type': 'color-text'}, 'custom.width': 110})
    field(status, '主机', {'custom.width': 300})
    panels.append(status)
    panels.append({'id': 13, 'title': 'HTTP 请求构成与慢请求', 'type': 'row',
                   'collapsed': True, 'gridPos': {'x': 0, 'y': 27, 'w': 24, 'h': 1},
                   'panels': depth_panels})
    return panels
