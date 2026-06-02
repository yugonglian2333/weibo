#!/usr/bin/env python3
"""
微博超话自动签到 + AI 发帖
================================
每天定时执行：超话签到 → AI 生成内容 → 发布微博

敏感信息通过环境变量传入（适配 GitHub Actions Secrets）:
  WEIBO_COOKIE  - 微博登录 Cookie
  AI_PROVIDER   - AI 服务商 (默认 mimo)
  AI_API_KEY    - AI API Key
  AI_API_BASE   - AI API 地址 (可选)
  AI_MODEL      - AI 模型名称 (可选)
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# 尝试导入 yaml，如果没有则使用简单的配置方式
try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from weibo_client import WeiboClient
from ai_provider import create_provider_from_env
from notifier import (
    create_notifier_from_env,
    build_notification,
)


# ============================================================
# 日志配置
# ============================================================

def setup_logging():
    """配置日志输出"""
    # 北京时间时区
    tz_beijing = timezone(timedelta(hours=8))

    class BeijingFormatter(logging.Formatter):
        """使用北京时间的日志格式化器"""

        def formatTime(self, record, datefmt=None):
            dt = datetime.fromtimestamp(record.created, tz=tz_beijing)
            if datefmt:
                return dt.strftime(datefmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")

    formatter = BeijingFormatter(
        fmt="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)


# ============================================================
# 配置读取
# ============================================================

def load_config() -> dict:
    """加载配置文件，合并环境变量"""
    config = {
        "checkin": {"topics": []},
        "posting": {"enabled": True, "topics": [], "style": "自然随性"},
    }

    # 尝试从 config.yaml 读取
    if HAS_YAML:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "config.yaml",
        )
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f) or {}
            config.update(yaml_config)

    # 环境变量也可以覆盖配置
    env_topics = os.environ.get("WEIBO_CHECKIN_TOPICS", "")
    if env_topics:
        config["checkin"]["topics"] = [
            {"name": t.strip()}
            for t in env_topics.split(",")
            if t.strip()
        ]

    env_post_topics = os.environ.get("WEIBO_POST_TOPICS", "")
    if env_post_topics:
        config["posting"]["topics"] = [
            t.strip()
            for t in env_post_topics.split(",")
            if t.strip()
        ]

    posting_enabled = os.environ.get("WEIBO_POST_ENABLED", "")
    if posting_enabled.lower() in ("false", "0", "no"):
        config["posting"]["enabled"] = False

    return config


# ============================================================
# 核心流程
# ============================================================

def run_checkin(client: WeiboClient, topics: list[dict]) -> list[dict]:
    """
    执行超话签到

    Args:
        client: WeiboClient 实例
        topics: 超话列表，如 [{"name": "xxx", "containerid": "100808xxx"}, ...]

    Returns:
        签到结果列表
    """
    if not topics:
        logger.info("没有配置超话签到列表，跳过签到")
        return []

    logger.info(f"===== 超话签到开始（共 {len(topics)} 个）=====")
    results = []

    for topic in topics:
        name = topic.get("name", "未知超话")
        containerid = topic.get("containerid", "")

        # 如果没有提供 containerid，自动搜索
        if not containerid:
            logger.info(f"正在搜索超话 '{name}'...")
            containerid = client.get_containerid_by_name(name)
            if not containerid:
                results.append({
                    "success": False,
                    "message": f"❌ 未找到超话 '{name}'，请确认名称正确",
                    "name": name,
                })
                continue
            # 短暂延迟，避免请求过快
            time.sleep(1)

        result = client.checkin_super_topic(containerid)
        result["name"] = name
        results.append(result)

        # 每个签到之间间隔，避免被限流
        if len(topics) > 1:
            time.sleep(2)

    # 汇总
    success_count = sum(1 for r in results if r["success"])
    logger.info(
        f"===== 签到完成: {success_count}/{len(topics)} 成功 ====="
    )

    return results


def run_posting(
    client: WeiboClient,
    ai_provider,
    topics: list[str],
    style: str = "自然随性",
) -> dict:
    """
    执行 AI 发帖

    Args:
        client: WeiboClient 实例
        ai_provider: AIProvider 实例
        topics: 发帖涉及的话题列表
        style: 发帖风格

    Returns:
        发帖结果
    """
    if not topics:
        logger.info("没有配置发帖话题，跳过 AI 发帖")
        return {"success": False, "message": "未配置发帖话题"}

    logger.info(f"===== AI 发帖开始 =====")
    logger.info(f"话题: {', '.join(topics)}")
    logger.info(f"风格: {style}")

    # 1. 调用 AI 生成内容
    logger.info("正在调用 AI 生成帖子内容...")
    content = ai_provider.generate_post(topics=topics, style=style)

    if not content:
        return {
            "success": False,
            "message": "AI 内容生成失败，不发帖",
            "content": "",
        }

    logger.info(f"AI 生成内容:\n---\n{content}\n---")

    # 2. 发布到微博
    logger.info("正在发布到微博...")
    result = client.post_weibo(content)
    result["content"] = content

    logger.info(f"===== 发帖完成 =====")
    return result


# ============================================================
# 主函数
# ============================================================

def main():
    """主入口"""
    setup_logging()

    logger.info("=" * 50)
    logger.info("微博超话签到 + AI 发帖 启动")
    logger.info("=" * 50)

    # ---- 1. 加载配置 ----
    cookie = os.environ.get("WEIBO_COOKIE", "")
    if not cookie:
        logger.error("未设置 WEIBO_COOKIE 环境变量，无法继续")
        logger.error(
            "请在 GitHub Secrets 中设置 WEIBO_COOKIE，"
            "或本地运行时设置环境变量"
        )
        sys.exit(1)

    config = load_config()

    # ---- 2. 初始化微博客户端 ----
    logger.info("初始化微博客户端...")
    client = WeiboClient(cookie)

    # 检测登录态
    if not client.check_session_valid():
        logger.error(
            "Cookie 已失效，请重新获取微博 Cookie 并更新"
        )
        sys.exit(1)

    # ---- 3. 超话签到 ----
    checkin_topics = config.get("checkin", {}).get("topics", [])
    checkin_results = run_checkin(client, checkin_topics)

    # ---- 4. AI 发帖 ----
    posting_config = config.get("posting", {})
    posting_enabled = posting_config.get("enabled", True)
    posting_topics = posting_config.get("topics", [])
    posting_style = posting_config.get("style", "自然随性")

    post_result = None
    if posting_enabled and posting_topics:
        try:
            # 初始化 AI Provider
            ai = create_provider_from_env()
            post_result = run_posting(
                client, ai, posting_topics, posting_style
            )
        except ValueError as e:
            logger.error(f"AI Provider 初始化失败: {e}")
        except Exception as e:
            logger.error(f"AI 发帖异常: {e}")
    elif posting_enabled and not posting_topics:
        logger.info("发帖已开启但未配置话题，跳过发帖")
    else:
        logger.info("发帖已禁用，跳过")

    # ---- 5. 汇总报告 ----
    logger.info("=" * 50)
    logger.info("执行汇总")
    logger.info("=" * 50)

    for r in checkin_results:
        logger.info(f"签到: {r.get('name', '?')} -> {r['message']}")

    if post_result:
        logger.info(f"发帖: {post_result['message']}")
        if post_result.get("content"):
            preview = post_result["content"][:80].replace("\n", " ")
            logger.info(f"内容预览: {preview}...")

    all_success = all(r["success"] for r in checkin_results)
    post_success = post_result and post_result.get("success", False)

    logger.info("=" * 50)
    if checkin_results:
        logger.info(
            f"签到: {sum(1 for r in checkin_results if r['success'])}"
            f"/{len(checkin_results)} 成功"
        )
    if post_result:
        logger.info(f"发帖: {'成功 ✅' if post_success else '失败 ❌'}")
    logger.info("=" * 50)

    # ---- 6. 发送通知 ----
    notification_config = config.get("notification", {})
    notify_enabled = notification_config.get("enabled", True)

    if notify_enabled:
        logger.info("=" * 50)
        logger.info("发送通知")
        logger.info("=" * 50)

        try:
            notifier = create_notifier_from_env()
            if notifier:
                title, content = build_notification(
                    checkin_results, post_result
                )
                if notifier.send(title, content):
                    logger.info("通知已发送")
                else:
                    logger.warning("通知发送失败，不影响主流程")
            else:
                logger.info("未配置通知渠道，跳过")
        except Exception as e:
            logger.error(f"通知模块异常，不影响主流程: {e}")

    # 如果有任何失败，返回非零退出码（GitHub Actions 会标记为失败）
    if not all_success or (post_result and not post_success):
        sys.exit(1)


if __name__ == "__main__":
    main()
