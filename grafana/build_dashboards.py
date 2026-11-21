"""Build provisioned dashboards from the metrics and logs this deployment exposes."""
import json
from copy import deepcopy
from pathlib import Path
from http_classification import SCOPES, scope_variable, business_variable, classify_business

ROOT = Path(__file__).resolve().parent / 'dashboards'
LOGS_ENABLED = (Path(__file__).resolve().parent / '.logs-enabled').exists()
RDS_LOGS_ENABLED = (Path(__file__).resolve().parent / '.rds-slow-logs-enabled').exists()
LIGHT_MODE = (Path(__file__).resolve().parent / '.light-mode').exists()
LOG_LIMIT = 200 if LIGHT_MODE else 500
DS = {'type': 'prometheus', 'uid': 'prometheus'}
LOKI = {'type': 'loki', 'uid': 'loki'}
APP = 'job="application",service=~"${service:pipe}",exported_env=~"$env",host=~"$host"'
HTTP_APP = APP + ',traffic=~"${traffic:pipe}",route=~"${business:pipe}"'
TARGET = 'job="application",service=~"${service:pipe}",env=~"$env",host=~"$host"'
ROLE = ',role=~"$role"'
ROLES = {'api-server': 'API 服务', 'admin': '管理端', 'worker': '后台处理', 'scheduler': '定时任务', 'agent': 'Agent'}
COLORS = {'api-server': 'blue', 'admin': 'purple', 'worker': 'green', 'scheduler': 'orange', 'agent': 'red'}


def thresholds(steps):
    return {'mode': 'absolute', 'steps': [{'color': c, 'value': v} for v, c in steps]}


def query(expr, legend='', instant=False, table=False, ref='A'):
    return {'refId': ref, 'expr': expr, 'datasource': DS, 'legendFormat': legend,
            'instant': instant, 'range': not instant,
            'format': 'table' if table else 'time_series'}


def panel(pid, title, kind, pos, expr, unit='short', description='', legend='', no_value='暂无数据'):
    p = {'id': pid, 'title': title, 'type': kind,
         'gridPos': dict(zip(('x', 'y', 'w', 'h'), pos)), 'datasource': DS,
         'description': description,
         'fieldConfig': {'defaults': {'unit': unit, 'noValue': no_value,
                                     'color': {'mode': 'palette-classic'},
                                     'thresholds': thresholds([(None, 'green')])}, 'overrides': []},
         'targets': [query(expr, legend, kind in ('stat', 'table', 'bargauge'), kind == 'table')],
         'options': {}}
    if kind == 'stat':
        p['options'] = {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                        'colorMode': 'value', 'graphMode': 'none', 'textMode': 'value',
                        'orientation': 'horizontal', 'justifyMode': 'center'}
        p['fieldConfig']['defaults']['decimals'] = 0
        p['fieldConfig']['defaults']['color'] = {'mode': 'fixed', 'fixedColor': 'blue'}
    if kind == 'timeseries':
        p['options'] = {'legend': {'displayMode': 'list', 'placement': 'bottom', 'calcs': []},
                        'tooltip': {'mode': 'multi', 'sort': 'desc'}}
        p['fieldConfig']['defaults']['min'] = 0
        p['fieldConfig']['defaults']['custom'] = {
            'drawStyle': 'line', 'lineInterpolation': 'linear', 'lineWidth': 2,
            'fillOpacity': 8, 'showPoints': 'never', 'spanNulls': False,
            'axisBorderShow': False, 'axisPlacement': 'auto', 'scaleDistribution': {'type': 'linear'}}
        for role, name in ROLES.items():
            p['fieldConfig']['overrides'].append({'matcher': {'id': 'byName', 'options': role},
                                                 'properties': [{'id': 'displayName', 'value': name},
                                                                {'id': 'color', 'value': {'mode': 'fixed', 'fixedColor': COLORS[role]}}]})
    return p


def request_bars(p):
    """Request volume is discrete; stack roles to show the total and composition."""
    p['fieldConfig']['defaults']['custom'].update({
        'drawStyle': 'bars', 'lineWidth': 0, 'fillOpacity': 85,
        'barAlignment': 0, 'stacking': {'mode': 'normal', 'group': 'requests'}})
    return p


def latency_line(p):
    """Mark observed latency windows so isolated low-traffic samples remain visible."""
    p['fieldConfig']['defaults']['custom'].update({'showPoints': 'always', 'pointSize': 4})
    return p


def semantic(p, steps):
    p['fieldConfig']['defaults']['color'] = {'mode': 'thresholds'}
    p['fieldConfig']['defaults']['thresholds'] = thresholds(steps)
    return p


def table(pid, title, pos, expr, columns, description='', sort='Value', descending=True):
    p = panel(pid, title, 'table', pos, expr, description=description)
    p['options'] = {'showHeader': True, 'cellHeight': 'md', 'sortBy': [{'displayName': columns.get(sort, sort), 'desc': descending}],
                    'footer': {'show': False}}
    p['transformations'] = [
        {'id': 'filterFieldsByName', 'options': {'include': {'names': list(columns)}}},
        {'id': 'organize', 'options': {'excludeByName': {},
                                      'indexByName': {k: i for i, k in enumerate(columns)},
                                      'renameByName': columns}}]
    p['fieldConfig']['defaults']['decimals'] = 0
    p['fieldConfig']['defaults']['custom'] = {'align': 'left', 'cellOptions': {'type': 'auto'}}
    for field_name in ('角色',):
        p['fieldConfig']['overrides'].append({'matcher': {'id': 'byName', 'options': field_name},
            'properties': [{'id': 'mappings', 'value': [{'type': 'value', 'options': {
                r: {'text': n} for r, n in ROLES.items()}}]}, {'id': 'custom.width', 'value': 120}]})
    p['fieldConfig']['overrides'].append({'matcher': {'id': 'byName', 'options': '服务'},
        'properties': [{'id': 'mappings', 'value': [{'type': 'value', 'options': {
            'huahuo-server': {'text': 'Server'}, 'huahuo-agent': {'text': 'Agent'}}}]},
            {'id': 'custom.width', 'value': 110}]})
    return p


