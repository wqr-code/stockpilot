"""Single-stock sales planning workspace. Confirmed receipts are never sales history."""
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import asyncio
import logging
import uuid
from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from demo.operations import OperationsTools


logger = logging.getLogger(__name__)


class Settings(BaseModel):
    lead_days: int = Field(default=7, ge=1, le=30)
    cover_days: Literal[1] = 1  # Fixed daily review; retained for old clients.
    service: float = Field(default=.95, ge=.5, le=.99)


class ProductSettings(BaseModel):
    category: str = Field(default='未分类', min_length=1, max_length=40)
    lead_days: int | None = Field(default=None, ge=1, le=30)


class Line(BaseModel):
    sku: str
    quantity: int = Field(gt=0, strict=True)
    due: date


class NewOrder(BaseModel):
    lines: list[Line] = Field(min_length=1, max_length=100)
    token: str = Field(min_length=1, max_length=100)


class Action(BaseModel):
    action: str
    lines: list[Line] = Field(default_factory=list)
    note: str = Field(default='', max_length=500)
    token: str = Field(default='', max_length=100)


def order_event(ops, order, action, lines):
    labels = {'create': '已生成补货草稿', 'submit': '已提交补货需求',
              'feedback': '生产已确认', 'receive': '已登记入库', 'cancel': '已取消剩余补货'}
    items = [{'sku': line['sku'], 'name': ops.products[line['sku']].name,
              'quantity': line['quantity']} for line in lines if line['quantity'] > 0]
    summary = '；'.join(f"{item['name']} {item['quantity']} 件" for item in items)
    return {'type': action, 'order_id': order['id'], 'items': items,
            'text': labels[action]+'：'+summary}


def now():
    return datetime.now().isoformat(timespec='seconds')


def state_for(workflows, key):
    state = workflows.setdefault(key, {})
    state.setdefault('requests', [])
    state.setdefault('receipts', [])
    state.setdefault('events', [])
    state.setdefault('parameters', Settings().model_dump())
    state.setdefault('product_settings', {})
    state.setdefault('forecasts', {})
    state.setdefault('revision', 0)
    state.setdefault('setup_complete', False)
    state.setdefault('forecast_revision', -1)
    if state['parameters'].get('cover_days', 1) != 1:
        state['parameters']['cover_days'] = 1
        state['revision'] += 1
    if not state.get('legacy_migrated'):
        for old in state.get('plans', []):
            if old.get('closed'):
                continue
            state['requests'].append({'id': 'REQ-'+uuid.uuid4().hex[:8].upper(), 'token': uuid.uuid4().hex,
                'status': 'pending', 'created': now(), 'lines': [{'sku': old['sku'], 'quantity': old['quantity'],
                'due': old['eta'], 'confirmed': 0, 'confirmed_due': None, 'received': 0}],
                'notes': [{'time': now(), 'action': '迁移', 'note': '旧本地计划，需录入生产确认后才计入待入库'}], 'receipt_tokens': []})
        state['legacy_migrated'] = True
    return state


def incoming(state, sku):
    return [{'date': line['confirmed_due'], 'quantity': line['confirmed'] - line['received']}
            for order in state['requests'] if order['status'] in ('confirmed', 'partial')
            for line in order['lines'] if line['sku'] == sku and line['confirmed'] > line['received']]


def category_for(state, sku):
    return state['product_settings'].get(sku, {}).get('category', '未分类')


