"""
通知推送模块
支持多种通知渠道，可插拔设计，通过配置切换
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

class Notifier(ABC):
    """通知渠道抽象基类"""

    @abstractmethod
    def send(self, title: str, content: str) -> bool:
        """
        发送一条通知

        Args:
            title: 通知标题
            content: 通知正文（支持纯文本，部分渠道支持 Markdown）

        Returns:
            True 表示发送成功
        """
        ...

    @classmethod
    def name(cls) -> str:
        """返回渠道名称"""
        return cls.__name__


# ============================================================
# 内置 Notifier 实现
# ============================================================

class PushPlusNotifier(Notifier):
    """
    PushPlus 推送（微信公众号）
    官网: https://www.pushplus.plus/

    使用步骤:
    1. 访问 pushplus.plus 微信扫码登录
    2. 在「发送消息」→「一对一发送」页面获取 Token
    3. 设置环境变量 NOTIFY_TOKEN=你的Token
    """

    def __init__(self, token: str):
        self.token = token

    @classmethod
    def name(cls) -> str:
        return "pushplus"

    def send(self, title: str, content: str) -> bool:
        """通过 PushPlus 发送通知"""
        try:
            resp = requests.post(
                "http://www.pushplus.plus/send",
                json={
                    "token": self.token,
                    "title": title,
                    "content": content,
                    "template": "txt",  # txt 模板，纯文本
                },
                timeout=15,
            )
            data = resp.json()
            code = data.get("code", -1)
            if code == 200:
                logger.info(f"PushPlus 通知发送成功")
                return True
            else:
                msg = data.get("msg", "未知错误")
                logger.error(f"PushPlus 通知发送失败: code={code}, msg={msg}")
                return False

        except requests.RequestException as e:
            logger.error(f"PushPlus 请求失败: {e}")
            return False
        except ValueError as e:
            logger.error(f"PushPlus 响应解析失败: {e}")
            return False


class ServerChanNotifier(Notifier):
    """
    Server酱 推送（微信公众号）
    官网: https://sct.ftqq.com/

    使用步骤:
    1. 访问 sct.ftqq.com 微信扫码登录
    2. 在「SendKey」页面获取 SendKey
    3. 设置环境变量 NOTIFY_TOKEN=你的SendKey
    """

    def __init__(self, sendkey: str):
        self.sendkey = sendkey

    @classmethod
    def name(cls) -> str:
        return "serverchan"

    def send(self, title: str, content: str) -> bool:
        """通过 Server酱 发送通知"""
        try:
            resp = requests.post(
                f"https://sctapi.ftqq.com/{self.sendkey}.send",
                data={
                    "title": title,
                    "desp": content,
                },
                timeout=15,
            )
            data = resp.json()
            code = data.get("code", -1)
            if code == 0:
                logger.info(f"Server酱 通知发送成功")
                return True
            else:
                errmsg = data.get("message", data.get("info", "未知错误"))
                logger.error(f"Server酱 通知发送失败: code={code}, msg={errmsg}")
                return False

        except requests.RequestException as e:
            logger.error(f"Server酱 请求失败: {e}")
            return False
        except ValueError as e:
            logger.error(f"Server酱 响应解析失败: {e}")
            return False


class TelegramNotifier(Notifier):
    """
    Telegram Bot 推送

    使用步骤:
    1. 在 Telegram 中向 @BotFather 创建一个 Bot，获取 Bot Token
    2. 向你的 Bot 发送一条消息
    3. 访问 https://api.telegram.org/bot<TOKEN>/getUpdates 获取 chat_id
    4. 设置环境变量:
       NOTIFY_TOKEN=bot<TOKEN> （Bot Token）
       NOTIFY_CHAT_ID=<你的chat_id>
    """

    def __init__(self, bot_token: str, chat_id: str = ""):
        self.bot_token = bot_token
        # chat_id 优先从参数取，否则从环境变量取
        self.chat_id = chat_id or os.environ.get("NOTIFY_CHAT_ID", "")

    @classmethod
    def name(cls) -> str:
        return "telegram"

    def send(self, title: str, content: str) -> bool:
        """通过 Telegram Bot 发送通知"""
        if not self.chat_id:
            logger.error(
                "Telegram 通知需要 chat_id，请设置 NOTIFY_CHAT_ID 环境变量"
            )
            return False

        # 合并标题和内容
        message = f"<b>{title}</b>\n\n{content}"

        try:
            resp = requests.post(
                f"https://api.telegram.org/{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                logger.info(f"Telegram 通知发送成功")
                return True
            else:
                errmsg = data.get("description", "未知错误")
                logger.error(f"Telegram 通知发送失败: {errmsg}")
                return False

        except requests.RequestException as e:
            logger.error(f"Telegram 请求失败: {e}")
            return False
        except ValueError as e:
            logger.error(f"Telegram 响应解析失败: {e}")
            return False


# ============================================================
# 工厂函数 & 便捷 API
# ============================================================

# Notifier 注册表
NOTIFIERS: dict[str, type[Notifier]] = {
    "pushplus": PushPlusNotifier,
    "serverchan": ServerChanNotifier,
    "telegram": TelegramNotifier,
}


def register_notifier(name: str, cls: type[Notifier]):
    """注册自定义通知渠道"""
    NOTIFIERS[name.lower()] = cls


def create_notifier(
    provider: str = "pushplus",
    **kwargs,
) -> Notifier:
    """
    根据名称创建 Notifier 实例

    Args:
        provider: 渠道名称 (pushplus / serverchan / telegram)
        **kwargs: 渠道构造参数

    Returns:
        Notifier 实例

    Raises:
        ValueError: 未知的渠道
    """
    cls = NOTIFIERS.get(provider.lower())
    if cls is None:
        available = ", ".join(NOTIFIERS.keys())
        raise ValueError(
            f"未知的通知渠道: '{provider}'，可用: {available}"
        )
    return cls(**kwargs)


def create_notifier_from_env() -> Optional[Notifier]:
    """
    从环境变量自动创建 Notifier

    环境变量:
        NOTIFY_PROVIDER  - 通知渠道 (默认 pushplus)
        NOTIFY_TOKEN     - 渠道 Token / SendKey
        NOTIFY_CHAT_ID   - Telegram chat_id (仅 Telegram 需要)

    Returns:
        Notifier 实例，如果未配置 token 则返回 None
    """
    token = os.environ.get("NOTIFY_TOKEN", "")
    if not token:
        logger.info("未设置 NOTIFY_TOKEN，跳过通知")
        return None

    provider = os.environ.get("NOTIFY_PROVIDER", "pushplus")

    kwargs = {}
    if provider.lower() in ("pushplus", "serverchan"):
        if provider.lower() == "pushplus":
            kwargs["token"] = token
        else:
            kwargs["sendkey"] = token
    elif provider.lower() == "telegram":
        kwargs["bot_token"] = token
    else:
        kwargs["token"] = token

    return create_notifier(provider, **kwargs)


# ============================================================
# 便捷函数：构建通知内容
# ============================================================

def build_notification(
    checkin_results: list[dict],
    post_results: Optional[list[dict]] = None,
    comment_results: Optional[list[dict]] = None,
    fudai_results: Optional[dict] = None,
) -> tuple[str, str]:
    """
    根据签到、评论、发帖和福袋结果构建通知标题和内容

    Args:
        checkin_results: 签到结果列表
        post_results: 发帖结果列表（可为 None 或空列表）
        comment_results: 评论结果列表（可为 None 或空列表）
        fudai_results: 福袋任务结果（可为 None）

    Returns:
        (title, content) 元组
    """
    lines = []

    # ---- 福袋报告（放在最前面） ----
    if fudai_results:
        fudai_summary = fudai_results.get("summary", {})
        fudai_topics = fudai_results.get("topics", [])

        lines.append("🎁 周三福袋任务报告")
        lines.append("━━━━━━━━━━━━━━")

        if fudai_summary:
            total = fudai_summary.get("total", 0)
            ck_ok = fudai_summary.get("checkin_success", 0)
            cs_ok = fudai_summary.get("consume_success", 0)
            pt_ok = fudai_summary.get("post_success", 0)
            ia_ok = fudai_summary.get("interact_success", 0)

            lines.append(
                f"🎫 签到福袋: {ck_ok}/{total} "
                f"{'✅' if ck_ok == total else '⚠️'}"
            )
            lines.append(
                f"📖 消费福袋: {cs_ok}/{total} "
                f"{'✅' if cs_ok == total else '⚠️'}"
            )
            lines.append(
                f"📝 发帖福袋: {pt_ok}/{total} "
                f"{'✅' if pt_ok == total else '⚠️'}"
            )
            lines.append(
                f"💬 互动福袋: {ia_ok}/{total} "
                f"{'✅' if ia_ok == total else '⚠️'}"
            )

        # 详细列出每个超话
        for tr in fudai_topics:
            topic = tr.get("topic", "未知")
            error = tr.get("error", "")
            if error:
                lines.append(f"  ❌ {topic}: {error}")
                continue
            ck = "✅" if (tr.get("checkin") or {}).get("success") else "❌"
            cs = "✅" if (tr.get("consume") or {}).get("success") else "❌"
            pt = "✅" if (tr.get("post") or {}).get("success") else "❌"
            ia = "✅" if (tr.get("interact") or {}).get("success") else "❌"
            ia_method = (tr.get("interact") or {}).get("method", "")
            ia_label = {
                "comment": "评",
                "repost": "转",
                "none": "-",
            }.get(ia_method, ia_method)
            lines.append(
                f"  {topic}: 签到{ck} 消费{cs} 发帖{pt} 互动({ia_label}){ia}"
            )

        lines.append("━━━━━━━━━━━━━━")
        lines.append("")

    # ---- 常规报告 ----
    lines.append("📋 微博签到发帖报告")
    lines.append("━━━━━━━━━━━━━━")

    # 签到汇总
    if checkin_results:
        total = len(checkin_results)
        success = sum(1 for r in checkin_results if r["success"])
        emoji = "✅" if success == total else "⚠️"
        lines.append(f"{emoji} 签到: {success}/{total} 成功")
        for r in checkin_results:
            name = r.get("name", "未知")
            msg = r.get("message", "")
            # 精简消息，只取核心信息
            if "签到成功" in msg:
                lines.append(f"  • {name} ✅ 签到成功")
            elif "已签到" in msg:
                lines.append(f"  • {name} ⏭ 今日已签到")
            else:
                lines.append(f"  • {name} ❌ 签到失败")
    else:
        lines.append("📭 签到: 未配置")

    # 评论汇总
    if comment_results:
        total_c = sum(len(r.get("comments", [])) for r in comment_results)
        success_c = sum(
            sum(1 for c in r.get("comments", []) if c.get("success"))
            for r in comment_results
        )
        emoji = "✅" if success_c == total_c else ("⚠️" if success_c > 0 else "❌")
        lines.append(f"{emoji} 评论: {success_c}/{total_c} 条成功")
        for cr in comment_results:
            topic = cr.get("topic", "未知")
            lines.append(f"  • {topic}: {cr.get('message', '?')}")
            for c in cr.get("comments", []):
                user = c.get("post_user", "?")
                comment = c.get("comment", "")[:20]
                status = "✅" if c.get("success") else "❌"
                lines.append(f"      {status} @{user} 「{comment}」")
    else:
        lines.append("💬 评论: 未开启或未配置")

    # 发帖汇总（支持多条）
    if post_results:
        total = len(post_results)
        success = sum(1 for r in post_results if r.get("success"))
        emoji = "✅" if success == total else "⚠️"
        lines.append(f"{emoji} AI 发帖: {success}/{total} 成功")
        for pr in post_results:
            topic = pr.get("topic", "未知话题")
            if pr.get("success"):
                wid = pr.get("weibo_id", "?")
                lines.append(f"  • {topic} ✅ 已发布 (ID: {wid})")
                content_preview = pr.get("content", "")
                if content_preview:
                    preview = content_preview[:60].replace("\n", " ")
                    preview = (
                        preview + "..."
                        if len(content_preview) > 60
                        else preview
                    )
                    lines.append(f"    {preview}")
            else:
                msg = pr.get("message", "失败")
                lines.append(f"  • {topic} ❌ {msg}")
    else:
        lines.append("📝 发帖: 未开启或未配置")

    lines.append("━━━━━━━━━━━━━━")

    # 标题：结合签到、评论和发帖状态
    title_parts = []
    if checkin_results:
        s = sum(1 for r in checkin_results if r["success"])
        t = len(checkin_results)
        title_parts.append(f"签到({s}/{t})")
    if comment_results:
        s = sum(
            sum(1 for c in r.get("comments", []) if c.get("success"))
            for r in comment_results
        )
        t = sum(len(r.get("comments", [])) for r in comment_results)
        if t > 0:
            title_parts.append(f"评论({s}/{t})")
    if post_results:
        s = sum(1 for r in post_results if r.get("success"))
        t = len(post_results)
        title_parts.append(f"发帖({s}/{t})")

    # 标题：优先使用福袋标题
    if fudai_results:
        fudai_summary = fudai_results.get("summary", {})
        total = fudai_summary.get("total", 0)
        ck = fudai_summary.get("checkin_success", 0)
        cs = fudai_summary.get("consume_success", 0)
        pt = fudai_summary.get("post_success", 0)
        ia = fudai_summary.get("interact_success", 0)
        all_good = (ck == total and cs == total and pt == total and ia == total)
        status = "✅" if all_good else "⚠️"
        title = f"{status} 周三福袋 签到{ck}/{total} 发帖{pt}/{total} 互动{ia}/{total}"
    elif title_parts:
        title = "微博 " + "，".join(title_parts)
    else:
        title = "微博执行完成"

    return title, "\n".join(lines)
