"""Server work, dependency and stream panels for the unified telemetry contract."""


def build_server_dashboard(save, panel, table, field, query, DS, LOKI, variable):
    app = 'job="application",service="huahuo-server",exported_env=~"$env",host=~"$host"'
    variables = [
        variable('env', '环境', 'label_values(application_info{job="application",service="huahuo-server"}, exported_env)', 'test'),
        variable('host', '主机', 'label_values(application_info{job="application",service="huahuo-server",exported_env=~"$env"}, host)'),
    ]

    def row(pid, title, y, collapsed=False, children=None):
        p = {'id': pid, 'type': 'row', 'title': title, 'collapsed': collapsed,
             'gridPos': {'x': 0, 'y': y, 'w': 24, 'h': 1}}
        if collapsed:
            p['panels'] = children or []
        return p

    def count(metric, groups='', extra=''):
        by = f' by ({groups})' if groups else ''
        return f'sum{by}(increase({metric}{{{app}{extra}}}[$__range]))'

    def quantile(metric, groups, extra=''):
        return (f'histogram_quantile(0.95, sum by (le,{groups}) '
                f'(increase({metric}_bucket{{{app}{extra}}}[$__range])))')

    def merged(pid, title, pos, expressions, identity, measures, description):
        columns = {**identity, **{f'Value #{ref}': label for ref, label, _expr in measures}}
        p = table(pid, title, pos, expressions[0], columns, description=description)
        p['targets'] = [query(expr, instant=True, table=True, ref=ref)
                        for (ref, _label, _expr), expr in zip(measures, expressions)]
        p['transformations'] = [
            {'id': 'merge', 'options': {}},
            {'id': 'filterFieldsByName', 'options': {'include': {'names': list(columns)}}},
            {'id': 'organize', 'options': {'excludeByName': {},
                'indexByName': {name: i for i, name in enumerate(columns)}, 'renameByName': columns}},
        ]
        p['fieldConfig']['defaults']['noValue'] = '未上报'
        return p

    scheduler = app + ',role="scheduler"'
    fresh = (f'(time() - task_backlog_observed_timestamp_seconds{{{scheduler}}} < 60)')

    def backlog(metric):
        return (f'max by (exported_env,queue) ({metric}{{{scheduler}}} '
                f'and on (job,instance,exported_env,service,role,queue) {fresh})')

    pending = backlog('task_pending')
    oldest = backlog('task_oldest_waiting_seconds')
    snapshot_age = (f'max by (exported_env,queue) '
                    f'(time() - task_backlog_observed_timestamp_seconds{{{scheduler}}})')
    panels = [
        panel(1, '待处理任务 · 新鲜快照', 'stat', (0, 0, 6, 4), f'sum({pending})',
              description='到期可调度的 queued/retry_scheduled；排除运行中、外部等待与未来重试。先排除超过60秒的快照，同一共享队列多Scheduler取max后才汇总队列。', no_value='无新鲜快照'),
        panel(2, '最老可执行任务等待', 'stat', (6, 0, 6, 4), f'max({oldest})', 's',
              '从本次 available_at 算起；空队列为0，过期或未上报的快照留空。', no_value='无新鲜快照'),
        panel(3, '时段任务终态变更', 'stat', (12, 0, 6, 4), count('task_terminal_total'),
              description='已提交的 succeeded/failed/cancelled 终态；不同于处理尝试数，也不是所选时段创建任务的成功率。', no_value='尚无终态样本'),
        panel(4, '时段已提交处理尝试', 'stat', (18, 0, 6, 4), count('task_processing_attempts_total'),
              description='finish/fail事务提交后的success/failure尝试。重试可产生多次尝试；延后继续、回滚和未知外部结果不伪造完成。', no_value='尚无尝试样本'),
    ]
    queue_measures = [('A', '待处理数', pending), ('B', '最老等待', oldest), ('C', '最旧快照年龄', snapshot_age)]
    queue = merged(5, '任务队列 · 数量、等待与快照新鲜度', (0, 4, 24, 7),
                   [x[2] for x in queue_measures], {'exported_env': '环境', 'queue': '队列'}, queue_measures,
                   '共享PostgreSQL队列按环境/队列取max，避免多Scheduler重复累计。数量和等待仅采用60秒内成功快照；快照年龄显示最旧副本，便于识别停滞。全部过期时数量留空，不填0。')
    field(queue, '最老等待', {'unit': 's', 'decimals': 1, 'noValue': '无新鲜快照'})
    field(queue, '待处理数', {'noValue': '无新鲜快照'})
    field(queue, '最旧快照年龄', {'unit': 's', 'decimals': 1,
        'color': {'mode': 'thresholds'}, 'thresholds': {'mode': 'absolute', 'steps': [
            {'value': None, 'color': 'green'}, {'value': 60, 'color': 'red'}]},
        'custom.cellOptions': {'type': 'color-text'}})
    field(queue, '队列', {'mappings': [{'type': 'value', 'options': {
        'content': {'text': '内容处理'}, 'ai': {'text': 'AI'},
        'commercial': {'text': '商业事务'}, 'maintenance': {'text': '维护'}}}]})
    panels.append(queue)

    terminal = panel(6, '任务终态速率 · 5分钟均值', 'timeseries', (0, 11, 12, 8),
                     f'60 * sum by (state) (rate(task_terminal_total{{{app}}}[5m]))', 'suffix:次/分',
                     '按已提交终态统计；每个点为此前5分钟的平均每分钟速率。进程内观测不是持久账本。', '{{state}}', '尚无终态样本')
    terminal['fieldConfig']['defaults']['custom'].update({
        'drawStyle': 'bars', 'lineWidth': 0, 'fillOpacity': 75,
        'stacking': {'mode': 'normal', 'group': 'terminal'}})
    for state, title, color in [('succeeded', '成功终态', 'green'), ('failed', '失败终态', 'red'), ('cancelled', '取消终态', 'gray')]:
        field(terminal, state, {'displayName': title, 'color': {'mode': 'fixed', 'fixedColor': color}})
    panels.append(terminal)
    attempts = table(7, '处理尝试 · 按任务类型与结果', (12, 11, 12, 8),
                     count('task_processing_attempts_total', 'kind,result'),
                     {'kind': '任务类型', 'result': '尝试结果', 'Value': '时段尝试数'},
                     '处理尝试与唯一任务终态分别展示；一次任务可经历失败重试后成功。没有序列时显示未上报。')
    field(attempts, '尝试结果', {'mappings': [{'type': 'value', 'options': {
        'success': {'text': '成功', 'color': 'green'}, 'failure': {'text': '失败', 'color': 'red'}}}],
        'custom.cellOptions': {'type': 'color-text'}})
    panels.append(attempts)

    groups = 'exported_env,dependency,operation'
    eligible = count('http_client_requests_total', groups, ',outcome!="cancelled"')
    failures = count('http_client_requests_total', groups, ',outcome=~"http_error|business_error|network_error|timeout|response_error"')
    ratio = f'100 * (({failures}) or on ({groups}) (0 * ({eligible}))) / (({eligible}) > 0)'
    dependency_measures = [
        ('A', '时段客户端尝试', count('http_client_requests_total', groups)),
        ('B', '故障占比 · 排除主动取消', ratio),
        ('C', '尝试耗时 P95', quantile('http_client_request_duration_seconds', groups)),
        ('D', '主动取消', count('http_client_requests_total', groups, ',outcome="cancelled"')),
    ]
    panels.append(row(8, '依赖调用 · 包含完整响应消费与业务校验', 19))
    dependencies = merged(9, '依赖与操作 · 调用量、故障和耗时', (0, 20, 24, 9),
                          [x[2] for x in dependency_measures],
                          {'exported_env': '环境', 'dependency': '依赖', 'operation': '操作'}, dependency_measures,
                          '每次客户端尝试各一次。2xx响应的业务信封失败仍为business_error；故障包含HTTP、业务、网络、超时与响应校验，分母排除主动cancelled。P95包含所有尝试的完整消费耗时。无流量或缺失数据留空；未插桩的SDK调用不能据此推断。')
    field(dependencies, '故障占比 · 排除主动取消', {'unit': 'percent', 'decimals': 2, 'noValue': '无有效尝试'})
    field(dependencies, '尝试耗时 P95', {'unit': 's', 'decimals': 3})
    field(dependencies, '依赖', {'mappings': [{'type': 'value', 'options': {
        'document_parser': {'text': '文档解析'}, 'embedding': {'text': '向量嵌入'},
        'rerank': {'text': '重排'}, 'processing_node': {'text': '计算节点'}, 'other': {'text': '其他'}}}]})
    panels.append(dependencies)

    stage = panel(11, 'RAG 阶段 P95 · 所选时段', 'bargauge', (0, 30, 10, 8),
                  quantile('rag_stage_duration_seconds', 'stage'), 's',
                  '各阶段实际耗时分别合并直方图后估算P95，不能相加当作端到端P95。没有观测样本留空。', '{{stage}}', '尚无阶段样本')
    stage['options'] = {'orientation': 'horizontal', 'displayMode': 'basic', 'showUnfilled': False,
                        'reduceOptions': {'calcs': ['lastNotNull'], 'values': False, 'fields': ''}}
    rag_groups = 'operation,outcome'
    rag_measures = [('A', '时段操作数', count('rag_operations_total', rag_groups)),
                    ('B', '操作耗时 P95', quantile('rag_operation_duration_seconds', rag_groups))]
    rag = merged(12, 'RAG 操作 · 查询与嵌入尝试分开', (10, 30, 14, 8),
                 [x[2] for x in rag_measures], {'operation': '操作', 'outcome': '结果'}, rag_measures,
                 'note_query/file_query/text_query为查询；embedding_attempt为内部嵌入尝试，两层不能相加当用户检索数。')
    field(rag, '操作耗时 P95', {'unit': 's', 'decimals': 3})
    fallback = table(13, '重排降级 · 所选时段', (0, 38, 24, 5), count('rag_fallbacks_total', 'reason'),
                     {'reason': '降级原因', 'Value': '降级次数'}, '每次查询发生rerank降级仅记一次；没有样本不伪造零。')
    panels.append(row(10, 'RAG 检索与重排（按需展开）', 29, True, [stage, rag, fallback]))

    stream_groups = 'kind,outcome'
    stream_measures = [('A', '已结束会话', count('stream_sessions_total', stream_groups)),
                       ('B', '生命周期 P95', quantile('stream_duration_seconds', stream_groups))]
    streams = merged(15, '状态流 · 语义终态与生命周期', (0, 31, 14, 8),
                     [x[2] for x in stream_measures], {'kind': '状态流类型', 'outcome': '关闭结果'}, stream_measures,
                     '仅Server状态流：node_status/document_trial/state。HTTP200下仍可error/cancelled；不能把HTTP成功率当作业务成功率，亦不同于Agent模型输出流。')
    field(streams, '生命周期 P95', {'unit': 's', 'decimals': 3})
    first_event = panel(16, '状态首事件等待 P95', 'bargauge', (14, 31, 10, 8),
                        quantile('stream_first_event_seconds', 'kind'), 's',
                        '第一条状态数据被传输层接收的等待；排除心跳，不代表客户端ACK，也不是LLM首文本。', '{{kind}}', '尚无首事件样本')
    first_event['options'] = {'orientation': 'horizontal', 'displayMode': 'basic', 'showUnfilled': False,
                              'reduceOptions': {'calcs': ['lastNotNull'], 'values': False, 'fields': ''}}
    panels.append(row(14, '状态流（按需展开）', 30, True, [streams, first_event]))

    identity = table(18, 'Server 进程身份', (0, 32, 24, 7), f'application_info{{{app}}}',
                     {'exported_env': '环境', 'role': '角色', 'host': '主机', 'instance': '抓取地址',
                      'version': '上报版本', 'service_instance_id': '进程实例'},
                     'application_info确认上报身份，不代表依赖健康。version可能是package版本，不保证是提交SHA；当前没有蓝绿槽位标签，不据此推断蓝绿发布。',
                     sort='role', descending=False)
    panels.append(row(17, '进程身份（按需展开）', 31, True, [identity]))
    save('server-workloads', 'Server 任务与依赖', panels, 'business', variables,
         '按统一观测契约查看任务队列、处理尝试、任务终态、依赖调用、RAG与状态流；无样本保留为空。')
