"""Loopback workspace. Only explicit chat submissions call the configured model."""
from dataclasses import replace
from decimal import Decimal
from demo.operations import DailySale, OperationsTools
import asyncio
import os
from datetime import date, timedelta
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from dotenv import load_dotenv
from demo.import_data import ImportFailure, import_csv, template
from demo.retail_sample import sample_tools

app = FastAPI(title='StockPilot')
datasets = {}
run_lock = asyncio.Lock()


class Upload(BaseModel):
    products_csv: str = Field(max_length=2_000_000)
    sales_csv: str = Field(max_length=8_000_000)
    as_of: date






@app.get('/config')
async def config():
    load_dotenv(Path(__file__).resolve().parents[1] / '.env.demo')
    return {'chat_configured': bool(os.getenv('DEMO_API_KEY') and os.getenv('DEMO_MODEL')),
            'key_present': bool(os.getenv('DEMO_API_KEY')),
            'model': os.getenv('DEMO_MODEL', ''), 'base_url': os.getenv('DEMO_BASE_URL', '')}


@app.get('/assets/{name}')
async def asset(name: str):
    types = {'upload.js': 'text/javascript', 'upload.css': 'text/css', 'stockpilot.svg': 'image/svg+xml'}
    if name not in types:
        raise HTTPException(404)
    return Response(Path(__file__).with_name(name).read_text(encoding='utf-8'),
                    media_type=types[name])


@app.get('/detail/{dataset_id}/{sku}')
async def detail(dataset_id: str, sku: str):
    entry = datasets.get(dataset_id)
    if entry is None or sku not in entry[0].products:
        raise HTTPException(404, '请重新载入数据或选择商品')
    ops, synthetic = entry
    return {'history': ops.activity_history(sku), 'synthetic': synthetic,
            'missing_dates': missing_dates(ops, sku),
            'repair_opening': repair_opening(ops, sku),
            'daily': [{'date': str(r.day), 'units': r.units} for r in ops.daily[sku] if ops.as_of - timedelta(days=30) <= r.day < ops.as_of]}




def register(operations, synthetic, key=None):
    if len(datasets) >= 20:
        del datasets[next(iter(datasets))]
    key = key or uuid.uuid4().hex
    datasets[key] = (operations, synthetic)
    products = []
    for sku, p in operations.products.items():
        history, missing = operations.training_history(sku)
        sales_windows = {}
        for days in (7, 30):
            rows, missing_days = operations._window(sku, days)
            sales_windows[f'sales_{days}d'] = None if missing_days else sum(r.units for r in rows)
            sales_windows[f'missing_{days}d'] = missing_days
        products.append({**operations.inspect_product(sku), **sales_windows, 'name': p.name,
                         'available_stock': None if missing_dates(operations, sku) else p.available_stock,
                         'history_rows': len(history), 'history_missing_days': missing,
                         'current_price': str(p.current_price),
                         'price_floor': str(p.price_floor) if p.price_floor else None})
    save_workspace()
    return {'dataset_id': key, 'synthetic': synthetic, 'as_of': str(operations.as_of),
            'products': products,
            'note': '缺失日期未补零；数据截至日当天不计入历史。'}


@app.get('/', response_class=HTMLResponse)
async def index():
    return Path(__file__).with_name('upload.html').read_text(encoding='utf-8')


