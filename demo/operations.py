"""Cost-free, deterministic read tools for the next operations graph.

No model or network access. Readiness permits model evaluation, not publication
of an uplift estimate. Thresholds are demo policy, not industry standards.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    launched_on: date
    current_price: Decimal
    price_floor: Decimal | None
    available_stock: int
    active: bool = True


@dataclass(frozen=True)
class DailySale:
    day: date
    units: int
    selling_price: Decimal
    in_stock: bool
    promotion_id: str | None = None
    opening_stock: int | None = None
    received_units: int | None = None
    closing_stock: int | None = None


@dataclass(frozen=True)
class Policy:
    observation_days: int = 30
    new_product_days: int = 28
    high_coverage_days: int = 90
    training_days: int | None = 365  # None selects all available history.
    minimum_history_days: int = 56
    minimum_promotion_days: int = 7
    minimum_regular_days: int = 28


class OperationsTools:
    def __init__(self, products: list[Product], daily: dict[str, list[DailySale]],
                 as_of: date, policy: Policy = Policy()):
        self.products = {p.sku: p for p in products}
        if len(self.products) != len(products):
            raise ValueError('duplicate SKU')
        if set(daily) - set(self.products):
            raise ValueError('sales reference unknown SKU')
        for p in products:
            if p.available_stock < 0 or not p.current_price.is_finite() or p.current_price <= 0:
                raise ValueError('invalid stock or price')
            if p.price_floor is not None and (not p.price_floor.is_finite() or p.price_floor <= 0):
                raise ValueError('invalid price floor')
        self.daily = {sku: sorted(rows, key=lambda r: r.day) for sku, rows in daily.items()}
        for rows in self.daily.values():
            if len({r.day for r in rows}) != len(rows):
                raise ValueError('duplicate daily observation')
            for r in rows:
                if type(r.units) is not int or r.units < 0 or not r.selling_price.is_finite() or r.selling_price <= 0:
                    raise ValueError('invalid daily sales')
                if r.day >= as_of:
                    raise ValueError('only completed days before as_of are accepted')
        self.as_of, self.policy = as_of, policy

    def _window(self, sku: str, days: int) -> tuple[list[DailySale], int]:
        self.products[sku]
        start = max(self.as_of - timedelta(days=days), self.products[sku].launched_on)
        rows = [r for r in self.daily.get(sku, []) if start <= r.day < self.as_of]
        return rows, max(0, (self.as_of - start).days) - len(rows)

    def inspect_product(self, sku: str) -> dict:
        p = self.products[sku]
        rows, missing = self._window(sku, self.policy.observation_days)
        units = sum(r.units for r in rows)
        average = units / len(rows) if rows and not missing else None
        coverage = p.available_stock / average if average else None
        reasons = []
        if not p.active:
            status = 'listing_review'
            reasons.append('商品未处于在售状态，先检查上架情况。')
        elif (self.as_of - p.launched_on).days < self.policy.new_product_days:
            status = 'new_product'
            reasons.append('新品观察期，不按滞销处理。')
        elif missing or not rows:
            status = 'insufficient_data'
            reasons.append('历史日期不完整，缺失记录不能当成零销量。')
        elif p.available_stock == 0 or any(not r.in_stock for r in rows):
            status = 'availability_review'
            reasons.append('当前或观察期存在缺货，销量不足不能直接归因于需求。')
        elif units == 0 or (coverage is not None and coverage > self.policy.high_coverage_days):
            status = 'slow_moving_candidate'
            reasons.append('库存相对近期销量偏多，建议排查并参考历史活动；不自动降价。')
        else:
            status = 'monitor'
            reasons.append('未触发本次示例筛查规则。')
        return {'sku': sku, 'status': status, 'reasons': reasons,
                'observed_units': units, 'missing_days': missing,
                'average_daily_units': average, 'available_stock': p.available_stock,
                'stock_coverage_days': coverage,
                'window_start': str(self.as_of - timedelta(days=self.policy.observation_days)),
                'window_end_exclusive': str(self.as_of)}

    def training_history(self, sku: str) -> tuple[list[DailySale], int]:
        """Use available history, without treating an unrecorded early era as zeros."""
        rows = self.daily.get(sku, [])
        if not rows:
            return [], 0
        start = max(rows[0].day, self.products[sku].launched_on)
        limit = self.policy.training_days
        if limit is not None:
            if type(limit) is not int or limit <= 0:
                raise ValueError('training_days must be positive or None')
            start = max(start, self.as_of - timedelta(days=limit))
        selected = [r for r in rows if start <= r.day < self.as_of]
        return selected, max(0, (self.as_of - start).days) - len(selected)

    def activity_history(self, sku: str) -> dict:
        self.products[sku]
        groups: dict[str, list[DailySale]] = {}
        for r in self.daily.get(sku, []):
            if r.promotion_id:
                groups.setdefault(r.promotion_id, []).append(r)
        return {'sku': sku, 'interpretation': '历史活动表现，不是活动因果增量',
                'activities': [{'promotion_id': key, 'first_observed_day': str(rows[0].day),
                    'last_observed_day': str(rows[-1].day), 'observed_days': len(rows),
                    'units': sum(r.units for r in rows),
                    'average_daily_units': sum(r.units for r in rows) / len(rows),
                    'observed_prices': sorted({str(r.selling_price) for r in rows}),
                    'stockout_days': sum(not r.in_stock for r in rows)}
                    for key, rows in sorted(groups.items())]}

    def forecast_readiness(self, sku: str, proposed_price: Decimal) -> dict:
        p, policy = self.products[sku], self.policy
        if not proposed_price.is_finite() or proposed_price <= 0:
            raise ValueError('invalid proposed price')
        rows, missing = self.training_history(sku)
        reasons = []
        if (self.as_of - p.launched_on).days < policy.new_product_days:
            reasons.append('new_product')
        if not p.active or p.available_stock == 0:
            reasons.append('not_available_for_promotion')
        if p.price_floor is None:
            reasons.append('missing_price_floor')
        elif proposed_price < p.price_floor:
            reasons.append('below_price_floor')
        if proposed_price >= p.current_price:
            reasons.append('not_a_markdown')
        if missing or len(rows) < policy.minimum_history_days:
            reasons.append('insufficient_complete_history')
        if any(not r.in_stock for r in rows):
            reasons.append('stockout_contaminated_history')
        promo = [r for r in rows if r.promotion_id]
        regular = [r for r in rows if not r.promotion_id]
        if len(promo) < policy.minimum_promotion_days or len(regular) < policy.minimum_regular_days:
            reasons.append('insufficient_promotion_comparison')
        if len({r.selling_price for r in rows}) < 2:
            reasons.append('no_price_variation')
        # Conservative first version: no extrapolation to an unobserved discount.
        if proposed_price not in {r.selling_price for r in promo}:
            reasons.append('proposed_price_not_observed_in_promotion')
        if sum(r.units for r in rows) == 0:
            reasons.append('no_sales_signal')
        status: Literal['blocked', 'ready_for_backtest'] = 'blocked' if reasons else 'ready_for_backtest'
        return {'sku': sku, 'status': status, 'reasons': reasons,
                'forecast': None, 'estimated_incremental_units': None,
                'next_step': '仅给出历史参考或观察建议' if reasons else
                    '训练并进行时间滚动回测；通过后才可展示情景预测，不能视为因果效应'}


if __name__ == '__main__':
    import json
    from demo.operations_sample import sample_tools
    tools = sample_tools()
    print(json.dumps({'mode': 'OFFLINE READ TOOLS — NO LLM OR FORECAST MODEL',
        'products': [tools.inspect_product(sku) for sku in tools.products],
        'history': tools.activity_history('HISTORY'),
        'forecast_readiness': tools.forecast_readiness('HISTORY', Decimal('90.00'))},
        ensure_ascii=False, indent=2))
