from demo.planning_workspace import supply_risk

def test_timely_receipt_resolves_shortage():
    daily=[{'date':'2026-09-14','demand':10},{'date':'2026-09-15','demand':10}]
    r=supply_risk(10,daily,[{'date':'2026-09-15','quantity':10}],5)
    assert r['periods']==[] and r['sales_at_risk']==0

def test_late_receipt_and_no_double_counting():
    daily=[{'date':f'2026-09-{d}','demand':10} for d in (14,15,16)]
    r=supply_risk(0,daily,[{'date':'2026-09-16','quantity':10}],5)
    assert r['unmet_units']==20 and r['sales_at_risk']==100
    assert r['periods']==[{'start':'2026-09-14','end':'2026-09-15'}]
    assert supply_risk(0,daily,[],5)['unmet_units']==30


def test_statistics_separates_existing_and_proposed_replenishment():
    from dataclasses import replace
    from demo.standard_sample import sample_tools
    from demo.planning_workspace import statistics, state_for
    ops = sample_tools()
    sku = next(iter(ops.products))
    ops.products[sku] = replace(ops.products[sku], available_stock=10)
    state = state_for({}, 'test')
    state['forecasts'][sku] = {'status': 'ready', 'revision': 0, 'quantity': 20,
        'due': '2026-09-15', 'daily': [{'date': f'2026-09-{d}', 'demand': 10} for d in (14, 15, 16)]}
    def product():
        return next(p for p in statistics(ops, state)['products'] if p['sku'] == sku)
    result = product()
    assert result['supply_risk']['unmet_units'] == 20
    assert result['after_replenishment_risk']['periods'] == []
    state['forecasts'][sku]['due'] = '2026-09-16'
    result = product()
    assert result['after_replenishment_risk']['periods'] == [{'start': '2026-09-15', 'end': '2026-09-15'}]
    assert result['after_replenishment_risk']['unmet_units'] == 10
    state['requests'] = [{'status': 'confirmed', 'lines': [{'sku': sku, 'confirmed': 20,
        'received': 0, 'confirmed_due': '2026-09-15'}]}]
    state['forecasts'][sku]['quantity'] = 0
    result = product()
    assert result['supply_risk']['periods'] == []
    assert result['after_replenishment_risk']['periods'] == []
