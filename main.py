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
import random
import sys
import time
from datetime import datetime, timezone, timedelta


def load_dotenv(env_path: str = None):
    """
    加载 .env 文件到环境变量（不覆盖已有环境变量）

    这样本地开发时可以用 .env 文件管理配置，
    GitHub Actions 中则通过 Secrets 传入（优先级更高）。
    """
    if env_path is None:
        # 默认在脚本同级目录查找 .env
        env_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".env",
        )

    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            # 解析 key=value
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # 移除引号
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                # 只在环境变量未设置时加载（环境变量优先级更高）
                if key and key not in os.environ:
                    os.environ[key] = value


# 在模块加载时自动加载 .env
load_dotenv()

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


# AI 评论生成失败时的模板评论
FALLBACK_COMMENTS = [
    "说得对",
    "支持",
    "太棒了",
    "加油",
    "好帖",
    "说得好",
    "赞同",
    "赞一个",
    "有道理",
    "厉害了",
    "可以的",
    "不错不错",
    "真好",
    "期待更新",
    "感谢分享",
    "爱了爱了",
    "每天都在看",
    "超级好看",
    "收藏了",
    "比心",
]


# ============================================================
# 日志配置
# ============================================================

def setup_logging():
    """配置日志输出"""
    # 修复 Windows 终端 GBK 编码问题：用 UTF-8 包装 stdout
    if sys.platform == "win32":
        sys.stdout = open(
            sys.stdout.fileno(),
            mode="w",
            encoding="utf-8",
            errors="replace",
            closefd=False,
        )

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


def _save_containerid_to_config(name: str, containerid: str):
    """将搜索到的 containerid 自动保存回 config.yaml，下次无需重复搜索"""
    if not HAS_YAML:
        return
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "config.yaml",
    )
    if not os.path.exists(config_path):
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        topics = config.setdefault("checkin", {}).setdefault("topics", [])
        updated = False
        for t in topics:
            if isinstance(t, dict) and t.get("name") == name:
                if not t.get("containerid"):
                    t["containerid"] = containerid
                    updated = True
                break
        else:
            # 话题不在签到列表中，添加进去（用于记录 containerid 映射）
            topics.append({"name": name, "containerid": containerid})
            updated = True

        if updated:
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    config, f,
                    allow_unicode=True, default_flow_style=False, sort_keys=False,
                )
            logger.info(
                f"💾 已将「{name}」的 containerid 保存到 config.yaml，下次无需重复搜索"
            )
    except Exception as e:
        logger.warning(f"保存 containerid 到 config.yaml 失败: {e}")


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
            # 保存到 config.yaml，下次无需重复搜索
            _save_containerid_to_config(name, containerid)
            topic["containerid"] = containerid
            # 短暂延迟，避免请求过快
            time.sleep(1)

        result = client.checkin_super_topic(containerid, topic_name=name)
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


