"""LLM 调用和系统提示词获取。"""
from astrbot.api import logger


class LLMMixin:
    """封装 AstrBot LLM 调用。"""

    async def _llm_call(self, prompt, system_prompt="", max_tokens=300, provider_id=None):
        try:
            pid = provider_id if provider_id is not None else self.config.get("LLM_PROVIDER_ID", "")
            # 人设走真正的 system role：① 增强人设遵循 ② 让人设成为稳定前缀，命中提示词缓存
            kwargs = {"prompt": prompt}
            if system_prompt:
                kwargs["system_prompt"] = system_prompt
            if pid:
                kwargs["chat_provider_id"] = pid
            resp = await self.context.llm_generate(**kwargs)
            return resp.completion_text.strip() if resp and resp.completion_text else None
        except Exception as e:
            logger.error(f"[BiliBot] LLM 调用失败: {e}")
            return None

    async def _get_system_prompt(self):
        # 强制使用 AstrBot 默认人设系统：
        # 通过 get_using_provider() 获取默认 provider，不覆盖 system_prompt，
        # 让 AstrBot 自带的人设 prompt 自然生效。
        # 不再支持自定义系统提示词分支。
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                logger.warning("[BiliBot] 未获取到默认 provider，将不设置 system_prompt（仍由 AstrBot 人设兜底）")
        except Exception as e:
            logger.warning(f"[BiliBot] 获取默认 provider 失败，将不设置 system_prompt: {e}")
        # 不覆盖 system_prompt，交由 AstrBot 默认人设系统处理
        return ""
