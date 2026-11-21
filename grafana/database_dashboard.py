"""Provision the RDS slow-query dashboard without changing the application."""


def build_database_dashboard(save, panel, field, DS, LOKI, log_limit=500):
    """Use the dashboard builder's common appearance and provisioning helpers."""
    instance = 'pgm-2ze7o54d25m8kn53'
    database = 'huahuo'
    prom_selector = 'job="monitoring",service="rds-slow-logs",env=~"$env"'
    selector = ('{service="rds-slow-sql",env=~"$env",'
                f'rds_instance="{instance}",database="{database}"' + '}')
    logs = (selector + ' | json | __error__="" | probe="$probe"'
            ' | query_id=~"${query_id:raw}"')
    unwrapped = logs + ' | unwrap elapsed_ms | __error__=""'

    def loki_target(expr, ref='A', instant=False, legend='', limit=500):
        return {'refId': ref, 'datasource': LOKI, 'expr': expr,
                'queryType': 'instant' if instant else 'range',
                'editorMode': 'code', 'legendFormat': legend,
                'direction': 'backward', 'maxLines': limit}

    def loki_panel(pid, title, kind, pos, expr, unit='short', description='', no_value='暂无记录'):
        p = panel(pid, title, kind, pos, expr, unit=unit,
                  description=description, no_value=no_value)
        p['datasource'] = LOKI
        p['targets'] = [loki_target(expr, instant=kind in ('stat', 'table'))]
        return p

    variables = [
        {'name': 'env', 'label': '环境', 'type': 'query', 'datasource': DS,
         'query': {'query': 'label_values(up{job="monitoring",service="rds-slow-logs"},env)',
                   'refId': 'StandardVariableQuery'},
         'definition': 'label_values(up{job="monitoring",service="rds-slow-logs"},env)',
         'refresh': 1, 'multi': True, 'includeAll': True, 'allValue': '.*', 'sort': 1,
         'current': {'selected': True, 'text': ['test'], 'value': ['test']}, 'options': []},
        {'name': 'probe', 'label': '数据范围', 'type': 'custom', 'multi': False,
         'query': '业务慢SQL : false,验证探针 : true',
         'current': {'selected': True, 'text': '业务慢SQL', 'value': 'false'},
         'options': [{'text': '业务慢SQL', 'value': 'false', 'selected': True},
                     {'text': '验证探针', 'value': 'true', 'selected': False}]},
        {'name': 'query_id', 'label': 'SQL 标识（正则）', 'type': 'textbox',
         'query': '.*', 'current': {'selected': True, 'text': '.*', 'value': '.*'},
         'options': []},
    ]

    collector_state = panel(
        1, '慢日志同步状态', 'stat', (0, 0, 6, 4),
        f'up{{{prom_selector}}} * '
        f'(time() - rds_slow_logs_last_success_timestamp_seconds{{{prom_selector}}} < bool 180)',
        description='状态取所选时间范围的结束时刻：采集器可访问，且此前 3 分钟成功同步 RDS 慢日志。结束时刻早于本次接入时会无数据，请切换到最近 6 小时或将结束时间设为 now。RDS 明细本身可能有分钟级延迟。',
        no_value='所选时刻无状态')
    collector_state['fieldConfig']['defaults'].update({
        'color': {'mode': 'thresholds'},
        'thresholds': {'mode': 'absolute', 'steps': [{'color': 'red', 'value': None}, {'color': 'green', 'value': 1}]},
        'mappings': [{'type': 'value', 'options': {
            '0': {'text': '未同步', 'color': 'red'}, '1': {'text': '正常', 'color': 'green'}}}]})

    records = f'sum(count_over_time({logs}[$__range]))'
    unique = f'count(sum by (query_id) (count_over_time({logs}[$__range])))'
    maximum = f'max(max_over_time({unwrapped}[$__range]))'
    panels = [
        collector_state,
        loki_panel(2, '时段慢SQL记录数', 'stat', (6, 0, 6, 4), records + ' or vector(0)',
                   description='所选范围已采集的 RDS 慢日志记录数。API 可能聚合多次执行，记录数不等同于精确 SQL 执行次数。'),
        loki_panel(3, '涉及 SQL 摘要', 'stat', (12, 0, 6, 4), unique + ' or vector(0)',
                   description='按 RDS SQLHash / 采集器 query_id 区分的 SQL 摘要数。'),
        loki_panel(4, '最长记录耗时', 'stat', (18, 0, 6, 4), maximum, unit='ms',
                   description='当前筛选范围内，RDS 每条慢日志记录 QueryTimeMS 的最大值。', no_value='无慢SQL'),
        loki_panel(5, '慢SQL记录数 · 时间分布', 'timeseries', (0, 4, 12, 8),
                   f'sum(count_over_time({logs}[$__interval]))',
                   description='每根柱子统计对应采样区间的慢日志记录。拖选出现慢 SQL 的时间段可继续排查。'),
        loki_panel(6, '慢SQL耗时 · 区间最大值', 'timeseries', (12, 4, 12, 8),
                   f'max(max_over_time({unwrapped}[$__interval]))', unit='ms',
                   description='每个采样区间内，已采集慢日志记录的最大耗时；没有记录时留空。'),
    ]
    panels[3]['fieldConfig']['defaults']['decimals'] = 2
    panels[4]['fieldConfig']['defaults']['custom'].update({
        'drawStyle': 'bars', 'lineWidth': 0, 'fillOpacity': 85, 'barAlignment': 0,
        'axisSoftMax': 1})
    panels[4]['fieldConfig']['defaults']['decimals'] = 0
    panels[5]['fieldConfig']['defaults']['custom'].update({'showPoints': 'always', 'pointSize': 5})
    for p in panels[4:6]:
        p['interval'] = '1m'
        p['options']['legend']['showLegend'] = False

    # Keep one numeric series per SQL so Grafana can expose its parsed labels
    # directly, without pivoting multiple measures or parsing a composite key.
    rank_expr = f'topk(30, sum by (query_id, sql_summary) (sum_over_time({unwrapped}[$__range])))'
    rank = loki_panel(7, '慢SQL排行 · 按记录总耗时排序，点击查看详情', 'table', (0, 12, 24, 10), rank_expr,
                      description='按所选时间内已采集记录的耗时求和排序，最多 30 行。API 记录可能聚合执行，此值是记录耗时之和。点击 SQL 摘要、标识或耗时查看逐条记录；SQL 字面值和注释在入库前已脱敏。')
    columns = {'query_id': 'SQL 标识', 'sql_summary': 'SQL 摘要', 'Value #A': '记录总耗时'}
    rank['transformations'] = [
        {'id': 'labelsToFields', 'options': {'mode': 'columns'}},
        {'id': 'filterFieldsByName', 'options': {'include': {'names': list(columns)}}},
        {'id': 'organize', 'options': {'indexByName': {name: i for i, name in enumerate(columns)}, 'renameByName': columns}},
    ]
    rank['options'] = {'showHeader': True, 'cellHeight': 'md',
                       'sortBy': [{'displayName': '记录总耗时', 'desc': True}], 'footer': {'show': False}}
    rank['fieldConfig']['defaults'].update({'decimals': 0, 'custom': {'align': 'left', 'cellOptions': {'type': 'auto'}}})
    focus = ('/d/database-slow-sql?from=${__from}&to=${__to}&${env:queryparam}'
             '&${probe:queryparam}&var-query_id=${__data.fields["SQL 标识"]:percentencode}&viewPanel=8')
    for name in ['SQL 标识', 'SQL 摘要', '记录总耗时']:
        field(rank, name, {'links': [{'title': '查看该 SQL 的逐条慢日志', 'url': focus, 'oneClick': True}]})
    field(rank, 'SQL 标识', {'custom.width': 170})
    field(rank, '记录总耗时', {'unit': 'ms', 'decimals': 2, 'custom.width': 160})
    panels.append(rank)

    details = loki_panel(8, '逐条慢SQL · 按耗时降序', 'table', (0, 25, 24, 12), logs,
                         description=f'显示最近 {log_limit} 条已保留记录，并在这些记录内按耗时降序。数据库侧日志暂不包含 HTTP traceId 关联；SQL 参数字面值已脱敏。')
    details['targets'] = [loki_target(logs, limit=log_limit)]
    detail_columns = {'Time': '发生时间', 'query_id': 'SQL 标识', 'elapsed_ms': '耗时',
                      'sql': 'SQL（已脱敏）', 'execution_count': 'API 执行次数'}
    details['transformations'] = [
        {'id': 'extractFields', 'options': {'source': 'Line', 'format': 'json', 'replace': True, 'keepTime': True}},
        {'id': 'filterFieldsByName', 'options': {'include': {'names': list(detail_columns)}}},
        {'id': 'organize', 'options': {'indexByName': {name: i for i, name in enumerate(detail_columns)}, 'renameByName': detail_columns}},
    ]
    details['options'] = {'showHeader': True, 'cellHeight': 'md',
                          'sortBy': [{'displayName': '耗时', 'desc': True}], 'footer': {'show': False}}
    details['fieldConfig']['defaults'].update({'decimals': 0, 'noValue': '未记录',
                                              'custom': {'align': 'left', 'cellOptions': {'type': 'auto'}}})
    field(details, '发生时间', {'unit': 'dateTimeAsIso', 'custom.width': 195})
    field(details, 'SQL 标识', {'custom.width': 180})
    field(details, '耗时', {'unit': 'ms', 'decimals': 2, 'custom.width': 110})
    field(details, 'SQL（已脱敏）', {'custom.wrapText': True, 'custom.cellOptions': {'type': 'auto', 'wrapText': True}})
    field(details, 'API 执行次数', {'custom.width': 125})
    panels.append({'id': 9, 'title': '数据范围与查看方式', 'type': 'text',
                   'gridPos': {'x': 0, 'y': 22, 'w': 24, 'h': 3},
                   'options': {'mode': 'markdown', 'content':
                       '**RDS PostgreSQL / huahuo**：数据库记录耗时达到 **1 秒** 的 SQL，采集器每分钟同步，云端明细存在延迟。'
                       '默认仅显示 **业务慢SQL**，验证探针单独查看。没有符合条件的记录时，统计为 0、排行为空。'
                       f'排行使用所选范围已采集的全部记录；下方明细最多最近 {log_limit} 条，日志保留 72 小时。'
                       'SQL 字面值和注释已脱敏；当前尚未接入 HTTP traceId 与 SQL 的关联。'}})
    panels.append(details)
    save('database-slow-sql', '数据库慢查询', panels, 'business', variables,
         'RDS 数据库侧慢日志：采集状态、记录排行、精确记录耗时与脱敏 SQL；默认排除监控验证探针。')