def period_statistics(ops, state, days=30, category=None, start=None, end=None):
    if (start is None) != (end is None):
        raise HTTPException(422, '请同时选择开始和结束日期')
    if start is None:
        if not 1 <= days <= 3660:
            raise HTTPException(422, '时间范围无效')
        end = ops.as_of - timedelta(days=1)
        start = ops.as_of - timedelta(days=days)
    if start > end or end >= ops.as_of or (end-start).days >= 3660:
        raise HTTPException(422, '请选择有效日期范围，结束日期不能晚于最后一个完整销售日')
    days = (end-start).days+1
    products, daily = [], defaultdict(lambda: {'sales': 0, 'revenue': 0, 'received': 0, 'stock': 0, 'complete': True})
    sales_rows, ledger = [], []
    for sku, p in ops.products.items():
        cat = category_for(state, sku)
        if category and cat != category:
            continue
        rows = [r for r in ops.daily.get(sku, []) if max(start, p.launched_on) <= r.day <= end]
        missing = max(0, (end-max(start, p.launched_on)).days+1)-len(rows)
        complete = not missing and bool(rows)
        inventory_complete = complete and all(r.closing_stock is not None for r in rows)
        units = sum(r.units for r in rows) if complete else None
        average = sum(r.closing_stock for r in rows)/len(rows) if inventory_complete else None
        previous = [r for r in ops.daily[sku] if start-timedelta(days=days) <= r.day < start]
        previous_start = max(p.launched_on, start-timedelta(days=days))
        previous_complete = len(previous) == max(0, (start-previous_start).days) and bool(previous)
        products.append({'sku': sku, 'name': p.name, 'category': cat, 'stock': p.available_stock,
            'incoming': sum(x['quantity'] for x in incoming(state, sku)), 'sales': units,
            'revenue': float(sum((r.selling_price*r.units for r in rows), Decimal(0))) if complete else None,
            'previous_sales': sum(r.units for r in previous) if previous_complete else None,
            'average_stock': round(average, 2) if average is not None else None,
            'turnover': round(units/average, 3) if average and units is not None else None,
            'received': sum(r.received_units for r in rows) if complete and all(r.received_units is not None for r in rows) else None,
            'coverage': round(p.available_stock/(units/len(rows)), 1) if units else None,
            'missing': missing, 'lead_days': state['product_settings'].get(sku, {}).get('lead_days') or state['parameters']['lead_days']})
        for window in (7, 30):
            window_rows, window_missing = ops._window(sku, window)
            products[-1][f'sales_{window}d'] = None if window_missing else sum(r.units for r in window_rows)
            products[-1][f'missing_{window}d'] = window_missing
        by_date = {r.day: r for r in rows}
        for i in range(days):
            day = start+timedelta(days=i)
            if day < p.launched_on:
                continue
            r = by_date.get(day)
            d = daily[str(day)]
            if r is None:
                d['complete'] = False
                continue
            d['sales'] += r.units
            d['revenue'] += float(r.selling_price*r.units)
            d['received'] += r.received_units or 0
            if r.closing_stock is None or r.received_units is None:
                d['complete'] = False
            d['stock'] += r.closing_stock or 0
            sales_rows.append({'date': str(day), 'sku': sku, 'name': p.name, 'units': r.units, 'price': str(r.selling_price), 'received': r.received_units})
            ledger.append({'date': str(day), 'sku': sku, 'name': p.name, 'opening': r.opening_stock, 'received': r.received_units, 'sales': r.units, 'closing': r.closing_stock, 'source': '历史台账'})
    selected = {p['sku'] for p in products}
    ledger.extend({**r, 'name': ops.products[r['sku']].name, 'source': r['order'], 'sales': 0} for r in state['receipts'] if r['sku'] in selected and (start <= date.fromisoformat(r['date']) <= end or (end == ops.as_of-timedelta(days=1) and date.fromisoformat(r['date']) == ops.as_of)))
    categories = []
    for cat in sorted({p['category'] for p in products}):
        group = [p for p in products if p['category'] == cat]
        categories.append({'name': cat, 'revenue': round(sum(p['revenue'] for p in group), 2) if all(p['revenue'] is not None for p in group) else None, 'sales': sum(p['sales'] for p in group) if all(p['sales'] is not None for p in group) else None})
    complete = bool(products) and all(p['sales'] is not None for p in products)
    return {'days': days, 'start': str(start), 'end': str(end),
        'products': products, 'categories': categories,
        'total_revenue': round(sum(p['revenue'] for p in products), 2) if complete else None,
        'total_sales': sum(p['sales'] for p in products) if complete else None,
        'total_received': sum(p['received'] for p in products) if products and all(p['received'] is not None for p in products) else None,
        'average_stock': round(sum(d['stock'] for d in daily.values())/days, 2) if daily and all(d['complete'] for d in daily.values()) else None,
        'trend': [{'date': day, **{k: v if d['complete'] or k == 'complete' else None for k, v in d.items()}} for day, d in sorted(daily.items())],
        'sales_rows': sorted(sales_rows, key=lambda r: (r['date'], r['sku']), reverse=True),
        'ledger': sorted(ledger, key=lambda r: r['date'], reverse=True)}


