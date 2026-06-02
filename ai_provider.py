"""
AI 内容生成模块
支持多种 AI 服务商，可插拔设计，通过配置切换
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ============================================================
# 抽象基类
# ============================================================

class AIProvider(ABC):
    """AI 服务商抽象基类"""

    @abstractmethod
    def generate_post(
        self,
        topics: list[str],
        style: str = "自然随性",
    ) -> str:
        """
        生成一条微博帖子内容

        Args:
            topics: 话题列表，AI 会围绕这些话题生成内容
            style: 风格描述，如"自然随性"、"专业严谨"、"幽默风趣"

        Returns:
            生成的微博正文（纯文本）
        """
        ...

    @classmethod
    def name(cls) -> str:
        """返回 Provider 名称"""
        return cls.__name__


# ============================================================
# 内置 Provider 实现
# ============================================================

class MimoProvider(AIProvider):
    """小米 Mimo AI"""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.xiaomimimo.com/v1",
        model: str = "mimo-chat",
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model

    @classmethod
    def name(cls) -> str:
        return "mimo"

    def generate_post(
        self,
        topics: list[str],
        style: str = "自然随性",
    ) -> str:
        """调用 Mimo API 生成微博帖子"""
        topic_str = "、".join(topics)
        system_prompt = (
            f"你是一个微博用户，经常参与超话讨论。"
            f"你的发言风格：{style}。"
            f"请生成一条原创微博，围绕以下超话话题：{topic_str}。"
            f"要求：\n"
            f"1. 字数在 50-200 字之间\n"
            f"2. 内容有趣、有观点，避免空洞无物\n"
            f"3. 适当使用微博常用表达（如 emoji、话题标签 #）\n"
            f"4. 不要在回复中包含任何前缀说明，直接输出微博正文\n"
            f"5. 每次生成的内容要有所区别，避免重复"
        )

        try:
            resp = requests.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "请生成一条微博帖子"},
                    ],
                    "temperature": 0.9,
                    "max_tokens": 500,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

            if content:
                logger.info(
                    f"Mimo 生成内容成功，长度: {len(content)} 字"
                )
                return content
            else:
                logger.warning("Mimo 返回内容为空")
                return ""

        except requests.RequestException as e:
            status = getattr(e.response, 'status_code', 'N/A') if hasattr(e, 'response') and e.response else 'N/A'
            logger.error(
                f"Mimo API 请求失败: {e} | status={status}"
            )
            if status == 404:
                logger.error(
                    "Mimo API 返回 404，可能原因："
                    "1) API 地址不正确（当前: %s）"
                    "2) Mimo 服务已下线或变更 "
                    "3) 请检查 AI_API_BASE 环境变量，"
                    "或切换到其他 Provider（设置 AI_PROVIDER=openai）",
                    self.api_base,
                )
            return ""
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"Mimo 响应解析失败: {e}")
            return ""


class ClaudeProvider(AIProvider):
    """Anthropic Claude API"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
    ):
        self.api_key = api_key
        self.model = model

    @classmethod
    def name(cls) -> str:
        return "claude"

    def generate_post(
        self,
        topics: list[str],
        style: str = "自然随性",
    ) -> str:
        """调用 Claude API 生成微博帖子"""
        topic_str = "、".join(topics)

        try:
            from anthropic import Anthropic

            client = Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model=self.model,
                max_tokens=500,
                temperature=0.9,
                system=(
                    f"你是一个微博用户，经常参与超话讨论。"
                    f"你的发言风格：{style}。"
                    f"请生成原创的微博帖子。"
                    f"要求：字数50-200字，内容有趣有观点，"
                    f"适当使用 emoji 和话题标签，"
                    f"直接输出微博正文不要前缀说明"
                ),
                messages=[
                    {
                        "role": "user",
                        "content": f"请围绕这些超话话题生成一条微博：{topic_str}",
                    }
                ],
            )

            content = message.content[0].text.strip()

            if content:
                logger.info(
                    f"Claude 生成内容成功，长度: {len(content)} 字"
                )
                return content
            else:
                logger.warning("Claude 返回内容为空")
                return ""

        except ImportError:
            logger.error(
                "需要安装 anthropic SDK: pip install anthropic"
            )
            return ""
        except Exception as e:
            logger.error(f"Claude API 调用失败: {e}")
            return ""


