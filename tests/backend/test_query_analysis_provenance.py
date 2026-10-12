"""Pre-execution model guesses must never become executed-result evidence."""
import json

from app.providers import model
from app.providers.tool.base import ToolResult
from app.modules.runtime.agent import _tool_payload_for_model
from test_analytics import API, source


def test_prequery_value_guess_is_trace_only(client, administrator, source, monkeypatch):
    invented = '核验值=0；没有订单'
    monkeypatch.setattr(model, 'chat_for_run', lambda *a, **kw: {
        'content': json.dumps({'sql': 'SELECT COUNT(*) AS actual_count FROM sales', 'explanation': invented}),
        'usage': {},
    })
    response = client.post(API+'/query', headers=administrator, json={
        'datasourceId': source['id'], 'query': '实际有多少订单记录', 'mode': 'natural_language',
    })
    assert response.status_code == 200, response.text
    result = response.json()['data']
    assert result['data'] == [{'actual_count': 5}]
    assert result['modelUsage']['explanation'] == invented  # Diagnostic trace retains the original generation.
    assert invented not in result['analysis']
    packed = _tool_payload_for_model(ToolResult(status='success', data=result))
    assert invented not in json.dumps(packed, ensure_ascii=False)
    assert packed['data']['data'] == [{'actual_count': 5}]