def change(current, previous):
    if current is None or previous is None:
        return {'delta': None, 'percent': None, 'reason': '历史不足或数据不全'}
    return {'delta': round(current-previous, 2),
            'percent': round((current-previous)/previous*100, 2) if previous else None,
            'reason': '上期为零' if not previous else None}


def supply_risk(stock, daily, receipts, price):
    balance = stock
    periods = []
    unmet_total = 0
    active = None
    for row in daily:
        day = row['date']
        balance += sum(r['quantity'] for r in receipts if r['date'] == day)
        unmet = max(0, row['demand']-balance)
        balance = max(0, balance-row['demand'])
        if unmet > 0:
            unmet_total += unmet
            if active is None:
                active = {'start': day, 'end': day}
                periods.append(active)
            active['end'] = day
        else:
            active = None
    return {'periods': periods, 'unmet_units': round(unmet_total, 2),
            'sales_at_risk': round(unmet_total*float(price), 2),
            'horizon_end': daily[-1]['date'] if daily else None}


def statistics(ops, state, days=30, category=None, start=None, end=None):
    current = period_statistics(ops, state, days, category, start, end)
    first = date.fromisoformat(current['start'])
    previous = period_statistics(ops, state, current['days'], category,
                                 first-timedelta(days=current['days']), first-timedelta(days=1))
    for period in (current, previous):
        period['daily_revenue'] = round(period['total_revenue']/period['days'], 2) if period['total_revenue'] is not None else None
        period['turnover'] = round(period['total_sales']/period['average_stock'], 3) if period['total_sales'] is not None and period['average_stock'] else None
    keys = ('total_revenue', 'total_sales', 'daily_revenue', 'average_stock', 'turnover')
    current['comparison'] = {'start': previous['start'], 'end': previous['end'], 'label': '较上一周期',
        'previous': {k: previous[k] for k in keys},
        'changes': {k: change(current[k], previous[k]) for k in keys}}
    for field, identity in (('products', 'sku'), ('categories', 'name')):
        prior = {p[identity]: p for p in previous[field]}
        for item in current[field]:
            old = prior.get(item[identity], {})
            item['revenue_share_percent'] = round(item['revenue']/current['total_revenue']*100, 2) if item['revenue'] is not None and current['total_revenue'] else None
            item['revenue_change'] = change(item['revenue'], old.get('revenue'))
            item['previous_revenue'] = old.get('revenue')
            if field == 'products':
                item['turnover_change'] = change(item['turnover'], old.get('turnover'))
                item['average_stock_change'] = change(item['average_stock'], old.get('average_stock'))
                f = state['forecasts'].get(item['sku'], {})
                fresh = f.get('revision') == state['revision'] and f.get('status') == 'ready'
                receipts = incoming(state, item['sku'])
                item['supply_risk'] = supply_risk(item['stock'], f.get('daily', []), receipts, ops.products[item['sku']].current_price) if fresh else None
                planned_receipts = receipts + ([{'date': f['due'], 'quantity': f['quantity']}] if fresh and f.get('quantity', 0) > 0 else [])
                item['after_replenishment_risk'] = supply_risk(item['stock'], f.get('daily', []), planned_receipts, ops.products[item['sku']].current_price) if fresh else None
                item['current_risk'] = {'as_of': str(ops.as_of), 'no_stock': item['stock'] == 0,
                    'forecast_shortage_date': f.get('first_shortage') if fresh else None,
                    'replenishment_quantity': f.get('quantity') if fresh else None}
    return current