def run_commenting(
    client: WeiboClient,
    ai_provider,
    topics: list[dict],
    count: int = 3,
    fixed_comment: str = "",
) -> list[dict]:
    """
    对超话下的前 N 个帖子进行自动评论

    Args:
        client: WeiboClient 实例
        ai_provider: AIProvider 实例（可为 None，此时只用模板评论）
        topics: 超话列表，如 [{"name": "xxx", "containerid": "100808xxx"}, ...]
        count: 每个超话评论的帖子数（默认 3）
        fixed_comment: 固定评论内容。非空时直接使用此内容，跳过 AI 生成。

    Returns:
        评论结果列表
    """
    if not topics:
        logger.info("没有需要评论的超话，跳过")
        return []

    if fixed_comment:
        logger.info(f"使用固定评论内容: 「{fixed_comment}」")

    logger.info(
        f"===== 自动评论开始（共 {len(topics)} 个超话，每个 {count} 条）====="
    )
    results = []

    for t_idx, topic in enumerate(topics):
        name = topic.get("name", "未知超话")
        containerid = topic.get("containerid", "")

        if not containerid:
            logger.warning(f"「{name}」无 containerid，跳过评论")
            continue

        logger.info(
            f"--- [{t_idx + 1}/{len(topics)}] 超话「{name}」获取帖子 ---"
        )

        # 1. 获取前 N 个帖子
        posts = client.get_super_topic_posts(containerid, count=count)
        if not posts:
            logger.warning(f"「{name}」未获取到帖子，跳过评论")
            results.append({
                "topic": name,
                "success": True,
                "message": f"⏭ {name} 无帖子可评论",
                "comments": [],
            })
            continue

        logger.info(
            f"「{name}」获取到 {len(posts)} 条帖子，开始评论..."
        )

        topic_comment_results = []

        for p_idx, post in enumerate(posts):
            post_mid = post.get("mid", "")
            post_text = post.get("text", "")
            post_user = post.get("user", "用户")

            if not post_mid:
                logger.warning(
                    f"  帖子[{p_idx + 1}] 缺少 mid，跳过"
                )
                continue

            logger.info(
                f"  [{p_idx + 1}/{len(posts)}] 帖子 by @{post_user}: "
                f"「{post_text[:40]}...」"
            )

            # 2. 生成评论
            comment = ""
            if fixed_comment:
                # 有固定评论内容时直接使用
                comment = fixed_comment
                logger.info(f"   使用固定评论: 「{comment}」")
            elif ai_provider:
                try:
                    comment = ai_provider.generate_comment(
                        post_content=post_text, topic=name
                    )
                except Exception as e:
                    logger.warning(
                        f"   AI 评论生成异常: {e}，使用模板评论"
                    )
                    comment = ""

            if not comment:
                # 没有固定评论且 AI 未生成时，使用模板
                comment = random.choice(FALLBACK_COMMENTS)
                logger.info(f"   使用模板评论: 「{comment}」")
            elif not fixed_comment:
                logger.info(f"   AI 生成评论: 「{comment}」")

            # 3. 发布评论
            comment_result = client.comment_post(
                post_mid=post_mid, content=comment, post_id=post.get("id", "")
            )
            comment_result["comment"] = comment
            comment_result["post_user"] = post_user
            topic_comment_results.append(comment_result)

            logger.info(f"   结果: {comment_result['message']}")

            # 帖子之间间隔，避免限流
            if p_idx < len(posts) - 1:
                time.sleep(2)

        success_count = sum(
            1 for cr in topic_comment_results if cr.get("success")
        )
        results.append({
            "topic": name,
            "success": True,
            "message": (
                f"「{name}」评论完成: "
                f"{success_count}/{len(topic_comment_results)} 成功"
            ),
            "comments": topic_comment_results,
        })

        logger.info(f"  {results[-1]['message']}")

        # 超话之间间隔
        if t_idx < len(topics) - 1:
            time.sleep(2)

    # 汇总
    total_comments = sum(
        len(r.get("comments", [])) for r in results
    )
    total_success = sum(
        sum(1 for cr in r.get("comments", []) if cr.get("success"))
        for r in results
    )
    logger.info(
        f"===== 评论完成: {total_success}/{total_comments} 条成功 ====="
    )

    return results


