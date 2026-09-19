"""Public UCI product descriptions with explicitly simulated operating records."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from random import Random

from demo.operations import DailySale, OperationsTools, Product


def sample_tools() -> OperationsTools:
    catalog = json.loads(Path(__file__).with_name('data').joinpath('retail_catalog.json').read_text(encoding='utf-8'))
    as_of = date(2026, 9, 11)
    rng = Random(42)
    products, daily = [], {}
    for index, item in enumerate(catalog):
        sku = item['sku']
        age = 12 if index in (10, 11) else 365
        price = Decimal(item['source_unit_price_gbp'])
        floor = (price * Decimal('0.75')).quantize(Decimal('0.01'))
        discount = (price * Decimal('0.90')).quantize(Decimal('0.01'))
        base = 2 + index % 5
        stock = base * (160 if index % 4 == 0 else 25)
        if index == 7:
            stock = 0
        products.append(Product(sku, item['name'], as_of - timedelta(days=age),
                                price, floor, stock, active=index != 15))
        rows = []
        for offset in range(age, 0, -1):
            day = as_of - timedelta(days=offset)
            campaign = next((f'demo-sale-{start}' for start in (300, 210, 120, 40)
                             if start <= offset < start + 10), None) if index % 3 != 1 else None
            in_stock = not (index == 7 and offset <= 6)
            units = max(0, base + (2 if day.weekday() >= 5 else 0) + rng.randint(-1, 1))
            if campaign:
                units += base
            rows.append(DailySale(day, units if in_stock else 0,
                                  discount if campaign else price, in_stock, campaign))
        # Build a balanced ledger before removing demo records.
        balance = sum(r.units for r in rows) if index == 7 else base * (160 if index % 4 == 0 else 25)
        if index % 4 == 0:
            balance += sum(r.units for r in rows)
        ledger = []
        for row in rows:
            received = base * 30 if index != 7 and balance < base * 10 else 0
            closing = balance + received - row.units
            ledger.append(replace(row, opening_stock=balance, received_units=received,
                                  closing_stock=closing))
            balance = closing
        products[-1] = replace(products[-1], available_stock=balance)
        daily[sku] = [r for r in ledger if not (index == 14 and (as_of-r.day).days in (3, 8))]
    return OperationsTools(products, daily, as_of)
