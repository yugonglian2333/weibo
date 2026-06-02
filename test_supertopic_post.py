#!/usr/bin/env python3
"""
测试超话内部发帖 API
====================
目的：验证 post_weibo 的 page_id 参数是否能正确将帖子发到超话内部。

测试流程：
  1. 搜索一个测试超话，获取 containerid
  2. 分别用「不带 page_id」和「带 page_id」两种方式发帖
  3. 详细记录每种方式的请求参数和响应
"""

import logging
import os
import sys
import time

# ---- 加载 .env ----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import load_dotenv, setup_logging

load_dotenv()
setup_logging()

# 调高日志级别，方便抓包分析
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.DEBUG)

from weibo_client import WeiboClient

logger = logging.getLogger("test")

# ---- 测试用的无害内容 ----
TEST_CONTENT = "今天天气真不错☀️ 来超话逛逛，测试一下发帖功能～"


def main():
    cookie = os.environ.get("WEIBO_COOKIE", "")
    if not cookie:
        logger.error("未设置 WEIBO_COOKIE")
        sys.exit(1)

    client = WeiboClient(cookie)

    # 1. 验证登录态
    logger.info("=" * 60)
    logger.info("Step 1: 验证登录态")
    logger.info("=" * 60)
    if not client.check_session_valid():
        logger.error("Cookie 已失效！")
        sys.exit(1)

    # 2. 搜索测试超话
    # 用一个存在且安全的超话做测试
    test_topic = os.environ.get("TEST_TOPIC", "测试")
    logger.info("=" * 60)
    logger.info(f"Step 2: 搜索超话「{test_topic}」")
    logger.info("=" * 60)

    containerid = client.get_containerid_by_name(test_topic)
    if not containerid:
        logger.error(f"未找到超话「{test_topic}」，尝试用其他关键词...")
        # 尝试一些肯定存在的通用超话
        for fallback in ["微博", "日常", "生活"]:
            containerid = client.get_containerid_by_name(fallback)
            if containerid:
                test_topic = fallback
                logger.info(f"使用备选超话: {test_topic} (containerid={containerid})")
                break

    if not containerid:
        logger.error("所有超话搜索均失败，无法继续测试")
        sys.exit(1)

    logger.info(f"测试超话: {test_topic}, containerid: {containerid}")

    # 3. 测试一：不带 page_id（普通发帖）—— 作为对照组
    logger.info("=" * 60)
    logger.info("Step 3: 测试【普通发帖】—— 不传 containerid")
    logger.info("=" * 60)
    logger.info(f"测试内容: {TEST_CONTENT}")

    result_normal = client.post_weibo(TEST_CONTENT)
    logger.info(f"普通发帖结果: {result_normal}")

    # 如果普通发帖都失败了，说明 st 或其他参数有问题
    if not result_normal.get("success"):
        logger.error("普通发帖失败！请检查 st 和 Cookie 是否有效")
        # 不退出，继续尝试超话模式

    # 4. 测试二：带 page_id（超话内部发帖）
    logger.info("=" * 60)
    logger.info(f"Step 4: 测试【超话内部发帖】—— 传入 containerid={containerid}")
    logger.info("=" * 60)

    # 内容加个标识便于区分
    super_content = f"{TEST_CONTENT}（超话内发帖测试 #{int(time.time())}）"
    logger.info(f"测试内容: {super_content}")

    result_super = client.post_weibo(super_content, containerid=containerid)
    logger.info(f"超话内发帖结果: {result_super}")

    # 5. 汇总
    logger.info("=" * 60)
    logger.info("测试汇总")
    logger.info("=" * 60)
    logger.info(f"普通发帖: {'✅ 成功' if result_normal.get('success') else '❌ 失败'} - {result_normal.get('message')}")
    logger.info(f"超话发帖: {'✅ 成功' if result_super.get('success') else '❌ 失败'} - {result_super.get('message')}")

    if result_super.get("success"):
        logger.info("")
        logger.info("🎉 超话内发帖成功！page_id 参数有效。")
        logger.info(f"   请登录微博，进入超话「{test_topic}」查看帖子是否出现在超话帖子流中。")
        logger.info(f"   微博ID: {result_super.get('weibo_id')}")
    else:
        logger.info("")
        logger.info("⚠️ 超话内发帖失败，需要进一步排查。")
        logger.info("   请检查上方的完整请求/响应日志，特别关注：")
        logger.info("   1. 请求 URL 和 body 参数是否正确")
        logger.info("   2. 响应的 errno 和 msg 给出的失败原因")
        logger.info("   3. 可能需要抓包 m.weibo.cn 超话页面的真实发帖请求来对比")


if __name__ == "__main__":
    main()
