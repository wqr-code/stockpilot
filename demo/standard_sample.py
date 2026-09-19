"""Two-year, ledger-balanced demonstration; product labels retain UCI attribution."""
from datetime import date, timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
from random import Random
from demo.operations import DailySale, Product, OperationsTools, Policy


def sample_tools():
    catalog = json.loads(Path(__file__).with_name('data').joinpath('retail_catalog.json').read_text(encoding='utf-8'))
    cutoff = date(2026, 9, 14)
    products, daily = [], {}
    # Current cover by scenario: urgent, approaching replenishment, comfortable, excess.
    covers = [2, 5, 3, 6, 70, 4, 18, 0, 55, 12, 5, 16, 65, 7, 4, 20]
    for i, item in enumerate(catalog):
        rng = Random(700+i)
        base = 8+i%6*3
        units = []
        for t in range(730):
            day = cutoff-timedelta(days=730-t)
            weekly = [0.94, 0.97, 1.0, 1.03, 1.10, 1.24, 1.18][day.weekday()]
            trend = 0.8+0.4*t/729 if i in (0, 2, 10) else 1.2-0.45*t/729 if i in (4, 8, 12) else 1.0
            seasonal = 1+0.07*math.sin(2*math.pi*t/365)
            units.append(max(1, round(base*weekly*trend*seasonal+rng.uniform(-1.3, 1.3))))
        price = Decimal(item['source_unit_price_gbp'])
        target = round(sum(units[-30:])/30*covers[i])
        # The final 21-day batch runs down to the scenario's declared current cover.
        # No sale is censored: zero stock is reached only at the end of the last day.
        balance = sum(units[:21])+base*8
        rows = []
        for t, sold in enumerate(units):
            incoming = 0
            if t == 709:
                incoming = max(0, sum(units[t:])+target-balance)
            elif t < 709 and balance < sold+base*5:
                incoming = sum(units[t:min(t+14,709)])+base*6-balance
            closing = balance+incoming-sold
            assert closing >= 0
            rows.append(DailySale(cutoff-timedelta(days=730-t), sold, price, True, None, balance, incoming, closing))
            balance = closing
        products.append(Product(item['sku'], item['name'], cutoff-timedelta(days=730), price, None, balance))
        daily[item['sku']] = rows
    return OperationsTools(products, daily, cutoff, Policy(training_days=730))


def seed_workflow(ops):
    from demo.planning_workspace import state_for
    state = state_for({}, 'sample')
    state['sample_version'] = 'two-year-v2'
    # Different response states demonstrate duplicate protection, timely supply, and delay.
    for index, (sku, status, quantity, offset) in enumerate([
        ('84406B', 'confirmed', 220, 1), ('22752', 'pending', 150, 7),
        ('22745', 'confirmed', 180, -1), ('22310', 'partial', 160, 2)]):
        state['requests'].append({'id': f'DEMO-REQ-{index+1:03}', 'token': f'seed-{index}',
            'status': status, 'created': str(ops.as_of-timedelta(days=8))+'T09:00:00',
            'lines': [{'sku': sku, 'quantity': quantity, 'due': str(ops.as_of+timedelta(days=offset)),
                'confirmed': 0 if status=='pending' else quantity, 'confirmed_due': None if status=='pending' else str(ops.as_of+timedelta(days=offset)),
                'received': 40 if status=='partial' else 0}], 'receipt_tokens': [], 'notes': []})
    # A partial receipt is an explicit current-snapshot movement, added exactly once.
    sku = '22310'
    from dataclasses import replace
    old = ops.products[sku]
    ops.products[sku] = replace(old, available_stock=old.available_stock+40)
    state['receipts'].append({'date': str(ops.as_of), 'sku': sku, 'opening': old.available_stock,
        'received': 40, 'closing': old.available_stock+40, 'order': 'DEMO-REQ-004'})
    state['events'] = [{'time': str(ops.as_of)+'T09:00:00', 'text': '载入两年经营示例：含待确认、及时入库、延期和分批入库情景'}]
    return state
