"""Optional OpenAI-compatible model client for read-only business analysis."""
import json
import os
from pathlib import Path
from dotenv import load_dotenv


class ApiModel:
    def __init__(self):
        from openai import AsyncOpenAI
        load_dotenv(Path(__file__).resolve().parents[1] / '.env.demo')
        key, self.model = os.getenv('DEMO_API_KEY'), os.getenv('DEMO_MODEL')
        if not key or not self.model:
            raise ValueError('请在本地 .env.demo 配置 DEMO_API_KEY 和 DEMO_MODEL。')
        self.client = AsyncOpenAI(api_key=key,
            base_url=os.getenv('DEMO_BASE_URL') or 'https://api.openai.com/v1',
            timeout=60, max_retries=1)

    async def ask(self, instruction, data):
        response = await self.client.chat.completions.create(model=self.model,
            response_format={'type': 'json_object'}, messages=[
                {'role': 'system', 'content': '你是 StockPilot 的经营分析助手，只使用提供的数据，不编造原因或数字。使用中文，仅返回有效 JSON 对象。\n'+instruction},
                {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}])
        result = json.loads(response.choices[0].message.content)
        if not isinstance(result, dict):
            raise ValueError('模型必须返回 JSON 对象')
        return result
