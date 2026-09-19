"""Bounded, read-only analytics graph: choose products, inspect evidence, explain."""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from demo.model_client import ApiModel


class AnalysisState(TypedDict, total=False):
    selected: list[str]
    evidence: list[dict]
    text: str
    sales_interpretation: str
    inventory_interpretation: str


async def analyze(stats, synthetic):
    model = ApiModel()
    async def choose(state):
        result = await model.ask('只返回 {"skus":["商品SKU"]}。根据销量、同期库存周转和前期销量，选择最多5个值得进一步查看的商品。不要生成补货或改价建议。',
                                 {'period': [stats['start'], stats['end']], 'products': stats['products']})
        valid = {p['sku'] for p in stats['products']}
        skus = result.get('skus', [])
        if not isinstance(skus, list):
            raise ValueError('模型返回的查询范围无效')
        return {'selected': list(dict.fromkeys(s for s in skus if isinstance(s, str) and s in valid))[:5]}

    async def inspect(state):
        # Only selected product records are exposed. No arbitrary SQL or writes.
        evidence = []
        for sku in state['selected']:
            product = next(p for p in stats['products'] if p['sku'] == sku)
            ledger = [r for r in stats['ledger'] if r['sku'] == sku]
            checked = [r for r in ledger if all(r.get(k) is not None for k in ('opening', 'received', 'sales', 'closing'))]
            balance_errors = [r['date'] for r in checked if r['opening']+r['received']-r['sales'] != r['closing']]
            evidence.append({'product': product,
                'ledger_check': {'checked_rows': len(checked), 'balance_error_dates': balance_errors},
                'daily_sales': [r for r in stats['sales_rows'] if r['sku'] == sku],
                'receipts': [r for r in stats['ledger'] if r['sku'] == sku and r['received']]})
        return {'evidence': evidence}

    async def explain(state):
        prompt = """你是电商经营分析助手。仅根据工具数据，输出销售表现解读和库存情况解读，各一段，合计100—300字。
销售：概括主要商品/品类的销售额贡献及较上一周期的变化，优先关注金额增减贡献，而非仅看增长率。
库存：结合销量、平均库存与周转比较消耗快慢。引用最新库存风险时写“截至当前”；无现货不等于已损失销售。
数字、占比、变化和风险仅引用工具结果，不自行计算，不把缺失当零。上期为零或数据不足时不输出增长百分比。
不编造原因，不建议改价或补货数量，不把高库存直接断言为积压。没有工具判定的积压风险时仅描述库存与消耗表现。
不展示SKU、英文变量名、分析依据、技术口径、模拟数据说明。不泛泛总结或罗列指标。金额用元，数量用件，周转用次。
只返回有效JSON，且仅包含两个非空字符串字段：sales_interpretation、inventory_interpretation。"""
        result = await model.ask(prompt,
            {'period': [stats['start'], stats['end']], 'comparison': stats.get('comparison'),
             'totals': {k: stats.get(k) for k in ('total_revenue','total_sales','daily_revenue','average_stock','turnover')},
             'categories': stats['categories'], 'products': stats['products'],
             'evidence': state['evidence']})
        fields = ('sales_interpretation', 'inventory_interpretation')
        if set(result) != set(fields) or any(not isinstance(result[k], str) or not result[k].strip() for k in fields):
            raise ValueError('解读格式不符合要求，请重试')
        if sum(len(result[k]) for k in fields) > 450:
            raise ValueError('解读过长，请重试')
        return {**result, 'text': '\n\n'.join(result[k] for k in fields)}

    graph = StateGraph(AnalysisState)
    graph.add_node('select_products', choose)
    graph.add_node('inspect_records', inspect)
    graph.add_node('summarize', explain)
    graph.add_edge(START, 'select_products')
    graph.add_edge('select_products', 'inspect_records')
    graph.add_edge('inspect_records', 'summarize')
    graph.add_edge('summarize', END)
    try:
        result = await graph.compile().ainvoke({})
        return {'sales_interpretation': result['sales_interpretation'], 'inventory_interpretation': result['inventory_interpretation'], 'text': result['text'], 'evidence': result['evidence'], 'mode': 'model',
                'trace': ['选择重点商品', '查询销售与入库证据', '生成综合解读']}
    finally:
        await model.client.close()
