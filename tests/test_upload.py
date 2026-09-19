import pytest
from fastapi.testclient import TestClient
from demo.import_data import ImportFailure, import_csv, template
from demo.web import app


def test_import_template_and_missing_history():
    data = import_csv(template('products'), template('sales'), '2026-09-11')
    assert data.products['SKU-001'].available_stock == 120
    assert data.inspect_product('SKU-001')['status'] == 'insufficient_data'
    assert data.daily['SKU-001'][0].units == 2


@pytest.mark.parametrize('bad', [
    lambda s: s + s.splitlines()[-1] + '\n',
    lambda s: s.replace('SKU-001', 'UNKNOWN'),
    lambda s: s.replace(',2,', ',-2,'),
    lambda s: s.replace('2026-09-10', '2026-09-11'),
    lambda s: s.replace(',true,', ',maybe,'),
])
def test_bad_rows_are_rejected(bad):
    with pytest.raises(ImportFailure):
        import_csv(template('products'), bad(template('sales')), '2026-09-11')




def test_invalid_upload_returns_row_errors():
    with TestClient(app) as c:
        r = c.post('/upload', json={'products_csv': template('products'),
            'sales_csv': template('sales').replace('SKU-001', 'UNKNOWN'), 'as_of': '2026-09-11'})
        assert r.status_code == 422
        assert '销售第 2 行' in r.json()['detail'][0]


def test_detail_and_assets():
    with TestClient(app) as c:
        loaded = c.post('/sample?profile=boundary').json()
        r = c.get('/detail/' + loaded['dataset_id'] + '/85123A')
        assert len(r.json()['history']['activities']) == 4
        assert c.get('/assets/upload.js').status_code == 200
        assert c.get('/assets/upload.css').status_code == 200


def test_sales_windows_use_calendar_days():
    from datetime import timedelta
    from demo.retail_sample import sample_tools
    ops = sample_tools()
    with TestClient(app) as c:
        loaded = c.post('/sample?profile=boundary').json()
        for product in loaded['products']:
            for days in (7, 30):
                rows = [r for r in ops.daily[product['sku']]
                        if ops.as_of - timedelta(days=days) <= r.day < ops.as_of]
                assert product[f'sales_{days}d'] == (None if product[f'missing_{days}d'] else sum(r.units for r in rows))
        missing = next(p for p in loaded['products'] if p['sku'] == '84969')
        assert missing['missing_7d'] == 1
        assert missing['missing_30d'] == 2
        detail = c.get('/detail/' + loaded['dataset_id'] + '/84969').json()
        assert len(detail['daily']) == 28
        assert min(r['date'] for r in detail['daily']) == str(ops.as_of - timedelta(days=30))






def test_repair_missing_dates_and_reject_overwrite():
    with TestClient(app) as c:
        loaded = c.post('/sample?profile=boundary').json()
        payload = dict(dataset_id=loaded['dataset_id'], sku='84969', day='2026-09-03',
                       units=0, selling_price='4.25', has_receipt=False)
        assert c.post('/repair', json={**payload, 'units': -1}).status_code == 422
        assert c.post('/repair', json={**payload, 'units': 999999}).status_code == 422
        assert c.post('/repair', json={**payload, 'has_receipt': True}).status_code == 422
        repaired = c.post('/repair', json=payload)
        assert repaired.status_code == 200
        assert c.post('/repair', json=payload).status_code == 409
        loaded = repaired.json()
        p = next(p for p in loaded['products'] if p['sku'] == '84969')
        assert p['sales_30d'] is None
        payload.update(dataset_id=loaded['dataset_id'], day='2026-09-08')
        loaded = c.post('/repair', json=payload).json()
        p = next(p for p in loaded['products'] if p['sku'] == '84969')
        assert p['sales_30d'] is not None
        assert p['status'] != 'insufficient_data'
        assert c.get('/detail/' + loaded['dataset_id'] + '/84969').json()['missing_dates'] == []


@pytest.mark.parametrize('opening,received', [('1','0'), ('121','0'), ('122','-1'), ('','0')])
def test_inventory_import_rejects_invalid_ledger(opening, received):
    sales = template('sales').replace(',122,0', f',{opening},{received}')
    with pytest.raises(ImportFailure):
        import_csv(template('products'), sales, '2026-09-11')


def test_inventory_import_derives_next_opening():
    sales = template('sales').replace('2026-09-10', '2026-09-09')
    sales += '2026-09-10,SKU-001,3,100.00,true,,,3\n'
    ops = import_csv(template('products'), sales, '2026-09-11')
    assert ops.daily['SKU-001'][1].opening_stock == 120
    assert ops.daily['SKU-001'][1].closing_stock == 120