def field(p, name, properties):
    p['fieldConfig']['overrides'].append({'matcher': {'id': 'byName', 'options': name},
                                         'properties': [{'id': k, 'value': v} for k, v in properties.items()]})


def variable(name, label, expression, default=None):
    return {'name': name, 'label': label, 'type': 'query', 'datasource': DS,
            'query': {'query': expression, 'refId': 'StandardVariableQuery'}, 'definition': expression,
            'refresh': 1, 'multi': True, 'includeAll': True, 'allValue': '.*', 'sort': 1,
            'current': {'selected': True, 'text': [default or 'All'], 'value': [default or '$__all']}, 'options': []}


def variables(with_role=False):
    result = [variable('env', '环境', 'label_values(application_info{job="application",service=~"huahuo-server|huahuo-agent"}, exported_env)', 'test'),
              {'name': 'service', 'label': '项目服务', 'type': 'custom',
               'query': 'Server : huahuo-server,Agent : huahuo-agent', 'multi': True,
               'includeAll': True, 'allValue': 'huahuo-server|huahuo-agent',
               'current': {'selected': True, 'text': ['All'], 'value': ['$__all']},
               'options': [{'text': 'Server', 'value': 'huahuo-server'}, {'text': 'Agent', 'value': 'huahuo-agent'}]},
              variable('host', '主机', 'label_values(application_info{' + APP + '}, host)')]
    if with_role:
        result.append(variable('role', '角色', 'label_values(up{' + TARGET + '}, role)'))
    result.extend([scope_variable(DS), business_variable(APP, DS)])
    return result


def dashboard_links(uid):
    home = ('服务总览', '/d/monitoring-overview')
    http = ('HTTP 接口', '/d/huahuo-server')
    agent = ('Agent 运行与模型', '/d/agent-runtime')
    server = ('Server 任务与依赖', '/d/server-workloads')
    platform = ('平台采集', '/d/prometheus-health')
    if uid in ('huahuo-server', 'http-errors', 'slow-requests', 'http-request-details'):
        items = [home, http, ('HTTP 错误', '/d/http-errors'),
                 ('HTTP 慢请求', '/d/slow-requests'), ('请求日志', '/d/http-request-details')]
    elif uid == 'agent-runtime':
        items = [home, ('Agent HTTP', '/d/huahuo-server?var-service=huahuo-agent'),
                 ('Agent 请求日志', '/d/http-request-details?var-service=huahuo-agent')]
    elif uid in ('server-workloads', 'database-slow-sql'):
        items = [home, server, ('Server HTTP', '/d/huahuo-server?var-service=huahuo-server'),
                 ('数据库慢查询', '/d/database-slow-sql')]
    elif uid == 'prometheus-health':
        items = [home, platform]
    else:
        items = [http, agent, server, platform]
    result = [{'title': title, 'type': 'link', 'url': url, 'keepTime': True, 'includeVars': True}
              for title, url in items if url.split('?')[0] != '/d/' + uid]
    if uid != 'monitoring-overview':
        result.append({'title': '实时容器日志 ↗', 'type': 'link', 'url': 'https://dozzle.chuda.cc/', 'targetBlank': True})
    return result


def save(uid, title, panels, folder, vars=None, description=''):
    # Investigation pages query on open/filter/manual refresh, not on a timer.
    refresh = ('' if uid in ('http-errors', 'slow-requests', 'http-request-details', 'database-slow-sql')
               else '1m') if LIGHT_MODE else '30s'
    dashboard = {'uid': uid, 'title': title, 'description': description, 'tags': ['花火AI' if folder == 'business' else '平台运维'],
                 'timezone': 'browser', 'schemaVersion': 39, 'version': 2, 'editable': False,
                 'graphTooltip': 1, 'refresh': refresh, 'time': {'from': 'now-6h', 'to': 'now'},
                 'timepicker': {'refresh_intervals': ['1m', '5m', '15m'] if LIGHT_MODE else ['30s', '1m', '5m']},
                 'annotations': {'list': []}, 'templating': {'list': vars or []}, 'panels': panels,
                 'links': dashboard_links(uid)}
    section = {'monitoring-overview': '总览', 'agent-runtime': 'Agent',
               'server-workloads': 'Server', 'database-slow-sql': 'Server'}.get(uid, 'HTTP 排查')
    dest = ROOT / ('平台运维' if folder == 'platform' else '花火AI/' + section)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / (uid + '.json')
    for previous in ROOT.rglob(uid + '.json'):
        if previous != target:
            previous.unlink()
    target.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + '\n')


def requests(sel=HTTP_APP):
    return f'http_requests_total{{{sel}}}'


def request_count(sel=HTTP_APP):
    return f'sum(increase({requests(sel)}[$__range]))'


def zero(sel=TARGET):
    return f'(0 * count(up{{{sel}}}))'


def errors(sel=HTTP_APP, base=TARGET):
    return f'(sum(increase(http_requests_total{{{sel},status=~"5.."}}[$__range])) or {zero(base)})'


def p95(sel=HTTP_APP, window='$__rate_interval', by='role'):
    group = ','.join(['le'] + ([by] if by else []))
    return f'histogram_quantile(0.95, sum by ({group}) (increase(http_request_duration_seconds_bucket{{{sel}}}[{window}])))'


