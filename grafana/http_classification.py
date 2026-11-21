"""HTTP contract v1 filters and route-derived business navigation.

These labels are created in PromQL at query time. No source metric, application
code, or historical series is rewritten.
"""

import json


SCOPES = (
    {'key': 'public', 'title': '外部 API', 'pattern': 'public', 'color': 'blue'},
    {'key': 'internal', 'title': '内部 API', 'pattern': 'internal', 'color': 'purple'},
    {'key': 'admin', 'title': '管理端', 'pattern': 'admin', 'color': 'orange'},
    {'key': 'other', 'title': '其他 HTTP', 'pattern': 'other', 'color': 'gray'},
)

BUSINESS_NAMES = {
    'gateway': '网关鉴权',
    'auth': '账号认证',
    'me': '用户信息',
    'configuration': '系统配置',
    'document-trials': '文档调试',
    'processing-nodes': '处理节点',
    'document-parser': '文档解析',
    'login': '管理端登录',
    'logout': '管理端登出',
}


def scope_variable(DS=None):
    """Use :pipe when interpolating the selected regular-expression values."""
    return {
        'name': 'traffic', 'label': '请求分类', 'type': 'custom',
        'query': ','.join(f"{scope['title']} : {scope['pattern']}" for scope in SCOPES),
        'multi': True, 'includeAll': True, 'allValue': 'public|internal|admin|other',
        'current': {'selected': True, 'text': ['All'], 'value': ['$__all']},
        'options': [{'text': scope['title'], 'value': scope['pattern'], 'selected': False}
                    for scope in SCOPES],
    }


def _label_replace(expr, destination, replacement, source, regex):
    arguments = ','.join(json.dumps(value, ensure_ascii=False)
                         for value in (destination, replacement, source, regex))
    return f'label_replace(({expr}),{arguments})'


def classify_business(expr):
    """Attach business and business_pattern labels to a route-labelled vector.

    Unknown versioned modules keep their module name and appear automatically.
    Routes outside that structure retain their original route as a fallback.
    business_pattern intentionally stays a regex; display-name translations do
    not change the pattern used to filter the original historical metrics.
    """
    result = _label_replace(expr, 'business', '$1', 'route', '(.*)')
    result = _label_replace(result, 'business_pattern', '$1', 'route', '(.*)')
    module_route = '/(api|internal|admin/api)/v[0-9]+/([^/]+)(/.*)?'
    result = _label_replace(result, 'business', '$2', 'route', module_route)
    result = _label_replace(result, 'business_pattern',
                            '/(api|internal|admin/api)/v[0-9]+/$2(/.*)?',
                            'route', module_route)
    pages = '/admin(/assets/.*)?'
    result = _label_replace(result, 'business', '管理页面与资源', 'route', pages)
    result = _label_replace(result, 'business_pattern', pages, 'route', pages)
    result = _label_replace(result, 'business', '未匹配路径', 'route', 'unmatched')
    for title, pattern in [
        ('Agent 对话与运行', '/api/agents(/.*)?'),
        ('Agent 登录认证', '/(studio-auth|api/auth)(/.*)?'),
        ('其他 HTTP', '/other'),
    ]:
        result = _label_replace(result, 'business', title, 'route', pattern)
        result = _label_replace(result, 'business_pattern', pattern, 'route', pattern)
    for name, title in BUSINESS_NAMES.items():
        result = _label_replace(result, 'business', title, 'business', name)
    return result


def business_variable(APP, DS):
    """List observed business modules inside the selected request categories."""
    series = ('count_over_time(http_requests_total{' + APP +
              ',traffic=~"${traffic:pipe}"}[$__range])')
    expression = 'count by (business,business_pattern) (' + classify_business(series) + ')'
    definition = 'query_result(' + expression + ')'
    return {
        'name': 'business', 'label': '业务分组', 'type': 'query', 'datasource': DS,
        'query': {'query': definition, 'refId': 'StandardVariableQuery'},
        'definition': definition,
        'regex': '/business="(?<text>[^"]+)",?\\s*business_pattern="(?<value>[^"]+)"/',
        'refresh': 2, 'multi': True, 'includeAll': True, 'allValue': '.*', 'sort': 1,
        'current': {'selected': True, 'text': ['All'], 'value': ['$__all']},
        'options': [],
    }