async def evaluate(ops, state, sku):
    from demo.demand_models import compare_and_calibrate
    rows, missing = ops.training_history(sku)
    settings = state['parameters']
    lead = state['product_settings'].get(sku, {}).get('lead_days') or settings['lead_days']
    if not ops.products[sku].active:
        return {'sku': sku, 'status': 'blocked', 'reason': '商品当前未在售，请先确认是否需要备货。'}
    if missing or not rows or any(not r.in_stock for r in rows):
        return {'sku': sku, 'status': 'blocked', 'reason': '历史缺失或存在缺货，请先核对销售明细。'}
    horizon = lead + 1
    result = await asyncio.to_thread(compare_and_calibrate, rows, horizon, settings['service'])
    if result['status'] != 'ready':
        return {**result, 'sku': sku}
    predicted = result.pop('prediction')
    return replenish(ops, state, sku, result, predicted, lead)


def replenish(ops, state, sku, result, predicted, lead):
    horizon = lead + 1
    schedule = incoming(state, sku)
    end = ops.as_of+timedelta(days=horizon)
    counted = sum(x['quantity'] for x in schedule if ops.as_of <= date.fromisoformat(x['date']) < end)
    balance = ops.products[sku].available_stock
    trajectory = []
    for i, units in enumerate(predicted):
        day = str(ops.as_of+timedelta(days=i))
        balance += sum(x['quantity'] for x in schedule if x['date'] == day)
        balance -= units
        trajectory.append({'date': day, 'demand': round(units, 2), 'stock': round(balance, 2)})
    return {**result, 'sku': sku, 'daily': trajectory, 'lead_days': lead,
        'due': str(ops.as_of+timedelta(days=lead)), 'incoming_counted': counted,
        'overdue_incoming': sum(x['quantity'] for x in schedule if x['date'] < str(ops.as_of)),
        'quantity': max(0, result['target_stock']-ops.products[sku].available_stock-counted),
        'safety': max(0, result['target_stock']-int(__import__('math').ceil(sum(predicted)))),
        'first_shortage': next((x['date'] for x in trajectory if x['stock'] < 0), None), 'updated': now()}


