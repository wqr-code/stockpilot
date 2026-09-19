from demo.standard_sample import sample_tools, seed_workflow
from demo.planning_workspace import statistics
from demo.import_data import import_csv
from pathlib import Path


def test_two_year_sample_balances_and_is_complete():
    ops = sample_tools()
    assert len(ops.products) == 16
    for sku, rows in ops.daily.items():
        assert len(rows) == 730
        for i, row in enumerate(rows):
            assert row.in_stock
            assert row.closing_stock == row.opening_stock+row.received_units-row.units >= 0
            if i:
                assert (row.day-rows[i-1].day).days == 1
                assert row.opening_stock == rows[i-1].closing_stock
        assert ops.products[sku].available_stock == rows[-1].closing_stock
    state = seed_workflow(ops)
    stats = statistics(ops, state)
    assert stats['total_sales'] is not None
    assert stats['average_stock'] is not None
    for p in stats['products']:
        assert p['stock'] == ops.products[p['sku']].available_stock
    assert next(p for p in stats['products'] if p['sku']=='84406B')['incoming'] == 220
    assert next(p for p in stats['products'] if p['sku']=='22752')['incoming'] == 0
    assert next(p for p in stats['products'] if p['sku']=='22310')['incoming'] == 120


def test_exported_standard_sample_imports():
    root = Path(__file__).resolve().parents[1]/'demo/data'
    ops = import_csv((root/'products.csv').read_text(encoding='utf-8-sig'), (root/'sales.csv').read_text(encoding='utf-8-sig'), '2026-09-14')
    assert len(ops.daily['85123A']) == 730
    assert ops.products['85123A'].available_stock == 20