class OpenAIProvider(AIProvider):
    """OpenAI API（以及兼容 OpenAI 格式的其他服务）"""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model

    @classmethod
    def name(cls) -> str:
        return "openai"

    def generate_post(
        self,
        topics: list[str],
        style: str = "自然随性",
    ) -> str:
        """调用 OpenAI API 生成微博帖子"""
        topic_str = "、".join(topics)

        try:
            resp = requests.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"你是微博用户，发言风格：{style}。"
                                f"生成原创微博，50-200字，有趣有观点，"
                                f"用 emoji 和话题标签，直接输出正文。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"围绕超话话题生成微博：{topic_str}",
                        },
                    ],
                    "temperature": 0.9,
                    "max_tokens": 500,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

            if content:
                logger.info(
                    f"OpenAI 生成内容成功，长度: {len(content)} 字"
                )
                return content
            else:
                logger.warning("OpenAI 返回内容为空")
                return ""

        except requests.RequestException as e:
            status = getattr(e.response, 'status_code', 'N/A') if hasattr(e, 'response') and e.response else 'N/A'
            logger.error(f"OpenAI API 请求失败: {e} | status={status}")
            if status == 404:
                logger.error(
                    "API 返回 404，请检查 AI_API_BASE 是否正确（当前: %s）",
                    self.api_base,
                )
            elif status == 401:
                logger.error(
                    "API Key 无效（401），请检查 AI_API_KEY 环境变量"
                )
            return ""
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"OpenAI 响应解析失败: {e}")
            return ""


# ============================================================
# 工厂函数 & 便捷 API
# ============================================================

# Provider 注册表
PROVIDERS: dict[str, type[AIProvider]] = {
    "mimo": MimoProvider,
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
}


def register_provider(name: str, cls: type[AIProvider]):
    """注册自定义 AI Provider"""
    PROVIDERS[name.lower()] = cls


def create_provider(
    provider: str = "mimo",
    **kwargs,
) -> AIProvider:
    """
    根据名称创建 AI Provider 实例

    Args:
        provider: Provider 名称 (mimo / claude / openai)
        **kwargs: Provider 构造参数

    Returns:
        AIProvider 实例

    Raises:
        ValueError: 未知的 Provider
    """
    cls = PROVIDERS.get(provider.lower())
    if cls is None:
        available = ", ".join(PROVIDERS.keys())
        raise ValueError(
            f"未知的 AI Provider: '{provider}'，可用: {available}"
        )
    return cls(**kwargs)


def create_provider_from_env() -> AIProvider:
    """
    从环境变量自动创建 AI Provider

    环境变量:
        AI_PROVIDER  - Provider 名称 (默认 mimo)
        AI_API_KEY   - API Key
        AI_API_BASE  - API Base URL (可选)
        AI_MODEL     - 模型名称 (可选)

    Returns:
        AIProvider 实例
    """
    provider = os.environ.get("AI_PROVIDER", "mimo")
    api_key = os.environ.get("AI_API_KEY", "")

    if not api_key:
        raise ValueError(
            "未设置 AI_API_KEY 环境变量，请在 GitHub Secrets 或本地环境中配置"
        )

    api_base = os.environ.get("AI_API_BASE")
    model = os.environ.get("AI_MODEL")

    kwargs = {"api_key": api_key}
    if api_base:
        kwargs["api_base"] = api_base
    if model:
        kwargs["model"] = model

    return create_provider(provider, **kwargs)
