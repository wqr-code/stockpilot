"""Single-SKU adaptation of hsilvosa/retail-demand-forecasting algorithms.
See ALGORITHM_REFERENCE.md for provenance and differences.
"""
import math
import numpy as np
from datetime import timedelta

NAMES = ('seasonal_naive', 'moving_average', 'lightgbm', 'xgboost')


def features(y, origin, horizon, start):
    past = y[:origin]
    day = start + timedelta(days=origin + horizon - 1)
    return [horizon, day.weekday(), day.month,
            *[past[-lag] for lag in (1, 7, 14, 28)],
            *[float(past[-w:].mean()) for w in (7, 28)],
            float(past[-28:].std())]


def predict(name, y, start, h):
    if name == 'seasonal_naive':
        return np.resize(y[-7:], h)
    if name == 'moving_average':
        return np.full(h, y[-28:].mean())
    x, target = [], []
    # Direct multi-step model: all labels must precede this forecast origin.
    for origin in range(28, len(y)):
        for step in range(1, min(h, len(y)-origin)+1):
            x.append(features(y, origin, step, start))
            target.append(y[origin+step-1])
    if not any(target):
        return np.zeros(h)
    if name == 'lightgbm':
        from lightgbm import LGBMRegressor
        model = LGBMRegressor(objective='tweedie', tweedie_variance_power=1.2,
            n_estimators=80, learning_rate=.04, num_leaves=15,
            reg_lambda=1., n_jobs=1, random_state=42, verbosity=-1)
    else:
        from xgboost import XGBRegressor
        model = XGBRegressor(objective='reg:tweedie', tweedie_variance_power=1.2,
            n_estimators=80, learning_rate=.04, max_depth=4,
            reg_lambda=1., n_jobs=1, random_state=42, tree_method='hist')
    model.fit(np.asarray(x), np.asarray(target))
    result = np.maximum(model.predict(np.asarray([features(y,len(y),j,start) for j in range(1,h+1)])),0)
    if not np.isfinite(result).all():
        raise ValueError('模型返回非有限预测值')
    return result


def bootstrap_target(point, residuals, service_level):
    blocks = np.asarray(residuals)
    if blocks.ndim != 2 or blocks.shape[1] != len(point) or not np.isfinite(blocks).all():
        raise ValueError('校准误差必须覆盖完整保护期')
    rng = np.random.default_rng(42)
    draws = blocks[rng.integers(0,len(blocks),size=1000)]
    totals = np.maximum(np.asarray(point)[None,:] + draws, 0).sum(axis=1)
    return math.ceil(max(float(np.sum(point)), float(np.quantile(totals,service_level))))


def compare_and_calibrate(rows, h, service_level):
    # Separate contiguous blocks for selection, calibration, and final testing.
    n = len(rows)
    if n < 56 + 16*h:
        return {'status':'blocked','reason':f'保护期 {h} 天需要至少 {56+16*h} 天连续记录（含 12 段独立校准窗口），当前 {n} 天。'}
    y = np.asarray([r.units for r in rows],dtype=float)
    start = rows[0].day
    calibration_start = n - 13*h
    selection_origins = [calibration_start-3*h, calibration_start-2*h, calibration_start-h]
    comparison=[]
    for name in NAMES:
        errors=[];daily=[]
        for origin in selection_origins:
            estimate=predict(name,y[:origin],start,h)
            actual=y[origin:origin+h]
            errors.append(abs(float(actual.sum()-estimate.sum())))
            daily.extend(np.abs(actual-estimate).tolist())
        comparison.append({'model':name,'protection_total_mae':float(np.mean(errors)),
                           'daily_mae':float(np.mean(daily))})
    winner=min(comparison,key=lambda r:r['protection_total_mae'])['model']
    residuals=[]
    for origin in range(calibration_start,n-h,h):
        residuals.append(y[origin:origin+h]-predict(winner,y[:origin],start,h))
    held=predict(winner,y[:-h],start,h)
    held_target=bootstrap_target(held,residuals,service_level)
    actual=y[-h:]
    point=predict(winner,y,start,h)
    return {'status':'ready','model':winner,'comparison':comparison,
        'holdout_mae':float(np.abs(actual-held).mean()),'holdout_days':h,
        'holdout_total_error':float(actual.sum()-held.sum()),
        'holdout_target_covers_demand':bool(held_target>=actual.sum()),
        'calibration_blocks':len(residuals),'bootstrap_paths':1000,
        'calibration_warning':'12 个历史块仅供初步估计；抽样次数不增加独立历史样本，尾部风险仍不稳定。',
        'selection_end':str(rows[calibration_start-1].day),
        'calibration_end':str(rows[n-h-1].day),'holdout_start':str(rows[n-h].day),
        'target_stock':bootstrap_target(point,residuals,service_level),'prediction':point.tolist()}
