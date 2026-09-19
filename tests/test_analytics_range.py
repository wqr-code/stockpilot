from datetime import date
from decimal import Decimal
import pytest
from fastapi import HTTPException
from demo.standard_sample import sample_tools
from demo.planning_workspace import statistics, state_for


def test_custom_range_revenue_and_inclusive_dates():
    ops = sample_tools()
    state = state_for({}, 'test')
    start, end = date(2025, 2, 3), date(2025, 3, 9)
    result = statistics(ops, state, start=start, end=end)
    expected = sum((r.selling_price*r.units for rows in ops.daily.values() for r in rows if start <= r.day <= end), Decimal(0))
    assert result['total_revenue'] == float(expected)
    assert sum(r['revenue'] for r in result['trend']) == pytest.approx(float(expected))
    assert len(result['trend']) == 35
    assert result['start'] == str(start) and result['end'] == str(end)
    assert all(str(start) <= r['date'] <= str(end) for r in result['sales_rows'])
    assert sum(p['revenue'] for p in result['categories']) == pytest.approx(float(expected))
    single = statistics(ops, state, start=start, end=start)
    assert len(single['trend']) == 1


@pytest.mark.parametrize('start,end', [(date(2025, 2, 1), None), (date(2025, 2, 2), date(2025, 2, 1)), (date(2026, 9, 14), date(2026, 9, 14))])
def test_reject_invalid_ranges(start, end):
    with pytest.raises(HTTPException):
        statistics(sample_tools(), state_for({}, 'test'), start=start, end=end)


def test_previous_period_and_changes():
    from demo.planning_workspace import period_statistics, change
    ops=sample_tools()
    state=state_for({},'test')
    result=statistics(ops,state,start=date(2025,3,1),end=date(2025,3,10))
    prior=period_statistics(ops,state,start=date(2025,2,19),end=date(2025,2,28))
    c=result['comparison']
    assert (c['start'],c['end'])==('2025-02-19','2025-02-28')
    assert c['previous']['total_revenue']==prior['total_revenue']
    assert c['changes']['total_revenue']['delta']==round(result['total_revenue']-prior['total_revenue'],2)
    assert sum(p['revenue_change']['delta'] for p in result['categories'])==pytest.approx(c['changes']['total_revenue']['delta'],abs=.02)
    assert sum(p['revenue_share_percent'] for p in result['categories'])==pytest.approx(100,abs=.03)
    assert change(10,0)=={'delta':10,'percent':None,'reason':'上期为零'}
    assert change(10,None)['percent'] is None
