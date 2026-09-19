from fastapi.testclient import TestClient
import pytest
from demo.web import app, datasets, workflows
from demo.planning_workspace import state_for, statistics, evaluate


@pytest.fixture
def workspace():
    with TestClient(app) as client:
        data = client.post('/sample?profile=boundary').json()
        key = data['dataset_id']
        yield client, key, data
        datasets.pop(key, None)
        workflows.pop(key, None)


def make_order(client, key, sku, token='new'):
    response = client.post(f'/planning/{key}/requests', json={'token': token,
        'lines': [{'sku': sku, 'quantity': 10, 'due': '2026-09-18'}]})
    assert response.status_code == 200, response.text
    return response.json()


def test_pending_feedback_partial_receive_and_idempotency(workspace):
    c, key, data = workspace
    sku = data['products'][0]['sku']
    stock = datasets[key][0].products[sku].available_stock
    order = make_order(c, key, sku)
    assert make_order(c, key, sku)['id'] == order['id']
    assert c.post(f'/planning/{key}/requests', json={'token': 'duplicate', 'lines': [{'sku': sku, 'quantity': 5, 'due': '2026-09-18'}]}).status_code == 409
    url = f'/planning/{key}/requests/{order["id"]}'
    c.post(url, json={'action': 'submit'})
    result = c.get(f'/planning/{key}').json()
    assert next(p for p in result['statistics']['products'] if p['sku'] == sku)['incoming'] == 0
    assert c.post(url, json={'action': 'feedback', 'lines': [{'sku': sku, 'quantity': 8, 'due': '2026-09-19'}]}).status_code == 200
    assert c.post(url, json={'action': 'feedback', 'lines': [{'sku': sku, 'quantity': 8, 'due': '2026-09-19'}]}).status_code == 409
    payload = {'action': 'receive', 'token': 'receipt-1', 'lines': [{'sku': sku, 'quantity': 3, 'due': data['as_of']}]}
    assert c.post(url, json=payload).json()['status'] == 'partial'
    assert c.post(url, json=payload).status_code == 200
    assert datasets[key][0].products[sku].available_stock == stock+3
    payload['token'] = 'receipt-2'
    payload['lines'][0]['quantity'] = 6
    assert c.post(url, json=payload).status_code == 422
    payload['lines'][0]['quantity'] = 5
    assert c.post(url, json=payload).json()['status'] == 'completed'
    assert datasets[key][0].products[sku].available_stock == stock+8
    events = workflows[key]['events']
    assert [e['type'] for e in events if 'type' in e] == ['receive', 'receive', 'feedback', 'submit', 'create']
    assert events[0]['items'][0]['quantity'] == 5
    assert events[1]['items'][0]['quantity'] == 3
    assert datasets[key][0].products[sku].name in events[0]['text']
    assert order['id'] not in events[0]['text']


def test_order_changes_reuse_demand_forecast(workspace, monkeypatch):
    c, key, data = workspace
    ops = datasets[key][0]
    sku = data['products'][0]['sku']
    state = state_for(workflows, key)
    state.update(setup_complete=True, forecast_revision=state['revision'])
    state['forecasts'][sku] = {'sku': sku, 'status': 'ready', 'revision': state['revision'],
        'lead_days': 7, 'target_stock': ops.products[sku].available_stock+100,
        'quantity': 100, 'due': data['as_of'],
        'daily': [{'date': data['as_of'], 'demand': 5, 'stock': 0}]}
    async def unexpected(*args):
        pytest.fail('Order actions must not rerun forecasting')
    monkeypatch.setattr('demo.planning_workspace.evaluate', unexpected)
    initial = state['revision']
    order = make_order(c, key, sku)
    url = f'/planning/{key}/requests/{order["id"]}'
    assert c.post(url, json={'action': 'submit'}).status_code == 200
    assert state['revision'] == initial
    assert c.post(url, json={'action': 'cancel'}).status_code == 200
    assert state['revision'] == initial
    order = make_order(c, key, sku, token='second')
    url = f'/planning/{key}/requests/{order["id"]}'
    c.post(url, json={'action': 'submit'})
    assert c.post(url, json={'action': 'feedback', 'lines': [
        {'sku': sku, 'quantity': 8, 'due': data['as_of']}]}).status_code == 200
    assert state['forecasts'][sku]['quantity'] == 92
    assert c.post(url, json={'action': 'receive', 'token': 'partial', 'lines': [
        {'sku': sku, 'quantity': 3, 'due': data['as_of']}]}).status_code == 200
    assert state['forecasts'][sku]['quantity'] == 92
    assert state['forecasts'][sku]['incoming_counted'] == 5
    assert c.post(url, json={'action': 'cancel'}).status_code == 200
    assert state['forecasts'][sku]['quantity'] == 97
    assert state['forecast_revision'] == state['revision']
    assert not state.get('calculation') or state['calculation']['status'] != 'running'