def slow_count(sel=HTTP_APP, threshold='$slow_seconds', window='$__range', by='', rate=False):
    """Exact bucket boundary: count durations strictly above the selected threshold."""
    aggregate = f'sum by ({by})' if by else 'sum'
    operation = 'rate' if rate else 'increase'
    total = f'{aggregate}({operation}(http_request_duration_seconds_count{{{sel}}}[{window}]))'
    # Both v1 exporters serialize integer boundaries without a trailing .0.
    boundary = threshold[:-2] if threshold.endswith('.0') else threshold
    fast = f'{aggregate}({operation}(http_request_duration_seconds_bucket{{{sel},le="{boundary}"}}[{window}]))'
    return f'clamp_min({total} - {fast}, 0)'


def slow_url(with_role=False, focus=False, threshold_variable=False):
    url = ('/d/slow-requests?from=${__from}&to=${__to}&${env:queryparam}'
           '&${service:queryparam}&${traffic:queryparam}&${business:queryparam}')
    if focus:
        url = url.replace('${service:queryparam}', 'var-service=${__data.fields["服务标识"]:percentencode}')
        url += '&var-role=${__data.fields["角色标识"]:percentencode}&var-host=${__data.fields["主机"]:percentencode}'
        url += '&var-method=${__data.fields["方法"]:percentencode}&var-route=${__data.fields["接口"]:percentencode}'
        url += '&${slow_seconds:queryparam}&var-request_mode=slow'
    else:
        url += '&${host:queryparam}'
        url += '&${role:queryparam}' if with_role else '&var-role=%24__all'
        if with_role:
            url += '&${method:queryparam}&${route:queryparam}'
        url += '&${slow_seconds:queryparam}' if threshold_variable else '&var-slow_seconds=1'
    return url


def error_url(with_role=False, status='5..', focus=False):
    url = ('/d/http-errors?from=${__from}&to=${__to}&${env:queryparam}'
           '&${service:queryparam}&${traffic:queryparam}&${business:queryparam}')
    if not focus:
        url += '&${host:queryparam}'
        url += '&${role:queryparam}' if with_role else '&var-role=%24__all'
        if with_role:
            url += '&${method:queryparam}&${route:queryparam}'
    url += '&var-status=' + status
    if focus:
        url = url.replace('${service:queryparam}', 'var-service=${__data.fields["服务标识"]:percentencode}')
        url += '&var-role=${__data.fields["角色标识"]:percentencode}&var-host=${__data.fields["主机"]:percentencode}'
        url += '&var-method=${__data.fields["方法"]:percentencode}&var-route=${__data.fields["接口"]:percentencode}'
    return url


def click_link(p, title, url):
    p['fieldConfig']['defaults']['links'] = [{'title': title, 'url': url, 'oneClick': True}]
    return p


# The overview is assembled after the reusable HTTP drilldown panels.


app, tgt = APP + ROLE, TARGET + ROLE

http_app = HTTP_APP + ROLE
http_selected = http_app + ',method=~"$method",route=~"$route"'
detail_vars = variables(True)
detail_vars.append({'name': 'method', 'label': '方法', 'type': 'custom',
                    'query': 'GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS', 'multi': True,
                    'includeAll': True, 'allValue': '.*',
                    'current': {'text': ['All'], 'value': ['$__all'], 'selected': True}})
detail_vars.append(variable('route', '接口', f'label_values(http_request_duration_seconds_count{{{http_app}}}, route)'))
qps = f'sum(rate(http_requests_total{{{http_selected}}}[5m]))'
details = [
    panel(1, '时段请求数', 'stat', (0, 0, 4, 4), request_count(http_selected) + ' or ' + zero(tgt), description='当前勾选的 HTTP 类别、业务和接口在所选时段内的请求增量，采样估算。'),
    panel(10, 'QPS · 5分钟均值', 'stat', (4, 0, 4, 4), qps + ' or ' + zero(tgt), 'suffix:次/秒', '所选结束时刻之前 5 分钟，已完成请求的平均每秒速率。'),
    panel(11, 'QPM · 5分钟均值', 'stat', (8, 0, 4, 4), '60 * (' + qps + ') or ' + zero(tgt), 'suffix:次/分', '与 QPS 使用同一计数器，乘以 60；不是某个自然分钟的精确计数。'),
    semantic(panel(2, '时段 5xx 请求', 'stat', (12, 0, 4, 4), errors(http_selected, tgt)), [(None, 'green'), (0.5, 'red')]),
    panel(4, '时段 P95 耗时', 'stat', (16, 0, 4, 4), p95(http_selected, '$__range', ''), 's',
          '同范围已完成请求的直方图 P95 估算；无请求时留空。', no_value='无请求'),
    click_link(panel(9, '时段慢请求 > 1 秒', 'stat', (20, 0, 4, 4), slow_count(http_selected, '1.0') + ' or ' + zero(tgt),
                     description='当前 HTTP 类别、业务与接口中，已完成且耗时严格超过 1 秒的数量。'),
               '查看已筛选接口的慢请求', slow_url(True)),
    panel(5, 'QPS 趋势 · 5分钟均值', 'timeseries', (0, 4, 12, 7), qps, 'suffix:次/秒',
          '随 HTTP 类别、业务、角色、方法和接口选择变化。', 'QPS'),
    latency_line(panel(6, '响应延迟 · P50 / P95 / P99', 'timeseries', (12, 4, 12, 7),
                       p95(http_selected, '5m', '').replace('0.95', '0.50', 1), 's',
                       '最近 5 分钟全部已完成请求的分位估算；无请求窗口留空。', 'P50', '无请求'))]
details[1]['fieldConfig']['defaults']['decimals'] = 3
details[2]['fieldConfig']['defaults']['decimals'] = 2
details[4]['fieldConfig']['defaults']['decimals'] = 2
click_link(details[3], '按当前类别、业务和接口查看 5xx', error_url(True))
for quantile, name, color in [(0.50, 'P50', 'green'), (0.95, 'P95', 'blue'), (0.99, 'P99', 'orange')]:
    if name != 'P50':
        details[-1]['targets'].append(query(p95(http_selected, '5m', '').replace('0.95', str(quantile), 1), name, ref=name))
    field(details[-1], name, {'color': {'mode': 'fixed', 'fixedColor': color}})
