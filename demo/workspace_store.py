"""JSON-only local persistence, with atomic replacement. Never stores model keys."""
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
from demo.operations import Product,DailySale,OperationsTools,Policy

PATH=Path(__file__).with_name('data')/'workspace.local.json'

def dumps(datasets, workflows, keys=None):
    selected = set(datasets) if keys is None else set(keys)
    value={'datasets':{},'workflows':workflows}
    for key,(ops,synthetic) in datasets.items():
        if key not in selected:
            continue
        value['datasets'][key]={'products':[asdict(p) for p in ops.products.values()],
            'daily':{sku:[asdict(r) for r in rows] for sku,rows in ops.daily.items()},
            'as_of':str(ops.as_of),'policy':asdict(ops.policy),'synthetic':synthetic}
    value['workflows'] = {key: workflows[key] for key in selected if key in workflows}
    return json.dumps(value, ensure_ascii=False, default=str)

def loads(payload):
    value=json.loads(payload)
    restored={}
    restored_workflows=value.get('workflows',{})
    for key,item in value.get('datasets',{}).items():
        products=[];daily={}
        for p in item['products']:
            p['launched_on']=date.fromisoformat(p['launched_on']);p['current_price']=Decimal(p['current_price'])
            p['price_floor']=Decimal(p['price_floor']) if p['price_floor'] is not None else None
            products.append(Product(**p))
        for sku,rows in item['daily'].items():
            daily[sku]=[DailySale(**{**r,'day':date.fromisoformat(r['day']),'selling_price':Decimal(r['selling_price'])}) for r in rows]
        restored[key]=(OperationsTools(products,daily,date.fromisoformat(item['as_of']),Policy(**item['policy'])),item['synthetic'])
    return restored, restored_workflows

def save(datasets,workflows):
    if os.getenv('PYTEST_CURRENT_TEST'):return
    temp=PATH.with_suffix('.tmp')
    temp.write_text(dumps(datasets, workflows),encoding='utf-8')
    temp.replace(PATH)

def restore(datasets,workflows):
    if not PATH.exists():return
    restored, restored_workflows = loads(PATH.read_text(encoding='utf-8'))
    datasets.update(restored);workflows.update(restored_workflows)