def test_missing_inventory_does_not_make_false_turnover(workspace):
    c, key, _ = workspace
    result = c.get(f'/planning/{key}').json()['statistics']
    missing = next(p for p in result['products'] if p['sku'] == '84969')
    assert missing['sales'] is None
    assert missing['turnover'] is None
    assert result['total_sales'] is None
    assert any(row['sales'] is None for row in result['trend'])
    selected = c.get(f'/planning/{key}?category=保暖用品').json()['statistics']
    assert len(selected['products']) == 4
    assert selected['total_sales'] is not None


@pytest.mark.asyncio
async def test_forecast_counts_only_timely_confirmed_receipts(workspace, monkeypatch):
    c, key, data = workspace
    ops = datasets[key][0]
    sku = data['products'][0]['sku']
    state = state_for(workflows, key)
    def predict(rows, h, service):
        return {'status': 'ready', 'prediction': [10]*h, 'target_stock': 1000}
    monkeypatch.setattr('demo.demand_models.compare_and_calibrate', predict)
    def plan(status, quantity, eta):
        return {'status': status, 'lines': [{'sku': sku, 'confirmed': quantity, 'received': 0, 'confirmed_due': eta}]}
    state['requests'] = [plan('pending', 500, '2026-09-12'), plan('confirmed', 30, '2026-09-13'),
        plan('confirmed', 50, '2026-09-30'), plan('confirmed', 20, '2026-09-10')]
    result = await evaluate(ops, state, sku)
    assert result['incoming_counted'] == 30
    assert result['overdue_incoming'] == 20
    assert result['quantity'] == max(0, 1000-ops.products[sku].available_stock-30)


def test_cancel_keeps_received_inventory(workspace):
    c, key, data = workspace
    sku = data['products'][0]['sku']
    order = make_order(c, key, sku)
    url = f'/planning/{key}/requests/{order["id"]}'
    c.post(url, json={'action': 'submit'})
    c.post(url, json={'action': 'feedback', 'lines': [{'sku': sku, 'quantity': 8, 'due': '2026-09-18'}]})
    c.post(url, json={'action': 'receive', 'token': 'r', 'lines': [{'sku': sku, 'quantity': 2, 'due': data['as_of']}]})
    before = datasets[key][0].products[sku].available_stock
    assert c.post(url, json={'action': 'cancel'}).status_code == 200
    p = next(p for p in c.get(f'/planning/{key}').json()['statistics']['products'] if p['sku'] == sku)
    assert p['stock'] == before and p['incoming'] == 0