from http_route_table import build_route_metrics_table
details.append(build_route_metrics_table(panel, table, field, query, http_selected, DS))

details.extend([
    {'id': 20, 'title': '完整响应之外的连接状态', 'type': 'row', 'collapsed': False,
     'gridPos': {'x': 0, 'y': 22, 'w': 24, 'h': 1}},
    panel(21, '当前在途 HTTP · 含流连接', 'stat', (0, 23, 6, 6),
          f'sum(http_requests_in_flight{{{APP + ROLE}}})',
          description='按服务、环境、主机和角色查询；此 Gauge 无 route/traffic 标签，不按接口或分类筛选。连接中断不等于 Agent 运行取消。'),
    table(22, '中断连接 · 与已完成响应分开统计', (6, 23, 18, 6),
          f'sum by (service,role,method,route,reason) (increase(http_aborted_requests_total{{{http_selected}}}[$__range])) > 0',
          {'service': '服务', 'role': '角色', 'method': '方法', 'route': '接口', 'reason': '中断原因', 'Value': '中断数'},
          description='未消费完整响应的连接，不能当作 5xx、业务失败或运行取消。无事件时没有序列。')])
for p in details:
    p['gridPos']['y'] += 3
    if p.get('panels'):
        for nested in p['panels']:
            nested['gridPos']['y'] += 3
details.insert(0, {'id': 30, 'title': 'HTTP 指标口径', 'type': 'text',
                   'gridPos': {'x': 0, 'y': 0, 'w': 24, 'h': 3},
                   'options': {'mode': 'markdown', 'content':
                     '按上报的 **traffic** 区分外部、内部、管理和其他请求；这里统计的是**物理 HTTP 响应**，包含鉴权子请求，不等于用户请求数。'
                     '耗时包含完整响应和 **SSE 连接寿命**；运行终态、模型耗时与首文本见 [Agent 运行与模型](/d/agent-runtime)。'
                     '旧版缺少规范分类标签的指标不混入本页。'}})
save('huahuo-server', 'HTTP 接口分析', details, 'business', detail_vars,
     '跨 Server / Agent 查看规范化 HTTP 物理请求；完整响应、中断和业务运行分别统计。')


# Error drilldown: totals stay in Prometheus; individual requests come from logs.
error_base = HTTP_APP + ROLE + ',status=~"5..",method=~"$method",route=~"$route"'
error_selected = HTTP_APP + ROLE + ',status=~"$status",method=~"$method",route=~"$route"'
error_rows = f'sum by (service,status,role,host,method,route) (increase(http_requests_total{{{error_selected}}}[$__range])) > 0'
error_vars = variables(True)
codes = ['500', '501', '502', '503', '504', '505', '506', '507', '508', '510', '511']
error_vars.append({'name': 'status', 'label': '状态码', 'type': 'custom',
                   'query': '全部5xx : 5..,' + ','.join(codes), 'multi': False,
                   'current': {'text': '全部5xx', 'value': '5..', 'selected': True},
                   'options': [{'text': '全部5xx', 'value': '5..', 'selected': True}] +
                              [{'text': c, 'value': c, 'selected': False} for c in codes]})
error_vars.append(variable('route', '接口', f'label_values(http_requests_total{{{HTTP_APP + ROLE},status=~"$status"}}, route)'))
error_vars.append({'name': 'method', 'label': '方法', 'type': 'custom',
                   'query': 'GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS', 'multi': True,
                   'includeAll': True, 'allValue': '.*',
                   'current': {'text': ['All'], 'value': ['$__all'], 'selected': True}})
error_panels = [
    semantic(panel(1, '筛选错误数', 'stat', (0, 0, 8, 4),
                   f'sum(increase(http_requests_total{{{error_selected}}}[$__range])) or {zero(tgt)}',
                   description='Prometheus 在所选时段内的错误请求增量，采样估算。'), [(None, 'green'), (0.5, 'red')]),
    panel(2, '涉及接口', 'stat', (8, 0, 8, 4), f'count(count by (method,route) ({error_rows})) or {zero(tgt)}'),
    panel(3, '涉及角色', 'stat', (16, 0, 8, 4), f'count(count by (role) ({error_rows})) or {zero(tgt)}')]
chart_values = f'label_join(sum by (status,service,role,method,route) (increase(http_requests_total{{{error_base}}}[$__range])) > 0,"request"," · ","service","role","method","route")'
for code in ['500', '501', '502', '503', '504']:
    chart_values += f' or label_replace(label_replace({zero(tgt)},"status","{code}","__name__",".*"),"request","暂无记录","status",".*")'
status_chart = panel(4, '5xx 状态码排行 · 悬停查看接口，点击筛选', 'barchart', (0, 4, 14, 10), chart_values,
                     description='按错误总数降序。悬停显示各角色、方法、接口的贡献；点击柱子筛选下方列表。常见状态码无记录时为 0。')
status_chart['targets'][0].update({'instant': True, 'range': False, 'format': 'table'})
status_chart['transformations'] = [
    {'id': 'filterFieldsByName', 'options': {'include': {'names': ['status', 'request', 'Value']}}},
    {'id': 'groupingToMatrix', 'options': {'rowField': 'status', 'columnField': 'request', 'valueField': 'Value', 'emptyValue': 'zero'}},
    {'id': 'organize', 'options': {'renameByName': {'status\\request': '状态码'}}},
    {'id': 'calculateField', 'options': {'mode': 'reduceRow', 'reduce': {'reducer': 'sum'}, 'alias': '合计', 'timeSeries': False}},
    {'id': 'sortBy', 'options': {'sort': [{'field': '合计', 'desc': True}]}}]
