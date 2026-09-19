"""Tests must never use the developer's model credentials or paid API."""
import pytest


@pytest.fixture(autouse=True)
def block_live_model(monkeypatch):
    async def blocked(*args, **kwargs):
        raise AssertionError('Live model calls are forbidden in tests; mock the model')
    monkeypatch.setattr('openai.resources.chat.completions.AsyncCompletions.create', blocked)
