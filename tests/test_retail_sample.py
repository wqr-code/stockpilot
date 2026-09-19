from demo.retail_sample import sample_tools


def test_retail_catalog_and_operational_cases():
    ops = sample_tools()
    assert len(ops.products) == 16
    assert ops.products['85123A'].name == '白色爱心悬挂烛台'
    assert len(ops.daily['85123A']) == 365
    assert {ops.inspect_product(sku)['status'] for sku in ops.products} >= {
        'slow_moving_candidate', 'monitor', 'new_product',
        'availability_review', 'insufficient_data', 'listing_review'}
    assert len(ops.activity_history('85123A')['activities']) == 4
    assert ops.daily == sample_tools().daily


def test_demo_inventory_is_balanced():
    ops = sample_tools()
    for sku, rows in ops.daily.items():
        for i, row in enumerate(rows):
            assert row.closing_stock == row.opening_stock + row.received_units - row.units
            assert row.closing_stock >= 0
            if i and (row.day - rows[i-1].day).days == 1:
                assert row.opening_stock == rows[i-1].closing_stock
        assert ops.products[sku].available_stock == rows[-1].closing_stock