status_chart['options'] = {'xField': '状态码', 'orientation': 'horizontal', 'stacking': 'normal',
                           'fullHighlight': True, 'groupWidth': 0.7, 'barWidth': 0.95, 'showValue': 'auto',
                           'tooltip': {'mode': 'multi', 'sort': 'desc', 'hideZeros': True, 'maxHeight': 320, 'maxWidth': 900},
                           'legend': {'showLegend': False}}
status_chart['fieldConfig']['defaults'].update({'decimals': 0, 'min': 0, 'color': {'mode': 'fixed', 'fixedColor': 'red'}})
field(status_chart, '合计', {'custom.hideFrom': {'viz': True, 'legend': True, 'tooltip': False}})
field(status_chart, '暂无记录', {'color': {'mode': 'fixed', 'fixedColor': 'gray'}})
click_link(status_chart, '按这个状态码查看接口和请求列表',
           error_url(True, '${__data.fields["状态码"]:percentencode}'))
error_panels.append(status_chart)
error_panels.append(request_bars(panel(5, '错误出现时间 · 每分钟', 'timeseries', (14, 4, 10, 10),
                                      f'60 * sum by (status) (rate(http_requests_total{{{error_selected}}}[$__rate_interval]))',
                                      'suffix:次/分', '随顶部筛选变化；可缩小时间范围后再查逐条请求。', '{{status}}')))
error_panels[-1]['fieldConfig']['defaults']['color'] = {'mode': 'fixed', 'fixedColor': 'red'}
error_panels[-1]['fieldConfig']['defaults']['custom']['axisSoftMax'] = 1
error_table = table(6, '接口错误排行 · 点击接口或数量继续筛选', (0, 14, 24, 8),
                    f'sort_desc(label_replace(label_replace({error_rows},"role_key","$1","role","(.+)"),"service_key","$1","service","(.+)"))',
                    {'service': '服务', 'service_key': '服务标识', 'role': '角色', 'role_key': '角色标识', 'host': '主机', 'method': '方法', 'route': '接口', 'status': '状态码', 'Value': '错误数'},
                    '按错误次数降序。点击接口或错误数，定位到该角色、主机、方法、接口与状态码。')
focus = error_url(False, '${__data.fields["状态码"]:percentencode}', True)
for name in ['接口', '错误数']:
    field(error_table, name, {'links': [{'title': '查看这个接口的请求详情', 'url': focus + ('&viewPanel=8' if LOGS_ENABLED else ''), 'oneClick': True}]})
field(error_table, '方法', {'custom.width': 70})
field(error_table, '状态码', {'custom.width': 80, 'color': {'mode': 'fixed', 'fixedColor': 'red'}, 'custom.cellOptions': {'type': 'color-text'}})
field(error_table, '错误数', {'custom.width': 85})
field(error_table, '角色标识', {'custom.hidden': True})
field(error_table, '服务标识', {'custom.hidden': True})
error_panels.append(error_table)


def trace_log_url():
    return ('/d/http-request-details?from=${__from}&to=${__to}&${env:queryparam}'
            '&${service:queryparam}&${traffic:queryparam}&${business:queryparam}'
            '&var-trace_id=${__data.fields["traceId"]:percentencode}&viewPanel=12')






from http_logs import build_http_log_table


def request_log_table(pid, title, pos, slow=False, request_mode=False):
    return build_http_log_table(panel, table, field, LOKI, DS, pid, title, pos,
                                slow=slow, trace_url=trace_log_url(), request_mode=request_mode,
                                limit=LOG_LIMIT)


if LOGS_ENABLED:
    error_panels.append({'id': 7, 'title': '逐条请求与统计数量的区别', 'type': 'text',
                         'gridPos': {'x': 0, 'y': 22, 'w': 24, 'h': 3},
                         'options': {'mode': 'markdown', 'content':
                             '下面直接读取规范化 **http.server.completed** 单条完成事件，点击 **traceId** 查看关联日志。'
                             f'列表只包含采集后仍保留的日志（保留 3 天），最多 {LOG_LIMIT} 条响应；统计总数仍以 Prometheus 为准。'
                             '状态码和耗时来自同一条完成事件，不需要入站/完成关联。'}})
    error_panels.append(request_log_table(8, '逐条请求 · 点击 traceId 查看日志', (0, 25, 24, 12)))
save('http-errors', 'HTTP 错误分析', error_panels, 'business', error_vars,
     '从状态码排行下钻到接口，再按 traceId 查看已保留的请求日志；数量统计以 Prometheus 为准。')


# Slow requests: histogram counts include every HTTP status; log durations are ms.
slow_vars = variables(True)
slow_thresholds = [('50 毫秒', '0.05'), ('100 毫秒', '0.1'), ('250 毫秒', '0.25'),
                   ('500 毫秒', '0.5'), ('1 秒', '1'), ('2.5 秒', '2.5'), ('5 秒', '5'),
                   ('10 秒', '10'), ('30 秒', '30'), ('60 秒', '60'), ('120 秒', '120')]
slow_vars.append({'name': 'slow_seconds', 'label': '慢请求阈值（大于）', 'type': 'custom',
                  'query': ','.join(f'{label} : {value}' for label, value in slow_thresholds), 'multi': False,
                  'current': {'text': '1 秒', 'value': '1', 'selected': True},
                  'options': [{'text': label, 'value': value, 'selected': value == '1'} for label, value in slow_thresholds]})
slow_vars.append(variable('route', '接口', f'label_values(http_request_duration_seconds_count{{{http_app}}}, route)'))
slow_vars.append({'name': 'method', 'label': '方法', 'type': 'custom',
                  'query': 'GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS', 'multi': True,
                  'includeAll': True, 'allValue': '.*',
                  'current': {'text': ['All'], 'value': ['$__all'], 'selected': True}})
