import pytest
from tests.test_planning_workspace import workspace
from demo.web import workflows


def test_insight_cache_and_revision(workspace, monkeypatch):
    client, key, _ = workspace
    calls = []
    async def fake(stats, synthetic):
        calls.append((stats['start'],stats['end']))
        return {'text':'分析完成','evidence':[], 'trace':[]}
    monkeypatch.setattr('demo.analytics_agent.analyze', fake)
    url=f'/planning/{key}/insight'
    first=client.post(url).json()
    assert first['text']=='分析完成'
    assert client.post(url).json()==first
    assert len(calls)==1
    assert client.post(url+'?days=7').status_code==200
    assert len(calls)==2
    workflows[key]['revision']+=1
    assert client.post(url).json()['revision']==first['revision']+1
    assert len(calls)==3


def test_insight_rejects_stale_result(workspace, monkeypatch):
    client,key,_=workspace
    async def fake(stats, synthetic):
        workflows[key]['revision']+=1
        return {'text':'过期分析','evidence':[], 'trace':[]}
    monkeypatch.setattr('demo.analytics_agent.analyze',fake)
    assert client.post(f'/planning/{key}/insight').status_code==409
    assert not workflows[key].get('insight_cache')


@pytest.mark.asyncio
async def test_model_json_mode_prompt():
    from types import SimpleNamespace
    from demo.model_client import ApiModel
    async def create(**kwargs):
        assert kwargs['response_format']=={'type':'json_object'}
        assert 'json' in kwargs['messages'][0]['content'].lower()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"text":"ok"}'))])
    model=ApiModel.__new__(ApiModel)
    model.model='test'
    model.client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert await model.ask('只返回对象',{})=={'text':'ok'}
