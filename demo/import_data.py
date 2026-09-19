"""CSV import boundary for local operations data; missing days stay missing."""
from dataclasses import replace
from datetime import timedelta
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO

from demo.operations import DailySale, OperationsTools, Product

PRODUCT_FIELDS = ['sku', 'name', 'launched_on', 'active', 'current_price', 'price_floor', 'available_stock']
SALES_FIELDS = ['date', 'sku', 'units', 'selling_price', 'in_stock', 'promotion_id', 'opening_stock', 'received_units']


class ImportFailure(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__('; '.join(issues))


def rows(text, fields, label):
    reader = csv.DictReader(StringIO(text.lstrip('\ufeff')))
    headers = reader.fieldnames or []
    if len(set(headers)) != len(headers) or set(headers) != set(fields):
        raise ImportFailure([f'{label}表头必须为：' + ','.join(fields)])
    result = []
    for number, row in enumerate(reader, 2):
        if number > 100002:
            raise ImportFailure([f'{label}超过 100000 行'])
        if None in row or any(value is None for value in row.values()):
            raise ImportFailure([f'{label}第 {number} 行列数与表头不一致'])
        result.append((number, {k: v.strip() for k, v in row.items()}))
    if not result:
        raise ImportFailure([f'{label}没有数据行'])
    return result


def boolean(value):
    if value.lower() in ('true', '1', '是'):
        return True
    if value.lower() in ('false', '0', '否'):
        return False
    raise ValueError('布尔字段请填 true/false 或 1/0')


def integer(value):
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
        raise ValueError('销量与库存必须为非负整数')
    return int(parsed)


def money(value):
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed <= 0 or parsed != parsed.quantize(Decimal('.01')):
        raise ValueError('价格必须为正数，最多两位小数')
    return parsed


def import_csv(products_csv, sales_csv, as_of):
    """as_of is the first day excluded from history; inventory is as-of snapshot."""
    cutoff = date.fromisoformat(as_of)
    products, daily, issues = {}, {}, []
    for number, row in rows(products_csv, PRODUCT_FIELDS, '商品'):
        try:
            sku = row['sku']
            if not sku or not row['name'] or sku in products:
                raise ValueError('SKU/名称不能为空，SKU 不得重复')
            launched = date.fromisoformat(row['launched_on'])
            if launched > cutoff:
                raise ValueError('上架日期晚于数据截至日')
            price = money(row['current_price'])
            floor = money(row['price_floor']) if row['price_floor'] else None
            if floor is not None and floor > price:
                raise ValueError('价格底线高于当前价格')
            products[sku] = Product(sku, row['name'], launched, price, floor,
                                    integer(row['available_stock']), boolean(row['active']))
            daily[sku] = []
        except (ValueError, InvalidOperation) as exc:
            issues.append(f'商品第 {number} 行：{exc}')
    seen = set()
    for number, row in rows(sales_csv, SALES_FIELDS, '销售'):
        try:
            sku, day = row['sku'], date.fromisoformat(row['date'])
            if sku not in products:
                raise ValueError('SKU 不在有效商品表中')
            if day >= cutoff or day < products[sku].launched_on:
                raise ValueError('销售日期须在上架日及以后、截至日之前')
            if (sku, day) in seen:
                raise ValueError('同一 SKU 同一日期不能重复')
            seen.add((sku, day))
            daily[sku].append(DailySale(day, integer(row['units']), money(row['selling_price']),
                                       boolean(row['in_stock']), row['promotion_id'] or None,
                                       integer(row['opening_stock']) if row['opening_stock'] else None,
                                       integer(row['received_units'])))
        except (ValueError, InvalidOperation) as exc:
            issues.append(f'销售第 {number} 行：{exc}')
    if issues:
        raise ImportFailure(issues[:30])
    for sku, records in daily.items():
        previous = None
        rebuilt = []
        for record in sorted(records, key=lambda r: r.day):
            opening = record.opening_stock
            if previous and record.day == previous.day + timedelta(days=1):
                if opening is not None and opening != previous.closing_stock:
                    issues.append(f'{sku} {record.day}：期初库存与前一天期末库存不一致')
                opening = previous.closing_stock
            elif opening is None:
                issues.append(f'{sku} {record.day}：首条记录或日期中断后须填写期初库存')
            if opening is None:
                continue
            closing = opening + record.received_units - record.units
            if closing < 0:
                issues.append(f'{sku} {record.day}：期末库存为负，请检查销量与入库量')
            previous = replace(record, opening_stock=opening, closing_stock=closing)
            rebuilt.append(previous)
        daily[sku] = rebuilt
        if previous and previous.day == cutoff - timedelta(days=1) and previous.closing_stock != products[sku].available_stock:
            issues.append(f'{sku}：最后一天期末库存与商品表可售库存不一致')
    if issues:
        raise ImportFailure(issues[:30])
    return OperationsTools(list(products.values()), daily, cutoff)


def template(kind):
    """An application download, with one illustrative row; not training evidence."""
    fields = PRODUCT_FIELDS if kind == 'products' else SALES_FIELDS
    example = (['SKU-001', '示例商品', '2026-01-01', 'true', '100.00', '85.00', '120']
               if kind == 'products' else ['2026-09-10', 'SKU-001', '2', '100.00', 'true', '', '122', '0'])
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(fields)
    writer.writerow(example)
    return '\ufeff' + output.getvalue()