slow_selected = http_app + ',method=~"$method",route=~"$route"'
slow_total = slow_count(slow_selected) + ' or ' + zero(tgt)
duration_total = f'sum(increase(http_request_duration_seconds_count{{{slow_selected}}}[$__range]))'
slow_panels = [
    panel(1, '时段慢请求数', 'stat', (0, 0, 6, 4), slow_total,
          description='已完成请求中耗时严格大于顶部阈值的数量。按直方图桶边界统计，所有 HTTP 状态码均包含；采样估算，显示取整。'),
    panel(2, '慢请求占比', 'stat', (6, 0, 6, 4), f'100 * ({slow_total}) / ({duration_total} > 0)', 'percent',
          description='慢请求 / 同范围已完成请求。没有请求时留空。', no_value='无请求'),
    panel(3, '时段 P95 耗时', 'stat', (12, 0, 6, 4), p95(slow_selected, '$__range', ''), 's',
          description='同范围全部已完成请求的 P95，根据直方图估算；并非只计算慢请求。', no_value='无请求'),
    panel(4, '时段 P99 耗时', 'stat', (18, 0, 6, 4), p95(slow_selected, '$__range', '').replace('0.95', '0.99', 1), 's',
          description='同范围全部已完成请求的 P99，根据直方图估算。单次精确耗时见下面日志列表。', no_value='无请求'),
    request_bars(panel(5, '慢请求速率 · 次/分钟', 'timeseries', (0, 4, 12, 8),
                       '60 * ' + slow_count(slow_selected, window='$__rate_interval', by='role', rate=True),
                       'suffix:次/分', '严格大于所选阈值的请求速率，使用至少 5 分钟滑动均值，按角色堆叠。可拖选时间段继续排查。', '{{role}}')),
    latency_line(panel(6, '响应时间趋势 · 全部已完成请求', 'timeseries', (12, 4, 12, 8),
                       p95(slow_selected, by=''), 's', 'P95/P99 是直方图估算值；无请求窗口留空。', 'P95', '无请求'))]
for p in slow_panels[1:4]:
    p['fieldConfig']['defaults']['decimals'] = 2
slow_panels[4]['fieldConfig']['defaults']['custom']['axisSoftMax'] = 1
slow_panels[4]['targets'][0]['interval'] = '75s'  # $__rate_interval >= 4 * min step = 5m.
slow_panels[5]['targets'].append(query(p95(slow_selected, by='').replace('0.95', '0.99', 1), 'P99', ref='B'))
for name, color in [('P95', 'blue'), ('P99', 'orange')]:
    field(slow_panels[5], name, {'color': {'mode': 'fixed', 'fixedColor': color}})
slow_rows = slow_count(slow_selected, by='service,role,host,method,route') + ' > 0'
slow_table = table(7, '慢接口排行 · 点击接口或数量查看单次请求', (0, 12, 24, 9),
                   f'topk(30, label_replace(label_replace({slow_rows},"role_key","$1","role","(.+)"),"service_key","$1","service","(.+)"))',
                   {'service': '服务', 'service_key': '服务标识', 'role': '角色', 'role_key': '角色标识', 'host': '主机', 'method': '方法', 'route': '接口', 'Value': '慢请求数'},
                   '按慢请求次数降序，最多 30 行。点击接口或数量查看该主机、角色、方法和接口的逐条慢请求。数量为采样估算。')
for name in ['接口', '慢请求数']:
    field(slow_table, name, {'links': [{'title': '查看这个接口的慢请求详情',
                                       'url': slow_url(focus=True).replace('/d/slow-requests?', '/d/http-request-details?')
                                       if LOGS_ENABLED else slow_url(focus=True), 'oneClick': True}]})
field(slow_table, '角色标识', {'custom.hidden': True})
field(slow_table, '服务标识', {'custom.hidden': True})
field(slow_table, '方法', {'custom.width': 75})
field(slow_table, '慢请求数', {'custom.width': 160, 'color': {'mode': 'fixed', 'fixedColor': 'orange'},
                           'custom.cellOptions': {'type': 'gauge', 'mode': 'basic', 'valueDisplayMode': 'text'}})
slow_panels.append(slow_table)
if LOGS_ENABLED:
    slow_panels.append({'id': 8, 'title': '慢请求详情与统计口径', 'type': 'text',
                        'gridPos': {'x': 0, 'y': 21, 'w': 24, 'h': 3},
                        'options': {'mode': 'markdown', 'content':
                            '统计包含 **所有 HTTP 状态码**，仅统计已完成请求；阈值严格大于顶部所选值。'
                            f'下面取最近 **{LOG_LIMIT} 条慢响应**，在这些记录内按耗时降序，点击 **traceId** 查看关联日志。'
                            '日志保留 3 天；采集起点、保留范围、查询上限和跨时间边界会使明细与 Prometheus 估算数量不同。'}})
    slow_panels.append(request_log_table(9, '逐条慢请求 · 按耗时降序，点击 traceId 看日志', (0, 24, 24, 12), slow=True))
save('slow-requests', '慢请求分析', slow_panels, 'business', slow_vars,
     '按可选耗时阈值统计慢请求数量和占比，从接口排行下钻到单次耗时与 traceId 日志。')