def test_repair_preserves_current_receipts(workspace):
    c, key, data = workspace
    sku = '84969'
    order = make_order(c, key, sku)
    url = f'/planning/{key}/requests/{order["id"]}'
    c.post(url, json={'action': 'submit'})
    c.post(url, json={'action': 'feedback', 'lines': [{'sku': sku, 'quantity': 8, 'due': '2026-09-18'}]})
    c.post(url, json={'action': 'receive', 'token': 'repair-r', 'lines': [{'sku': sku, 'quantity': 2, 'due': data['as_of']}]})
    for _ in range(2):
        detail = c.get(f'/detail/{key}/{sku}').json()
        response = c.post('/repair', json={'dataset_id': key, 'sku': sku, 'day': detail['missing_dates'][0],
            'units': 1, 'selling_price': '4.25', 'has_receipt': False, 'received_units': 0})
        assert response.status_code == 200, response.text
    ops = datasets[key][0]
    assert ops.products[sku].available_stock == ops.daily[sku][-1].closing_stock+2


@pytest.mark.asyncio
async def test_analysis_graph_queries_selected_evidence(workspace, monkeypatch):
    c, key, data = workspace
    from demo.analytics_agent import analyze
    from demo.planning_workspace import statistics
    calls = []
    class FakeClient:
        async def close(self):
            calls.append('closed')
    class FakeModel:
        def __init__(self):
            self.client = FakeClient()
        async def ask(self, instruction, payload):
            calls.append(payload)
            if 'skus' in instruction:
                return {'skus': ['85123A', 'not-a-product']}
            assert len(payload['evidence']) == 1
            assert payload['evidence'][0]['product']['sku'] == '85123A'
            assert 'comparison' in payload
            return {'sales_interpretation': '销售分析。', 'inventory_interpretation': '库存分析。'}
    monkeypatch.setattr('demo.analytics_agent.ApiModel', FakeModel)
    result = await analyze(statistics(datasets[key][0], state_for(workflows, key)), True)
    assert result['sales_interpretation'] == '销售分析。'
    assert result['inventory_interpretation'] == '库存分析。'
    assert len(result['trace']) == 3
    assert calls[-1] == 'closed'


def test_setup_starts_automatic_forecast(workspace, monkeypatch):
    import time
    c, key, data = workspace
    calls = []
    async def fake(ops, state, sku):
        calls.append(sku)
        return {'sku': sku, 'status': 'ready', 'quantity': 3, 'due': str(ops.as_of), 'daily': []}
    monkeypatch.setattr('demo.planning_workspace.evaluate', fake)
    assert c.get(f'/planning/{key}').json()['state']['setup_complete'] is False
    assert calls == []
    assert c.post(f'/planning/{key}/start', json={'lead_days': 7, 'cover_days': 1, 'service': .95}).status_code == 200
    for _ in range(50):
        state = c.get(f'/planning/{key}').json()['state']
        if state['calculation']['status'] == 'complete':
            break
        time.sleep(.01)
    assert len(state['forecasts']) == len(data['products'])
    assert len(calls) == len(data['products'])
    c.get(f'/planning/{key}')
    assert len(calls) == len(data['products'])
    c.post(f'/planning/{key}/settings', json={'lead_days': 8, 'cover_days': 1, 'service': .95})
    for _ in range(50):
        state = c.get(f'/planning/{key}').json()['state']
        if state['forecast_revision'] == state['revision']:
            break
        time.sleep(.01)
    assert len(calls) == 2*len(data['products'])


def test_model_config_does_not_return_secret(monkeypatch):
    monkeypatch.setenv('DEMO_API_KEY', 'private-test-secret')
    with TestClient(app) as c:
        response = c.get('/config')
        assert response.json()['key_present'] is True
        assert 'private-test-secret' not in response.text


def test_review_period_fixed_daily(workspace):
    c, key, _ = workspace
    assert c.post(f'/planning/{key}/settings', json={'lead_days': 7, 'service': .95}).status_code == 200
    assert c.get(f'/planning/{key}').json()['state']['parameters']['cover_days'] == 1
    assert c.post(f'/planning/{key}/settings', json={'lead_days': 7, 'service': .95, 'cover_days': 10}).status_code == 422
