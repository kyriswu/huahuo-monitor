"""Request details from contract-v1 completion events, without log joins."""


def build_http_log_table(panel, table, field, LOKI, DS, pid, title, pos,
                         slow=False, trace_url='', request_mode=False, limit=500):
    stream = ('{service=~"${service:pipe}",env=~"$env",'
              'host=~"$host",role=~"$role"}')
    expression = (
        stream + ' |= "http.server.completed"'
        ' | json event="event", service_instance_id="service_instance_id",'
        ' traceId="traceId", trace_id="trace_id", span_id="span_id",'
        ' method="method", route="route", traffic="traffic", outcome="outcome",'
        ' status="statusCode", elapsed_ms="durationMs"'
        ' | __error__="" | event="http.server.completed" | outcome!="aborted"'
        ' | service_instance_id!="" | traceId!="" | status!="" | elapsed_ms!=""'
        ' | traffic=~"${traffic:pipe}" | route=~"${business:pipe}"'
        ' | method=~"$method" | route=~"$route"'
        ' | label_format elapsed="{{.elapsed_ms}}ms" | elapsed >= 0s | __error__=""')
    if request_mode:
        expression += (' | label_format selected_mode="${request_mode:raw}"'
                       ' | (selected_mode="all" or (selected_mode="slow" and elapsed > ${slow_seconds}s))'
                       ' | __error__=""')
    elif slow:
        expression += ' | elapsed > ${slow_seconds}s | __error__=""'
    else:
        expression += ' | status=~"$status"'
    expression += (
        ' | line_format `{"completed_at":{{__timestamp__ | unixEpochMillis}},'
        '"service":{{printf "%q" .service}},"role":{{printf "%q" .role}},'
        '"method":{{printf "%q" .method}},"route":{{printf "%q" .route}},'
        '"status":{{.status}},"elapsed_ms":{{.elapsed_ms}},'
        '"traffic":{{printf "%q" .traffic}},"outcome":{{printf "%q" .outcome}},'
        '"traceId":{{printf "%q" .traceId}},"trace_id":{{printf "%q" .trace_id}},'
        '"span_id":{{printf "%q" .span_id}},'
        '"service_instance_id":{{printf "%q" .service_instance_id}}}`')
    columns = {'completed_at': '发生时间', 'service': '服务', 'role': '角色',
               'method': '方法', 'route': '接口', 'status': '状态码',
               'elapsed_ms': '耗时', 'traffic': '请求分类', 'outcome': 'HTTP 结果',
               'traceId': 'traceId', 'trace_id': 'OTel Trace ID',
               'span_id': 'Span ID', 'service_instance_id': '进程实例'}
    p = table(pid, title, pos, '__LOKI__', columns,
              description=('直接读取最新 http.server.completed 完成事件，不关联入站记录。'
                           f'中断连接不混入完成请求。最近最多 {limit} 条，日志保留 72 小时；'
                           'HTTP 耗时包含流连接寿命。traceId 是业务关联号，trace_id/span_id 是 OTel 标识。'))
    p['datasource'] = LOKI
    p['targets'] = [{'refId': 'A', 'datasource': LOKI, 'expr': expression,
                     'queryType': 'range', 'editorMode': 'code',
                     'direction': 'backward', 'maxLines': limit}]
    p['transformations'] = [
        {'id': 'extractFields', 'options': {'source': 'Line', 'format': 'json', 'replace': True, 'keepTime': False}},
        {'id': 'merge', 'options': {}},
        {'id': 'convertFieldType', 'options': {'conversions': [
            {'targetField': name, 'destinationType': 'number'}
            for name in ['completed_at', 'status', 'elapsed_ms']]}},
        {'id': 'filterFieldsByName', 'options': {'include': {'names': list(columns)}}},
        {'id': 'organize', 'options': {'excludeByName': {},
            'indexByName': {k: i for i, k in enumerate(columns)}, 'renameByName': columns}},
    ]
    p['options']['sortBy'] = [{'displayName': '耗时' if slow and not request_mode else '发生时间', 'desc': True}]
    p['fieldConfig']['defaults']['noValue'] = '当前范围没有匹配的完成事件'
    field(p, '发生时间', {'unit': 'dateTimeAsIso', 'custom.width': 190})
    field(p, '角色', {'custom.width': 95})
    field(p, '方法', {'custom.width': 65})
    field(p, '状态码', {'custom.width': 75})
    field(p, '耗时', {'unit': 'ms', 'decimals': 2, 'custom.width': 95})
    field(p, '请求分类', {'mappings': [{'type': 'value', 'options': {
        'public': {'text': '外部 API'}, 'internal': {'text': '内部 API'},
        'admin': {'text': '管理端'}, 'other': {'text': '其他 HTTP'}}}], 'custom.width': 100})
    field(p, 'HTTP 结果', {'custom.hidden': True})
    field(p, 'Span ID', {'custom.hidden': True})
    field(p, '进程实例', {'custom.hidden': True})
    field(p, 'traceId', {'custom.width': 280, 'links': [
        {'title': '查看此业务关联号的原始日志', 'url': trace_url, 'oneClick': True}]})
    field(p, 'OTel Trace ID', {'custom.width': 270})
    return p