if LOGS_ENABLED:
    request_vars = deepcopy(slow_vars)
    request_vars.append({'name': 'request_mode', 'label': '请求范围', 'type': 'custom',
                         'query': '全部完成请求 : all,仅慢请求 : slow', 'multi': False,
                         'current': {'text': '全部完成请求', 'value': 'all', 'selected': True},
                         'options': [{'text': '全部完成请求', 'value': 'all', 'selected': True},
                                     {'text': '仅慢请求', 'value': 'slow', 'selected': False}]})
    request_vars.append({'name': 'trace_id', 'label': '请求 traceId', 'type': 'textbox', 'hide': 2,
                         'query': '__no_trace_selected__',
                         'current': {'text': '__no_trace_selected__', 'value': '__no_trace_selected__', 'selected': True}, 'options': []})
    coverage_stream = ('{service=~"${service:pipe}",env=~"$env",host=~"$host",role=~"$role"}'
                       ' |= "http.server.completed" | json event="event", service_instance_id="service_instance_id"'
                       ' | __error__="" | event="http.server.completed" | service_instance_id!=""')
    coverage_expr = ('min(min_over_time(' + coverage_stream +
                     ' | label_format log_at_ms="{{__timestamp__ | unixEpochMillis}}"'
                     ' | unwrap log_at_ms | __error__="" [72h]))')
    coverage = panel(2, '规范 HTTP 日志的最早时间', 'stat', (0, 5, 12, 4), '', 'dateTimeAsIso',
                     '在所选结束时刻之前 72 小时内，当前环境、主机、角色最早仍保留的日志。早于此时间的历史指标不保证有日志。',
                     no_value='所选时刻前无日志')
    coverage['datasource'] = LOKI
    coverage['targets'] = [{'refId': 'A', 'datasource': LOKI, 'queryType': 'instant',
                            'editorMode': 'code', 'expr': coverage_expr}]
    coverage['fieldConfig']['defaults']['decimals'] = 0
    if LIGHT_MODE:
        coverage = {'id': 2, 'title': '日志保留与查询范围', 'type': 'text',
                    'gridPos': {'x': 0, 'y': 5, 'w': 12, 'h': 4},
                    'options': {'mode': 'markdown', 'content':
                        f'日志保留 **72 小时**；下方显示所选时段最近 **{LOG_LIMIT} 条**匹配请求。'
                        '缩小时间范围或选择接口，可以继续查看其他记录。\n\n'
                        '指标保存时间可能长于日志。采集前、已过期或应用未写出的日志无法补回。'}}
    request_panels = [
        {'id': 1, 'title': 'HTTP 请求日志详情', 'type': 'text',
         'gridPos': {'x': 0, 'y': 0, 'w': 24, 'h': 3},
         'options': {'mode': 'markdown', 'content':
             '当前查看 **${request_mode:text}**；慢请求模式的阈值为 **大于 ${slow_seconds:text}**。'
             '用顶部服务、分类、业务和接口筛选，日志保留 72 小时。\n\n'
             '只展示规范完成事件。点击 **traceId** 查关联日志；**OTel Trace ID** 是标准调用标识。'}},
        coverage,
        panel(3, '所选时段慢请求估算数', 'stat', (12, 5, 6, 4), slow_total,
              description='Prometheus 采样估算，历史保留范围通常大于日志；统计数量不代表对应日志一定仍存在。'),
        {'id': 4, 'title': '列表为空时', 'type': 'text',
         'gridPos': {'x': 18, 'y': 5, 'w': 6, 'h': 4},
         'options': {'mode': 'markdown', 'content':
             '检查**时间、服务和接口**；慢请求模式还需检查阈值。\n\n' +
             ('只显示采集后仍保留的日志，保留 72 小时。' if LIGHT_MODE
              else '日志明细从左侧覆盖起点开始，保留 72 小时。')}},
        request_log_table(9, '逐条请求 · 点击 traceId 查看原始日志', (0, 9, 24, 12), slow=True, request_mode=True),
        {'id': 13, 'title': '查看原始日志', 'type': 'text',
         'gridPos': {'x': 0, 'y': 21, 'w': 24, 'h': 2},
         'options': {'mode': 'markdown', 'content':
             '点击上方请求的 **traceId** 打开下方面板。只显示应用实际写出的日志；未记录的请求体、响应体或业务执行过程无法补出。'}},
        {'id': 12, 'title': '请求原始日志', 'type': 'logs', 'datasource': LOKI,
         'gridPos': {'x': 0, 'y': 23, 'w': 24, 'h': 13},
         'fieldConfig': {'defaults': {}, 'overrides': []},
         'description': ('按业务 traceId 精确查两个服务的关联日志，保留环境和时间；可展开字段。'
                         f'最多返回 {500 if LIGHT_MODE else 1000} 条，超出时缩小时间范围继续查询。'
                         '这不是 Trace 后端中的完整 span 调用树。'),
         'targets': [{'refId': 'A', 'datasource': LOKI, 'queryType': 'range', 'editorMode': 'code',
                      'direction': 'forward', 'maxLines': 500 if LIGHT_MODE else 1000,
                      'expr': '{service=~"huahuo-server|huahuo-agent",env=~"$env"} |= "$trace_id"'
                              ' | json | __error__="" | service_instance_id!="" | traceId!="" | traceId="$trace_id"'}],
         'options': {'showTime': True, 'showLabels': False, 'showCommonLabels': False,
                     'wrapLogMessage': True, 'prettifyLogMessage': True, 'enableLogDetails': True,
                     'dedupStrategy': 'none', 'sortOrder': 'Ascending'}}]
    for request_panel in request_panels:
        if request_panel['id'] != 1:
            request_panel['gridPos']['y'] -= 2
    save('http-request-details', 'HTTP 请求明细', request_panels, 'business', request_vars,
         '按接口、耗时和 traceId 查看已采集且仍保留的原始 HTTP 请求日志。')



from http_overview import build_http_overview
overview = build_http_overview(panel, table, field, query, semantic, thresholds, request_bars,
                               latency_line, click_link, DS, APP, HTTP_APP, TARGET,
                               request_count, zero, errors, p95, slow_count, slow_url,
                               error_url, slow_table, SCOPES, classify_business)

