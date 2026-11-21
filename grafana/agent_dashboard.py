"""Grafana views for the Agent's shared telemetry contract.

All duration queries use seconds. A missing observation remains missing; the
only zero fallback below is a measured subset of a nonempty usage population.
"""


def build_agent_dashboard(save, panel, table, field, query, DS, LOKI, variable):
    base = ('job="application",service="huahuo-agent",'
            'exported_env=~"$env",host=~"$host"')
    runs = base + ',agent=~"${agent:regex}"'
    models = base + ',provider=~"${provider:regex}",model=~"${model:regex}"'
    tools = base + ',tool=~"${tool:regex}"'

    variables = [
        variable('env', '环境', 'label_values(application_info{job="application",service="huahuo-agent"},exported_env)', 'test'),
        variable('host', '主机', 'label_values(application_info{job="application",service="huahuo-agent",exported_env=~"$env"},host)'),
        variable('agent', 'Agent', f'label_values(agent_run_segments_total{{{base}}},agent)'),
        variable('provider', '模型提供商', f'label_values(model_requests_total{{{base}}},provider)'),
        variable('model', '模型', f'label_values(model_requests_total{{{base},provider=~"${{provider:regex}}"}},model)'),
        variable('tool', '工具', f'label_values(tool_calls_total{{{base}}},tool)'),
    ]

    def total(metric, selector, by=''):
        aggregate = f'sum by ({by})' if by else 'sum'
        return f'{aggregate}(increase({metric}{{{selector}}}[$__range]))'

    def quantile(metric, selector, value='0.95', window='$__range', by=''):
        group = 'le' + (',' + by if by else '')
        # A histogram with no observations yields NaN. Filter it instead of
        # rendering a fake zero or a NaN label in the stat or time-series.
        return (f'histogram_quantile({value},sum by ({group}) '
                f'(increase({metric}_bucket{{{selector}}}[{window}]))) >= 0')

    def stat(pid, title, pos, expr, unit='none', description='', decimals=0,
             no_value='无观测样本'):
        p = panel(pid, title, 'stat', pos, expr, unit=unit,
                  description=description, no_value=no_value)
        p['fieldConfig']['defaults']['decimals'] = decimals
        return p

    def line(pid, title, pos, expr, unit, description, legend=''):
        p = panel(pid, title, 'timeseries', pos, expr, unit=unit,
                  description=description, legend=legend, no_value='无观测样本')
        p['fieldConfig']['defaults']['decimals'] = 2
        p['fieldConfig']['defaults']['custom'].update({
            'showPoints': 'always', 'pointSize': 3, 'fillOpacity': 5})
        return p

    def quantile_lines(pid, title, pos, metric, selector, description):
        p = line(pid, title, pos, quantile(metric, selector, '0.50', '5m'),
                 's', description, 'P50')
        p['targets'] = [
            query(quantile(metric, selector, value, '5m'), name, ref=ref)
            for ref, name, value in [('A', 'P50', '0.50'), ('B', 'P95', '0.95'), ('C', 'P99', '0.99')]
        ]
        for name, color in [('P50', 'green'), ('P95', 'blue'), ('P99', 'orange')]:
            field(p, name, {'color': {'mode': 'fixed', 'fixedColor': color}})
        return p

    outcome_names = {
        'success': ('成功', 'green'), 'error': ('错误', 'red'),
        'abort': ('运行取消', 'orange'), 'limit': ('达到限制', 'orange'),
        'blocked': ('被阻止', 'orange'), 'suspend': ('暂停', 'blue'),
        'interrupted': ('中断', 'purple'), 'unknown': ('未知', 'gray'),
        'http_error': ('HTTP 错误', 'red'), 'business_error': ('业务错误', 'red'),
        'network_error': ('网络错误', 'red'), 'timeout': ('超时', 'orange'),
        'cancelled': ('调用取消', 'orange'), 'response_error': ('响应校验错误', 'red'),
    }

    def outcome_column(p):
        field(p, '结果', {
            'mappings': [{'type': 'value', 'options': {
                key: {'text': name, 'color': color}
                for key, (name, color) in outcome_names.items()}}],
            'custom.cellOptions': {'type': 'color-text'}, 'custom.width': 120})

    def rank_bar(pid, title, pos, expr, legend, description, unit='none'):
        p = panel(pid, title, 'bargauge', pos, expr, unit=unit,
                  description=description, legend=legend, no_value='无观测样本')
        p['options'] = {
            'orientation': 'horizontal', 'displayMode': 'basic', 'valueMode': 'color',
            'namePlacement': 'left', 'showUnfilled': False,
            'minVizHeight': 24, 'maxVizHeight': 48,
            'reduceOptions': {'calcs': ['lastNotNull'], 'values': False, 'fields': ''},
        }
        p['fieldConfig']['defaults'].update({'min': 0, 'decimals': 0 if unit == 'none' else 2})
        return p

    def row(pid, title, y):
        return {'id': pid, 'title': title, 'type': 'row', 'collapsed': False,
                'gridPos': {'x': 0, 'y': y, 'w': 24, 'h': 1}}

    segments_note = ('原生 AGENT_RUN 每个已结束执行段计一次，包含暂停、中断与恢复段；'
                     '计数按所选时段采样估算，执行段时间不含暂停等待。')
    terminal_note = ('仅 success/error/abort/limit/blocked 原生终态计为运行结束；'
                     '暂停和中断不是终态，HTTP reader 断开也不代表运行取消。'
                     'run/span 去重仅在本进程内有界保留 15 分钟、最多 4096 项，跨重启不构成审计账本。')
    inference_note = ('每个已结束原生 MODEL_INFERENCE span 计一次，不重复计算 generation/step；'
                      '同一 span 内的提供商 SDK HTTP 重试不会另计。')

    panels = [
        stat(1, '时段运行终态数', (0, 0, 4, 4), total('agent_runs_total', runs),
             description=terminal_note),
        stat(2, '时段执行段数', (4, 0, 4, 4), total('agent_run_segments_total', runs),
             description=segments_note),
        stat(3, '时段模型推理数', (8, 0, 4, 4), total('model_requests_total', models),
             description=inference_note),
        stat(4, '时段工具调用数', (12, 0, 4, 4), total('tool_calls_total', tools),
             description='仅原生 TOOL_CALL / MCP_TOOL_CALL 执行层；每个已结束工具 span 计一次。'),
        stat(5, '时段首文本 P95', (16, 0, 4, 4),
             quantile('agent_time_to_first_text_seconds', runs), 's',
             '初始原生运行开始到首个非空 text-delta，含首文本前工具等待；'
             '不是模型纯推理 TTFT。非流式、无文本、取消或恢复段不伪造样本。', 2),
        stat(6, '时段执行段 P95', (20, 0, 4, 4),
             quantile('agent_run_segment_duration_seconds', runs), 's',
             '所选时段已结束执行段的直方图 P95；不包含暂停等待，不能当作跨恢复全程耗时。', 2),
    ]

    terminal = rank_bar(7, '运行终态 · 数量与结果', (0, 4, 6, 8),
                        'sort_desc(' + total('agent_runs_total', runs, 'outcome') + ')',
                        '{{outcome}}', terminal_note)
    segment = rank_bar(8, '执行段 · 包含暂停与中断', (6, 4, 6, 8),
                       'sort_desc(' + total('agent_run_segments_total', runs, 'outcome') + ')',
                       '{{outcome}}', segments_note)
    for p in (terminal, segment):
        for key, (name, color) in outcome_names.items():
            field(p, key, {'displayName': name, 'color': {'mode': 'fixed', 'fixedColor': color}})
    panels.extend([terminal, segment,
                   quantile_lines(9, '首文本等待 · P50 / P95 / P99', (12, 4, 12, 8),
                                  'agent_time_to_first_text_seconds', runs,
                                  '5 分钟窗口的原生首文本等待分位数。包含首文本前工具等待；'
                                  '与 HTTP/SSE 整条响应结束耗时、单次模型推理耗时分别观测。无样本留空。'),
                   row(10, '模型推理与原始用量', 12)])

    usage_total = total('model_usage_reports_total', models)
    complete = total('model_usage_reports_total', models + ',availability="complete"')
    coverage = f'100 * (({complete}) or (0 * ({usage_total}))) / (({usage_total}) > 0)'
    panels.extend([
        stat(11, '时段模型推理 P95', (0, 13, 6, 4),
             quantile('model_request_duration_seconds', models), 's', inference_note, 2),
        stat(12, '已知输入 Token', (6, 13, 6, 4),
             total('model_tokens_total', models + ',direction="input"'),
             description='所选时段 inference 原始已知 input usage；不使用 SSE finish 累计量，缺失 usage 不补零、不估算费用。'),
        stat(13, '已知输出 Token', (12, 13, 6, 4),
             total('model_tokens_total', models + ',direction="output"'),
             description='所选时段 inference 原始已知 output usage；中止时没有原始 usage 的调用不合成零。'),
        stat(14, '原始 usage 完整率', (18, 13, 6, 4), coverage, 'percent',
             'complete / 全部 usage reports。complete 表示 input/output 都已知，partial 为只知一项，unknown 为均未知；'
             '未知用量不当作零 Token。没有报告时留空。', 2, '无用量报告'),
        line(15, '模型推理速率 · 5分钟均值', (0, 17, 12, 8),
             f'60 * sum by (provider,model) (rate(model_requests_total{{{models}}}[5m]))',
             'suffix:次/分', inference_note + '每个点为此前 5 分钟的每分钟平均速率。', '{{provider}} · {{model}}'),
        quantile_lines(16, '模型推理耗时 · P50 / P95 / P99', (12, 17, 12, 8),
                       'model_request_duration_seconds', models,
                       '所选提供商和模型的原生 inference 耗时；5 分钟直方图分位估算，包含当前选择的全部结果，无样本留空。'),
    ])

    model_rank = table(17, '模型推理明细 · 按结果计数', (0, 25, 12, 8),
                       'sort_desc(' + total('model_requests_total', models, 'provider,model,outcome') + ')',
                       {'provider': '提供商', 'model': '模型', 'outcome': '结果', 'Value': '推理数'},
                       inference_note + '只按模型维度筛选，不受 Agent 或工具筛选影响。')
    model_rank['fieldConfig']['defaults']['noValue'] = '无观测样本'
    outcome_column(model_rank)
    field(model_rank, '推理数', {'custom.width': 95})
    usage = rank_bar(18, '原始 usage 可用性 · 报告数', (12, 25, 12, 8),
                     'sort_desc(' + total('model_usage_reports_total', models, 'availability') + ')',
                     '{{availability}}',
                     '每次已结束 inference 记一条可用性报告；complete/partial/unknown 分别为完整/部分/未知。'
                     '缺失原始用量的中止调用归 unknown，不能据此推算 Token 或费用。')
    for key, name, color in [('complete', '完整', 'green'), ('partial', '部分', 'orange'), ('unknown', '未知', 'gray')]:
        field(usage, key, {'displayName': name, 'color': {'mode': 'fixed', 'fixedColor': color}})
    panels.extend([model_rank, usage, row(19, '工具执行与 Server 依赖', 33)])

    tool_rank = table(20, '工具调用排行 · 按结果计数', (0, 34, 12, 8),
                      'sort_desc(' + total('tool_calls_total', tools, 'tool,outcome') + ')',
                      {'tool': '工具', 'outcome': '结果', 'Value': '调用数'},
                      '原生 TOOL_CALL/MCP_TOOL_CALL 已结束 span；同一工具 span 不重复累计。工具筛选只影响工具图表。')
    tool_rank['fieldConfig']['defaults']['noValue'] = '无观测样本'
    outcome_column(tool_rank)
    field(tool_rank, '调用数', {'custom.width': 95})
    tool_latency = rank_bar(21, '工具耗时 P95 · 所选时段', (12, 34, 12, 8),
                            'sort_desc(' + quantile('tool_call_duration_seconds', tools, by='tool') + ')',
                            '{{tool}}', '按所选时段全部已结束工具调用的直方图 P95 降序；无调用的工具不填零。', 's')
    clients = table(22, 'Agent → Server 调用 · 按结果计数', (0, 42, 12, 8),
                    'sort_desc(' + total('http_client_requests_total', base, 'operation,outcome,status') + ')',
                    {'operation': '操作', 'outcome': '结果', 'status': 'HTTP 状态', 'Value': '调用数'},
                    '完整读取 body 并完成 envelope/schema 校验后计数。区分 HTTP、业务、网络、超时、取消和响应校验错误；'
                    'status=none 表示未收到响应头。此处仅随环境和主机筛选。')
    clients['fieldConfig']['defaults']['noValue'] = '无观测样本'
    outcome_column(clients)
    field(clients, 'HTTP 状态', {'custom.width': 105})
    field(clients, '调用数', {'custom.width': 95})
    client_latency = line(23, 'Agent → Server 耗时 P95 · 5分钟', (12, 42, 12, 8),
                          quantile('http_client_request_duration_seconds', base, window='5m', by='operation'),
                          's', '耗时包含完整 body 读取与响应校验，每个点为此前 5 分钟直方图 P95；无调用留空。', '{{operation}}')
    panels.extend([tool_rank, tool_latency, clients, client_latency,
                   row(24, '实例与统计范围', 50)])

    identities = table(25, '当前实例与观测能力', (0, 51, 24, 6), f'application_info{{{base}}}',
                       {'host': '主机', 'role': '角色', 'version': '应用版本',
                        'service_instance_id': '进程实例', 'ttft': '首文本观测'},
                       '当前 application_info 身份；版本和进程实例只在信息指标上，不是业务指标标签。'
                       '当前未上报蓝绿 slot，不能把进程 UUID 或 package 版本当作蓝绿发布状态。',
                       sort='host', descending=False)
    field(identities, '角色', {'mappings': [{'type': 'value', 'options': {'agent': {'text': 'Agent'}}}]})
    field(identities, '首文本观测', {'mappings': [{'type': 'value', 'options': {'first_text': {'text': '原生首文本'}}}]})
    field(identities, '进程实例', {'custom.width': 330})
    panels.append(identities)
    panels.append({'id': 26, 'title': '口径与筛选说明', 'type': 'text',
                   'gridPos': {'x': 0, 'y': 57, 'w': 24, 'h': 3},
                   'options': {'mode': 'markdown', 'content':
                       '环境、主机作用于全页；Agent 作用于运行和首文本，提供商/模型作用于模型，工具只作用于工具。'
                       '所有“时段”计数均由 Prometheus `increase` 采样估算；5 分钟趋势是滑动均值，分位数由直方图估算。'
                       '运行、HTTP/SSE 连接和模型推理分别统计；运行耗时不是跨暂停恢复的总墙钟时间。'
                       '尚无观测样本时留空，不将缺失用量或未结束调用填成 0。'
                       'Prometheus 首次采集前的调用只构成进程计数器基线，不能还原为时段新增；下方折叠区可查看进程累计值。'}})

    cumulative = []
    for pid, title, x, metric, selector, dimensions in [
        (28, '进程累计 · 原生运行终态', 0, 'agent_runs_total', runs,
         {'host': '主机', 'agent': 'Agent', 'outcome': '结果', 'Value': '累计运行数'}),
        (29, '进程累计 · 模型推理', 8, 'model_requests_total', models,
         {'host': '主机', 'provider': '提供商', 'model': '模型', 'outcome': '结果', 'Value': '累计推理数'}),
        (30, '进程累计 · 工具调用', 16, 'tool_calls_total', tools,
         {'host': '主机', 'tool': '工具', 'outcome': '结果', 'Value': '累计调用数'}),
    ]:
        p = table(pid, title, (x, 61, 8, 8), f'{metric}{{{selector}}}', dimensions,
                  '所选结束时刻的进程计数器值，含 Prometheus 首次采集前的基线；'
                  '进程重启后会重置，不是顶部时间范围内的新增调用数，也不是跨重启账本。')
        p['fieldConfig']['defaults']['noValue'] = '无观测样本'
        outcome_column(p)
        cumulative.append(p)
    panels.append({'id': 27, 'title': '进程累计基线（按需展开）', 'type': 'row', 'collapsed': True,
                   'gridPos': {'x': 0, 'y': 60, 'w': 24, 'h': 1}, 'panels': cumulative})

    save('agent-runtime', 'Agent 运行与模型', panels, 'business', variables,
         '按统一观测规范查看 Agent 原生终态、执行段、首文本、模型推理与已知 Token、工具和 Server 依赖。')
