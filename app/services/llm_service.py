# app/services/llm_service.py
import logging
import json
from openai import AsyncOpenAI
from app.core.config import settings

logger = logging.getLogger(__name__)

class LLMService:
    def __init__(self):
        self.mode = settings.LLM_MODE.lower()
        if self.mode == "real":
            self.client = AsyncOpenAI(
                api_key=settings.API_KEY,
                base_url=settings.BASE_URL
            )
            logger.info(f"✅ LLM 真实引擎已启动 (Base URL: {settings.BASE_URL})")
        else:
            self.client = None
            logger.warning("⚠️ LLM Mock 引擎已启动，将返回模拟数据")

    async def async_generate(self, prompt: str, system_prompt: str = "你是一个专业的AI法律顾问。") -> str:
        """核心生成方法，支持双擎切换"""
        if self.mode == "mock":
            return self._mock_response(prompt)
            
        try:
            response = await self.client.chat.completions.create(
                model="qwen-plus", # 可根据实际部署的模型名称调整
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=1024
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ LLM 调用失败: {e}")
            return f"系统繁忙，请稍后再试。(Error: {str(e)})"

    def _mock_response(self, prompt: str) -> str:
        """智能 Mock 数据返回"""
        if "意图" in prompt or "路由" in prompt:
            return "vector_researcher"
        elif "提取" in prompt or "实体" in prompt:
            return json.dumps({"entities": ["张三", "李四"], "relations": ["借贷"]})
        else:
            return "<legal_analysis>这是Mock的法律分析...</legal_analysis>\n<final_advice>建议收集证据并起诉。</final_advice>"

llm_service = LLMService()