# Project entry points summarize business layers without mixing their counters.
agent_sel = APP + ',service="huahuo-agent"'
server_sel = APP + ',service="huahuo-server"'
fresh = f'(time() - task_backlog_observed_timestamp_seconds{{{server_sel}}} < 60)'
pending = f'sum(max by (queue) (task_pending{{{server_sel}}} and on (env,service,role,instance,queue) {fresh}))'
summary = [
    panel(24, 'Agent 运行终态 · 时段', 'stat', (0, 37, 6, 4),
          f'sum(increase(agent_runs_total{{{agent_sel}}}[$__range]))',
          description='原生运行终态的采样增量，不与 HTTP、执行段或模型调用相加。只含开始抓取后的增量。', no_value='无终态样本'),
    panel(25, 'Agent 首文本 P95', 'stat', (6, 37, 6, 4),
          f'histogram_quantile(0.95,sum by (le) (increase(agent_time_to_first_text_seconds_bucket{{{agent_sel}}}[$__range])))', 's',
          '初始运行开始到第一个非空文本，包含工具等待；非 HTTP/SSE 生命周期。', no_value='无首文本样本'),
    panel(26, 'Server 可调度任务', 'stat', (12, 37, 6, 4), pending,
          description='先剔除超过 60 秒未更新的快照，再对共享队列取 max，避免多 Scheduler 重复累加。', no_value='无新鲜队列快照'),
    panel(27, 'Server 任务终态 · 时段', 'stat', (18, 37, 6, 4),
          f'sum(increase(task_terminal_total{{{server_sel}}}[$__range]))',
          description='已提交的任务终态，不是处理尝试次数，也不是本时段创建任务的成功率。', no_value='无终态样本')]
summary[1]['fieldConfig']['defaults']['decimals'] = 2
for i,p in enumerate(summary):
    click_link(p, '查看业务层详情', ('/d/agent-runtime' if i < 2 else '/d/server-workloads') +
               '?from=${__from}&to=${__to}&${env:queryparam}&${host:queryparam}')
overview.extend([{'id': 23, 'title': '业务层概况 · 点击进入 Agent / Server', 'type': 'row',
                 'collapsed': False, 'gridPos': {'x': 0, 'y': 36, 'w': 24, 'h': 1}}] + summary)
save('monitoring-overview', '花火AI 服务总览', overview, 'business', variables() + [deepcopy(next(v for v in slow_vars if v['name'] == 'slow_seconds'))],
     '勾选 HTTP 类别与业务，通过两级环形图查看请求分布；请求量、QPS、QPM、状态码和延迟同步筛选。')


platform = [
    semantic(panel(1, 'Prometheus 抓取状态', 'stat', (0, 0, 6, 4), 'up{job="prometheus"}'), [(None, 'red'), (1, 'green')]),
    semantic(panel(2, '采集异常目标', 'stat', (6, 0, 6, 4), 'sum(1 - up)'), [(None, 'green'), (1, 'red')]),
    panel(3, '当前时间序列', 'stat', (12, 0, 6, 4), 'prometheus_tsdb_head_series{job="prometheus"}', 'none'),
    panel(4, 'Prometheus 内存', 'stat', (18, 0, 6, 4), 'process_resident_memory_bytes{job="prometheus"}', 'bytes')]
platform[0]['fieldConfig']['defaults']['mappings'] = [{'type': 'value', 'options': {'0': {'text': '异常', 'color': 'red'}, '1': {'text': '正常', 'color': 'green'}}}]
targets = table(5, '采集目标', (0, 4, 24, 8), 'up',
                {'job': '采集任务', 'role': '角色', 'instance': '地址', 'env': '环境', 'Value': '采集状态'},
                '仅展示已经配置的采集目标。', sort='job', descending=False)
field(targets, '采集状态', {'mappings': [{'type': 'value', 'options': {'0': {'text': '异常', 'color': 'red'}, '1': {'text': '正常', 'color': 'green'}}}],
                         'custom.cellOptions': {'type': 'color-text'}})
targets['fieldConfig']['defaults']['noValue'] = '—'
platform.append(targets)
platform.append(panel(6, '抓取耗时', 'timeseries', (0, 12, 12, 7), 'max by (instance) (scrape_duration_seconds)', 's', legend='{{instance}}'))
platform.append(panel(7, 'Prometheus CPU', 'timeseries', (12, 12, 12, 7),
                      'rate(process_cpu_seconds_total{job="prometheus"}[$__rate_interval])', 'cores', legend='Prometheus'))
if LOGS_ENABLED:
    platform.append(panel(8, '日志服务进程内存', 'timeseries', (0, 19, 12, 7),
                          'process_resident_memory_bytes{job="monitoring",service=~"loki|alloy"}', 'bytes',
                          '进程 RSS，包含共享映射页；与 Docker 按容器统计的内存口径不同。日志服务按负载使用中心机可用 CPU 和内存。', '{{service}}'))
    platform.append(panel(9, '日志接收速率', 'timeseries', (12, 19, 12, 7),
                          'sum(rate(loki_distributor_bytes_received_total{job="monitoring",service="loki"}[$__rate_interval]))', 'Bps',
                          '当前只采集本机四角色的业务容器；用于观察日志量随接入范围的变化。', '日志字节/秒'))
save('prometheus-health', '采集状态', platform, 'platform', description='用于检查监控采集链路和 Prometheus 自身资源。')

if RDS_LOGS_ENABLED:
    from database_dashboard import build_database_dashboard
    build_database_dashboard(save, panel, field, DS, LOKI, log_limit=LOG_LIMIT)

from agent_dashboard import build_agent_dashboard
from server_dashboard import build_server_dashboard
build_agent_dashboard(save, panel, table, field, query, DS, LOKI, variable)
build_server_dashboard(save, panel, table, field, query, DS, LOKI, variable)

print('Built 服务总览、HTTP 排查、Agent 运行与模型、Server 任务与依赖、平台采集'
      + ('、数据库慢查询.' if RDS_LOGS_ENABLED else '.'))