def run_posting(
    client: WeiboClient,
    ai_provider,
    topics: list[str],
    style: str = "自然随性",
    topic_containerid_map: dict[str, str] = None,
    min_words: int = 50,
    max_words: int = 200,
    temperature: float = 0.9,
    max_tokens: int = 4096,
) -> list[dict]:
    """
    执行 AI 发帖：每个话题独立生成一条微博并发布到对应超话

    Args:
        client: WeiboClient 实例
        ai_provider: AIProvider 实例
        topics: 发帖涉及的话题列表（每个话题单独生成一条微博）
        style: 发帖风格
        topic_containerid_map: 话题名 -> containerid 映射，用于超话内发帖
        min_words: AI 生成内容最少字数
        max_words: AI 生成内容最多字数
        temperature: AI 生成随机性参数
        max_tokens: AI 生成最大 token 数

    Returns:
        发帖结果列表，每个元素为 {"topic": str, "success": bool, "content": str, "message": str, ...}
    """
    if not topics:
        logger.info("没有配置发帖话题，跳过 AI 发帖")
        return []

    if topic_containerid_map is None:
        topic_containerid_map = {}

    logger.info(f"===== AI 发帖开始（共 {len(topics)} 个话题）=====")
    logger.info(f"话题: {', '.join(topics)}")
    logger.info(f"风格: {style}，字数: {min_words}-{max_words}，temperature: {temperature}")

    results = []

    for i, topic in enumerate(topics):
        logger.info(f"--- [{i+1}/{len(topics)}] 正在处理话题: {topic} ---")

        # 1. 为每个话题单独调用 AI
        logger.info(f"正在调用 AI 为「{topic}」生成帖子内容...")
        content = ai_provider.generate_post(
            topics=[topic],
            style=style,
            min_words=min_words,
            max_words=max_words,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if not content:
            logger.warning(f"AI 为「{topic}」生成内容失败，跳过发帖")
            results.append({
                "topic": topic,
                "success": False,
                "message": "AI 内容生成失败",
                "content": "",
                "weibo_id": "",
            })
            continue

        logger.info(f"AI 生成内容 [{topic}]:\n---\n{content}\n---")

        # 2. 发布到微博（传入 containerid 以发布到超话内部）
        containerid = topic_containerid_map.get(topic)
        if containerid:
            logger.info(f"正在发布「{topic}」到超话内部 (containerid={containerid})...")
        else:
            logger.info(f"正在发布「{topic}」到微博（未找到 containerid，发布到个人主页）...")

        result = client.post_weibo(content, containerid=containerid)
        result["topic"] = topic
        result["content"] = content
        results.append(result)

        logger.info(f"「{topic}」发帖结果: {result['message']}")

        # 多条帖子之间间隔，避免被限流
        if i < len(topics) - 1:
            time.sleep(3)

    # 汇总
    success_count = sum(1 for r in results if r["success"])
    logger.info(f"===== 发帖完成: {success_count}/{len(topics)} 成功 =====")

    return results


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

    # ---- 3.5 自动评论 ----
    commenting_config = config.get("commenting", {})
    commenting_enabled = commenting_config.get("enabled", True)
    commenting_count = commenting_config.get("count", 3)
    # 固定评论内容：优先取 config，其次取环境变量 WEIBO_COMMENT_CONTENT
    fixed_comment = commenting_config.get("fixed_comment", "")
    if not fixed_comment:
        fixed_comment = os.environ.get("WEIBO_COMMENT_CONTENT", "")

    comment_results = []
    if commenting_enabled and checkin_results:
        # 只评论签到成功的超话
        successful_topics = [
            {"name": r["name"], "containerid": r.get("containerid", "")}
            for r in checkin_results
            if r.get("success")
        ]
        if successful_topics:
            logger.info(
                f"将为 {len(successful_topics)} 个签到成功的超话进行评论"
            )
            # 有固定评论时不需要 AI
            if fixed_comment:
                logger.info(f"固定评论模式: 「{fixed_comment}」")
                comment_results = run_commenting(
                    client, None, successful_topics,
                    count=commenting_count,
                    fixed_comment=fixed_comment,
                )
            else:
                try:
                    ai_for_comment = create_provider_from_env()
                    comment_results = run_commenting(
                        client, ai_for_comment, successful_topics,
                        count=commenting_count,
                    )
                except ValueError as e:
                    logger.warning(
                        f"AI Provider 初始化失败，使用模板评论: {e}"
                    )
                    comment_results = run_commenting(
                        client, None, successful_topics,
                        count=commenting_count,
                    )
                except Exception as e:
                    logger.error(f"自动评论异常: {e}")
        else:
            logger.info("没有签到成功的超话，跳过评论")
    elif not commenting_enabled:
        logger.info("评论功能已禁用，跳过")
    else:
        logger.info("没有签到结果，跳过评论")

    # ---- 4. AI 发帖 ----
    posting_config = config.get("posting", {})
    posting_enabled = posting_config.get("enabled", True)
    posting_topics = posting_config.get("topics", [])
    posting_style = posting_config.get("style", "自然随性")
    posting_min_words = posting_config.get("min_words", 50)
    posting_max_words = posting_config.get("max_words", 200)
    posting_temperature = posting_config.get("temperature", 0.9)
    posting_max_tokens = posting_config.get("max_tokens", 4096)

    post_results = []
    if posting_enabled and posting_topics:
        # 预先解析每个话题的 containerid，用于超话内发帖
        topic_to_cid = {}
        # 先从 checkin topics 中查找已有的 containerid
        for topic_name in posting_topics:
            for ct in config.get("checkin", {}).get("topics", []):
                if ct.get("name") == topic_name and ct.get("containerid"):
                    topic_to_cid[topic_name] = ct["containerid"]
                    logger.info(
                        f"话题「{topic_name}」-> containerid: {ct['containerid']}（来自签到配置）"
                    )
                    break

        # 如果签到配置中没有，再通过 API 搜索
        for topic_name in posting_topics:
            if topic_name in topic_to_cid:
                continue
            cid = client.get_containerid_by_name(topic_name)
            if cid:
                topic_to_cid[topic_name] = cid
                logger.info(f"话题「{topic_name}」-> containerid: {cid}")
                # 保存到 config.yaml，下次无需重复搜索
                _save_containerid_to_config(topic_name, cid)
            else:
                logger.warning(
                    f"未找到话题「{topic_name}」的 containerid，将发布到个人主页"
                )
            # 搜索间隔，避免请求过快
            if len(posting_topics) > 1:
                time.sleep(1)

        try:
            # 初始化 AI Provider
            ai = create_provider_from_env()
            post_results = run_posting(
                client, ai, posting_topics, posting_style, topic_to_cid,
                min_words=posting_min_words,
                max_words=posting_max_words,
                temperature=posting_temperature,
                max_tokens=posting_max_tokens,
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

    # 清理 Playwright 浏览器资源
    client.cleanup()

    for r in checkin_results:
        logger.info(f"签到: {r.get('name', '?')} -> {r['message']}")

    for cr in comment_results:
        logger.info(
            f"评论 [{cr.get('topic', '?')}]: {cr.get('message', '?')}"
        )
        for c in cr.get("comments", []):
            logger.info(
                f"  @{c.get('post_user', '?')}: "
                f"「{c.get('comment', '')[:30]}」→ {c.get('message', '?')}"
            )

    for pr in post_results:
        logger.info(
            f"发帖 [{pr.get('topic', '?')}]: {pr.get('message', '?')}"
        )
        if pr.get("content"):
            preview = pr["content"][:80].replace("\n", " ")
            logger.info(f"  内容预览: {preview}...")

    all_success = all(r["success"] for r in checkin_results)
    post_success = (
        len(post_results) > 0
        and all(pr.get("success") for pr in post_results)
    )

    logger.info("=" * 50)
    if checkin_results:
        logger.info(
            f"签到: {sum(1 for r in checkin_results if r['success'])}"
            f"/{len(checkin_results)} 成功"
        )
    if comment_results:
        total_c = sum(len(r.get("comments", [])) for r in comment_results)
        success_c = sum(
            sum(1 for c in r.get("comments", []) if c.get("success"))
            for r in comment_results
        )
        logger.info(
            f"评论: {success_c}/{total_c} 条成功"
        )
    if post_results:
        success_count = sum(1 for pr in post_results if pr.get("success"))
        logger.info(
            f"发帖: {success_count}/{len(post_results)} 成功"
            f"{' ✅' if post_success else ' ❌'}"
        )
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
                    checkin_results, post_results
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
    if not all_success or (post_results and not post_success):
        sys.exit(1)


if __name__ == "__main__":
    main()