def mount(app, datasets, workflows, lock, save, authorize=lambda request, key: None):
    router = APIRouter(prefix='/planning')
    jobs = {}
    insight_locks = defaultdict(asyncio.Lock)

    async def calculate(key):
        try:
            while key in datasets:
                async with lock:
                    ops, state = context(key)
                    revision = state['revision']
                    snapshot = deepcopy(state)
                    skus = list(ops.products)
                    state['calculation'] = {'status': 'running', 'done': 0, 'total': len(skus)}
                for i, sku in enumerate(skus):
                    try:
                        result = await evaluate(ops, snapshot, sku)
                    except Exception:
                        logger.exception('Forecast calculation failed for dataset=%s sku=%s', key, sku)
                        result = {'sku': sku, 'status': 'blocked', 'reason': '本次计算失败，请重试或检查数据。'}
                    async with lock:
                        if state['revision'] != revision:
                            break
                        state['forecasts'][sku] = {**result, 'revision': revision}
                        state['calculation']['done'] = i+1
                else:
                    async with lock:
                        state['forecast_revision'] = revision
                        state['calculation']['status'] = 'complete'
                        save()
                    return
        finally:
            jobs.pop(key, None)

    def schedule(key):
        state = state_for(workflows, key)
        if state['setup_complete'] and state['forecast_revision'] != state['revision'] and key not in jobs:
            state['calculation'] = {'status': 'running', 'done': 0, 'total': len(datasets[key][0].products)}
            jobs[key] = asyncio.create_task(calculate(key))

    def context(key, request=None):
        if request is not None:
            authorize(request, key)
        if key not in datasets:
            raise HTTPException(404, '请先导入数据')
        state = state_for(workflows, key)
        if datasets[key][1] and not state.get('sample_categories_initialized'):
            groups = {'家居装饰': ['85123A', '71053', '21730', '84879'],
                      '保暖用品': ['84029G', '84029E', '22633', '22632'],
                      '家居收纳': ['84406B', '22752'], '餐厨用品': ['22310', '84969'],
                      '玩具礼品': ['22745', '22748', '22749', '22623']}
            for category, skus in groups.items():
                for sku in skus:
                    if sku in datasets[key][0].products:
                        state['product_settings'].setdefault(sku, {'category': category, 'lead_days': None})
            state['sample_categories_initialized'] = True
        return datasets[key][0], state

    def changed(state, message, event=None, impact='forecast'):
        previous = state['revision']
        if impact != 'order':
            state['revision'] += 1
            state.pop('insight', None)
        if event is not None:
            state['events'].insert(0, {'time': now(), **event})
        for key, value in workflows.items():
            if value is state and key in datasets:
                if impact == 'supply':
                    ops = datasets[key][0]
                    affected = {item['sku'] for item in event['items']}
                    for sku, result in list(state['forecasts'].items()):
                        if result.get('revision') != previous:
                            continue
                        if sku in affected and result.get('status') == 'ready':
                            result = replenish(ops, state, sku, result,
                                [row['demand'] for row in result['daily']], result['lead_days'])
                        state['forecasts'][sku] = {**result, 'revision': state['revision']}
                    if state['forecast_revision'] == previous:
                        state['forecast_revision'] = state['revision']
                if impact != 'order':
                    schedule(key)
        save()

    @router.get('/{key}')
    async def get_state(request: Request, key: str, days: int = 30, category: str | None = None, start: date | None = None, end: date | None = None):
        ops, state = context(key, request)
        schedule(key)
        return {'state': state, 'statistics': statistics(ops, state, days, category, start, end)}

    @router.post('/{key}/start')
    async def start(request: Request, key: str, data: Settings):
        async with lock:
            ops, state = context(key, request)
            state['parameters'] = data.model_dump()
            state['setup_complete'] = True
            changed(state, '完成首次设置，开始自动计算')
        return {'started': True}

    @router.post('/{key}/settings')
    async def settings(request: Request, key: str, data: Settings):
        async with lock:
            ops, state = context(key, request)
            state['parameters'] = data.model_dump()
            changed(state, '更新备货规则')
        return state['parameters']

    @router.post('/{key}/product/{sku}')
    async def product_settings(request: Request, key: str, sku: str, data: ProductSettings):
        async with lock:
            ops, state = context(key, request)
            if sku not in ops.products:
                raise HTTPException(404, '商品不存在')
            state['product_settings'][sku] = data.model_dump()
            changed(state, '更新商品参数：'+sku)
        return {'ok': True}

    @router.post('/{key}/forecast/{sku}')
    async def forecast(request: Request, key: str, sku: str):
        async with lock:
            ops, state = context(key, request)
            if sku not in ops.products:
                raise HTTPException(404, '商品不存在')
            result = await evaluate(ops, state, sku)
            state['forecasts'][sku] = result
            save()
            return result

    @router.post('/{key}/requests')
    async def create(request: Request, key: str, data: NewOrder):
        async with lock:
            ops, state = context(key, request)
            existing = next((o for o in state['requests'] if o['token'] == data.token), None)
            if existing:
                return existing
            skus = [line.sku for line in data.lines]
            if len(skus) != len(set(skus)) or any(s not in ops.products for s in skus):
                raise HTTPException(422, '商品不存在或重复')
            if any(line.due < ops.as_of for line in data.lines):
                raise HTTPException(422, '期望日期不能早于数据截至日')
            if any(line['sku'] in skus for o in state['requests'] if o['status'] in ('draft', 'pending') for line in o['lines']):
                raise HTTPException(409, '已有待确认需求，请先处理原单')
            order = {'id': 'REQ-'+uuid.uuid4().hex[:8].upper(), 'token': data.token, 'status': 'draft', 'created': now(),
                'lines': [{**line.model_dump(mode='json'), 'confirmed': 0, 'received': 0, 'confirmed_due': None} for line in data.lines], 'notes': [], 'receipt_tokens': []}
            state['requests'].insert(0, order)
            changed(state, '', order_event(ops, order, 'create', order['lines']), impact='order')
            return order

    @router.post('/{key}/requests/{order_id}')
    async def action(request: Request, key: str, order_id: str, data: Action):
        async with lock:
            ops, state = context(key, request)
            order = next((o for o in state['requests'] if o['id'] == order_id), None)
            if not order:
                raise HTTPException(404, '需求单不存在')
            if data.action == 'receive' and data.token and data.token in order['receipt_tokens']:
                return order
            prior_status = order['status']
            event_lines = [{'sku': line['sku'], 'quantity': max(0, (line['confirmed'] if order['status'] in ('confirmed', 'partial') else line['quantity']) - line['received'])} for line in order['lines']]
            if data.action == 'submit' and order['status'] == 'draft':
                order['status'] = 'pending'
            elif data.action == 'feedback' and order['status'] == 'pending':
                values = {x.sku: x for x in data.lines}
                if len(values) != len(data.lines) or set(values) != {x['sku'] for x in order['lines']}:
                    raise HTTPException(422, '请填写每个商品的生产反馈')
                if any(x.due < ops.as_of for x in data.lines):
                    raise HTTPException(422, '承诺日期不能早于数据截至日')
                for line in order['lines']:
                    line.update(confirmed=values[line['sku']].quantity, confirmed_due=str(values[line['sku']].due))
                order['status'] = 'confirmed'
            elif data.action == 'receive' and order['status'] in ('confirmed', 'partial'):
                if not data.token or not data.lines or len({x.sku for x in data.lines}) != len(data.lines):
                    raise HTTPException(422, '请填写入库数量，商品不得重复')
                lookup = {x['sku']: x for x in order['lines']}
                for item in data.lines:
                    if item.sku not in lookup or item.quantity > lookup[item.sku]['confirmed']-lookup[item.sku]['received']:
                        raise HTTPException(422, '入库数量超过剩余确认数量')
                    if item.due != ops.as_of:
                        raise HTTPException(422, '本次入库按当前库存截至日登记，历史入库请从台账导入')
                products = dict(ops.products)
                for item in data.lines:
                    p = products[item.sku]
                    closing = p.available_stock+item.quantity
                    products[item.sku] = replace(p, available_stock=closing)
                    lookup[item.sku]['received'] += item.quantity
                    state['receipts'].append({'date': str(item.due), 'sku': item.sku, 'opening': p.available_stock, 'received': item.quantity, 'closing': closing, 'order': order_id})
                datasets[key] = (OperationsTools(list(products.values()), ops.daily, ops.as_of, ops.policy), datasets[key][1])
                order['receipt_tokens'].append(data.token)
                order['status'] = 'completed' if all(x['received'] == x['confirmed'] for x in order['lines']) else 'partial'
            elif data.action == 'cancel' and order['status'] not in ('completed', 'cancelled'):
                order['status'] = 'cancelled'
            else:
                raise HTTPException(409, '单据状态已变化，当前操作不可执行')
            order['notes'].append({'time': now(), 'action': data.action, 'note': data.note})
            if data.action in ('feedback', 'receive'):
                event_lines = [item.model_dump(mode='json') for item in data.lines]
            impact = 'supply' if data.action in ('feedback', 'receive') or (data.action == 'cancel' and prior_status in ('confirmed', 'partial')) else 'order'
            changed(state, '', order_event(ops, order, data.action, event_lines), impact=impact)
            return order

    @router.post('/{key}/insight')
    async def insight(request: Request, key: str, days: int = 30, category: str | None = None, start: date | None = None, end: date | None = None):
        from demo.analytics_agent import analyze
        async with insight_locks[key]:
            async with lock:
                ops, state = context(key, request)
                stats = statistics(ops, state, days, category, start, end)
                revision = state['revision']
                cache_key = f"v3|{revision}|{stats['start']}|{stats['end']}|{category or ''}"
                cached = state.get('insight_cache', {}).get(cache_key)
                if cached:
                    state['insight'] = cached
                    return cached
                synthetic = datasets[key][1]
            try:
                result = await analyze(stats, synthetic)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(502, 'AI 解读暂时未完成，请稍后重试；若持续失败，请检查服务端模型连接。') from exc
            async with lock:
                _, current = context(key)
                if current['revision'] != revision:
                    raise HTTPException(409, '数据已更新，请重新获取分析')
                value = {**result, 'analysis_version': 3, 'revision': revision, 'days': stats['days'], 'start': stats['start'], 'end': stats['end'], 'category': category, 'updated': now()}
                cache = current.setdefault('insight_cache', {})
                cache[cache_key] = value
                while len(cache) > 12:
                    del cache[next(iter(cache))]
                current['insight'] = value
                save()
                return value

    app.include_router(router)
