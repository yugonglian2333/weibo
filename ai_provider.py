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
        """调用 Mimo API 生成微博帖子（自动识别 OpenAI / Anthropic 协议）"""
        topic_str = "、".join(topics)
        system_prompt = (
            f"你是一个普通微博用户，喜欢分享日常生活、追星感受和正能量内容。"
            f"你的发言风格：{style}。\n"
            f"请围绕以下超话话题生成一条原创微博：{topic_str}。\n"
            f"要求：\n"
            f"1. 字数在 50-200 字之间\n"
            f"2. 内容风格正面积极，重点表达对该话题/人物的喜爱、赞美、鼓励或分享有趣见闻\n"
            f"3. 用 personal 的语气（如「今天刷到」「真的好喜欢」），增加真实感和亲切感\n"
            f"4. 适当使用 emoji（1-3 个即可）\n"
            f"5. 每次生成的内容要有所区别，避免重复\n"
            f"\n"
            f"【话题标签格式】：\n"
            f"- 微博超话的标签格式为 #话题名[超话]#（方括号内是「超话」二字，这是进入超话内部发帖的关键）\n"
            f"- 示例：如果话题是「鞠婧祎」，标签应写为 #鞠婧祎[超话]# 而不是 #鞠婧祎#\n"
            f"- 必须使用正确的 #[超话]# 格式，否则帖子不会出现在超话内部\n"
            f"\n"
            f"【严格禁止】：\n"
            f"- 绝对禁止将不同明星或艺人放在一起比较（如「A比B好看」「A不如B」）\n"
            f"- 绝对禁止发表任何可能引发粉丝争吵、对立、拉踩的言论\n"
            f"- 绝对禁止使用贬低性、攻击性语言评价任何人物\n"
            f"- 绝对禁止讨论敏感话题（政治、宗教、社会争议等）\n"
            f"- 内容应体现对该话题对象的单纯喜爱和支持，不拉踩、不引战\n"
            f"\n"
            f"【输出格式】：直接输出微博正文，不要在回复中包含任何前缀说明"
        )

        # 根据 API Base URL 自动识别协议类型
        # https://.../anthropic  → Anthropic 协议
        # https://.../v1         → OpenAI 协议
        is_anthropic = self.api_base.endswith("/anthropic")

        # 构建两种协议的请求配置（404 时自动切换）
        protocols = []
        if is_anthropic:
            # 优先 Anthropic 协议，备选 OpenAI 协议（去掉 /anthropic 后缀加 /v1）
            protocols.append({
                "name": "Anthropic",
                "url": f"{self.api_base}/messages",
                "payload": {
                    "model": self.model,
                    "max_tokens": 4096,
                    "temperature": 0.9,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": "请生成一条微博帖子"},
                    ],
                },
                "is_anthropic": True,
            })
            # 备选: 把 /anthropic 替换为 /v1，走 OpenAI 协议
            openai_base = self.api_base.replace("/anthropic", "/v1")
            protocols.append({
                "name": "OpenAI(备选)",
                "url": f"{openai_base}/chat/completions",
                "payload": {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "请生成一条微博帖子"},
                    ],
                    "temperature": 0.9,
                    "max_tokens": 4096,
                },
                "is_anthropic": False,
            })
        else:
            protocols.append({
                "name": "OpenAI",
                "url": f"{self.api_base}/chat/completions",
                "payload": {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "请生成一条微博帖子"},
                    ],
                    "temperature": 0.9,
                    "max_tokens": 4096,
                },
                "is_anthropic": False,
            })

        # 依次尝试每种协议（遇到 404 自动切换备选）
        last_error = None
        for proto in protocols:
            logger.info(f"尝试 {proto['name']} 协议: {proto['url']}")
            try:
                resp = requests.post(
                    proto["url"],
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=proto["payload"],
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()

                # 解析响应
                if proto["is_anthropic"]:
                    content = (
                        data.get("content", [{}])[0]
                        .get("text", "")
                        .strip()
                    )
                else:
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                        .strip()
                    )

                if content:
                    logger.info(
                        f"Mimo ({proto['name']}) 生成内容成功，长度: {len(content)} 字"
                    )
                    return content
                else:
                    # 推理模型（如 DeepSeek-R1）可能把内容放在 reasoning_content 里
                    msg = data.get("choices", [{}])[0].get("message", {})
                    reasoning = msg.get("reasoning_content", "")
                    if reasoning:
                        logger.info(
                            f"Mimo ({proto['name']}) 从 reasoning_content 提取内容，"
                            f"长度: {len(reasoning)} 字"
                        )
                        return reasoning.strip()
                    # 内容为空，打印响应结构帮助排查
                    logger.warning(
                        f"Mimo ({proto['name']}) 返回内容为空，"
                        f"响应结构: {str(data)[:500]}"
                    )
                    return ""

            except requests.RequestException as e:
                last_error = e
                # 获取 HTTP 状态码
                status = None
                try:
                    if e.response is not None:
                        status = e.response.status_code
                except Exception:
                    pass

                if status == 404:
                    logger.warning(
                        f"{proto['name']} 协议 404，URL 不可用，尝试下一个..."
                    )
                    continue
                else:
                    # 非 404 错误（如 401, 500 等）不重试
                    break

        # 所有协议都失败
        status_msg = ""
        if last_error is not None:
            try:
                if last_error.response is not None:
                    status_msg = f" | status={last_error.response.status_code}"
            except Exception:
                pass
        logger.error(f"Mimo API 请求失败: {last_error}{status_msg}")
        if status_msg and "401" in status_msg:
            logger.error("Mimo API Key 无效（401），请检查 AI_API_KEY 环境变量")
        elif status_msg and "404" in status_msg:
            logger.error("所有协议均返回 404，请检查 AI_API_BASE 是否正确")
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
                    f"你是一个普通微博用户，喜欢分享日常生活、追星感受和正能量内容。"
                    f"你的发言风格：{style}。\n"
                    f"请围绕以下超话话题生成一条原创微博：{topic_str}。\n"
                    f"要求：\n"
                    f"1. 字数在 50-200 字之间\n"
                    f"2. 内容风格正面积极，重点表达对该话题/人物的喜爱、赞美、鼓励或分享有趣见闻\n"
                    f"3. 用 personal 的语气（如「今天刷到」「真的好喜欢」），增加真实感和亲切感\n"
                    f"4. 适当使用 emoji（1-3 个即可）\n"
                    f"5. 每次生成的内容要有所区别，避免重复\n"
                    f"\n"
                    f"【话题标签格式】：\n"
                    f"- 微博超话的标签格式为 #话题名[超话]#（方括号内是「超话」二字，这是进入超话内部发帖的关键）\n"
                    f"- 示例：如果话题是「鞠婧祎」，标签应写为 #鞠婧祎[超话]# 而不是 #鞠婧祎#\n"
                    f"- 必须使用正确的 #[超话]# 格式，否则帖子不会出现在超话内部\n"
                    f"\n"
                    f"【严格禁止】：\n"
                    f"- 绝对禁止将不同明星或艺人放在一起比较（如「A比B好看」「A不如B」）\n"
                    f"- 绝对禁止发表任何可能引发粉丝争吵、对立、拉踩的言论\n"
                    f"- 绝对禁止使用贬低性、攻击性语言评价任何人物\n"
                    f"- 绝对禁止讨论敏感话题（政治、宗教、社会争议等）\n"
                    f"- 内容应体现对该话题对象的单纯喜爱和支持，不拉踩、不引战\n"
                    f"\n"
                    f"【输出格式】：直接输出微博正文，不要在回复中包含任何前缀说明"
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
                                f"你是一个普通微博用户，喜欢分享日常生活、追星感受和正能量内容。"
                                f"你的发言风格：{style}。\n"
                                f"请围绕以下超话话题生成一条原创微博：{topic_str}。\n"
                                f"要求：\n"
                                f"1. 字数在 50-200 字之间\n"
                                f"2. 内容风格正面积极，重点表达对该话题/人物的喜爱、赞美、鼓励或分享有趣见闻\n"
                                f"3. 用 personal 的语气（如「今天刷到」「真的好喜欢」），增加真实感和亲切感\n"
                                f"4. 适当使用 emoji（1-3 个即可）\n"
                                f"5. 每次生成的内容要有所区别，避免重复\n"
                                f"\n"
                                f"【话题标签格式】：\n"
                                f"- 微博超话的标签格式为 #话题名[超话]#（方括号内是「超话」二字，这是进入超话内部发帖的关键）\n"
                                f"- 示例：如果话题是「鞠婧祎」，标签应写为 #鞠婧祎[超话]# 而不是 #鞠婧祎#\n"
                                f"- 必须使用正确的 #[超话]# 格式，否则帖子不会出现在超话内部\n"
                                f"\n"
                                f"【严格禁止】：\n"
                                f"- 绝对禁止将不同明星或艺人放在一起比较（如「A比B好看」「A不如B」）\n"
                                f"- 绝对禁止发表任何可能引发粉丝争吵、对立、拉踩的言论\n"
                                f"- 绝对禁止使用贬低性、攻击性语言评价任何人物\n"
                                f"- 绝对禁止讨论敏感话题（政治、宗教、社会争议等）\n"
                                f"- 内容应体现对该话题对象的单纯喜爱和支持，不拉踩、不引战\n"
                                f"\n"
                                f"【输出格式】：直接输出微博正文，不要在回复中包含任何前缀说明"
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"围绕超话话题生成微博：{topic_str}",
                        },
                    ],
                    "temperature": 0.9,
                    "max_tokens": 4096,
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