@app.get('/template/{kind}')
async def download(kind: str):
    if kind not in ('products', 'sales'):
        raise HTTPException(404)
    return Response(template(kind), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{kind}.csv"'})


@app.post('/sample')
async def sample(profile: str = 'standard'):
    if profile == 'boundary':
        return register(sample_tools(), True)
    from demo.standard_sample import sample_tools as standard_tools, seed_workflow
    ops = standard_tools()
    key = uuid.uuid4().hex
    workflows[key] = seed_workflow(ops)
    return register(ops, True, key)


@app.post('/upload')
async def upload(data: Upload):
    try:
        operations = import_csv(data.products_csv, data.sales_csv, data.as_of.isoformat())
    except ImportFailure as exc:
        raise HTTPException(422, detail=exc.issues) from exc
    except ValueError as exc:
        raise HTTPException(422, detail=[str(exc)]) from exc
    return register(operations, False)






def missing_dates(ops, sku):
    history, _ = ops.training_history(sku)
    start = min((r.day for r in history), default=ops.as_of - timedelta(days=ops.policy.observation_days))
    start = min(start, ops.as_of - timedelta(days=ops.policy.observation_days))
    start = max(start, ops.products[sku].launched_on)
    present = {r.day for r in ops.daily.get(sku, [])}
    return [str(start + timedelta(days=i)) for i in range((ops.as_of - start).days)
            if start + timedelta(days=i) not in present]


def repair_opening(ops, sku):
    dates = missing_dates(ops, sku)
    if not dates:
        return None
    prior = date.fromisoformat(dates[0]) - timedelta(days=1)
    return next((r.closing_stock for r in ops.daily.get(sku, []) if r.day == prior), None)


class Repair(BaseModel):
    dataset_id: str
    sku: str
    day: date
    units: int = Field(ge=0, strict=True)
    selling_price: Decimal = Field(gt=0, allow_inf_nan=False)
    has_receipt: bool = Field(default=False, strict=True)
    received_units: int = Field(default=0, ge=0, strict=True)
    promotion_id: str | None = Field(default=None, max_length=100)


@app.post('/repair')
async def repair(data: Repair):
    async with run_lock:
        entry = datasets.get(data.dataset_id)
        if entry is None or data.sku not in entry[0].products:
            raise HTTPException(404, '数据已失效，请重新载入')
        ops, synthetic = entry
        if str(data.day) not in missing_dates(ops, data.sku):
            raise HTTPException(409, '该日期不属于缺失日期，不能覆盖已有记录')
        dates = missing_dates(ops, data.sku)
        if str(data.day) != dates[0]:
            raise HTTPException(422, '请先补录最早的缺失日期，才能衔接期初库存')
        opening = repair_opening(ops, data.sku)
        if opening is None:
            raise HTTPException(422, '缺少连续库存台账，不能自动推算；请重新导入完整记录')
        if (not data.has_receipt and data.received_units != 0) or (data.has_receipt and data.received_units <= 0):
            raise HTTPException(422, '有入库请填写正整数，无入库时入库量必须为 0')
        closing = opening + data.received_units - data.units
        if closing < 0:
            raise HTTPException(422, '库存不能为负，请检查销量或是否漏填入库')
        daily = {sku: list(rows) for sku, rows in ops.daily.items()}
        daily[data.sku].append(DailySale(data.day, data.units, data.selling_price,
            opening + data.received_units > 0, data.promotion_id or None,
            opening, data.received_units, closing))
        rows = sorted(daily[data.sku], key=lambda r: r.day)
        balance, previous, rebuilt = rows[0].opening_stock, rows[0].day - timedelta(days=1), []
        for row in rows:
            if row.day != previous + timedelta(days=1):
                balance = None
            if balance is None or row.received_units is None:
                rebuilt.append(replace(row, opening_stock=None, closing_stock=None))
            else:
                end = balance + row.received_units - row.units
                if end < 0:
                    raise HTTPException(422, f'{row.day} 库存为负，请核对补录数量及后续入库记录')
                rebuilt.append(replace(row, opening_stock=balance, closing_stock=end,
                                       in_stock=balance + row.received_units > 0))
                balance = end
            previous = row.day
        daily[data.sku] = rebuilt
        receipt_total = sum(r['received'] for r in workflows.get(data.dataset_id, {}).get('receipts', []) if r['sku'] == data.sku)
        products = [replace(p, available_stock=balance + receipt_total) if p.sku == data.sku and balance is not None else p
                    for p in ops.products.values()]
        updated = OperationsTools(products, daily, ops.as_of, ops.policy)
        datasets[data.dataset_id] = (updated, synthetic)
        workflows.get(data.dataset_id, {}).update(cards=[])
        workflow = workflows.get(data.dataset_id, {})
        workflow['revision'] = workflow.get('revision', 0) + 1
        workflows.get(data.dataset_id, {}).pop('insight', None)
        return register(updated, synthetic, data.dataset_id)







# Local demonstration workflow; all state shares the dataset lifetime.
workflows = {}







from demo.workspace_store import save as store_save, restore as store_restore

def save_workspace():
    store_save(datasets, workflows)

@app.get('/workspace')
async def workspace():
    if not datasets:
        return {'dataset':None}
    key=next(reversed(datasets))
    ops,synthetic=datasets[key]
    return {'dataset':register(ops,synthetic,key), 'workflow':workflows.get(key)}


store_restore(datasets,workflows)

from demo.planning_workspace import mount
mount(app, datasets, workflows, run_lock, save_workspace)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=3211)
