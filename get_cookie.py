#!/usr/bin/env python3
"""
自动获取微博 Cookie（免手动 F12 复制）

用法:
  python get_cookie.py

流程:
  1. 打开 Chromium 浏览器 → 跳转微博登录页
  2. 你用微博 APP 扫码登录
  3. 脚本自动检测登录成功
  4. 自动抓取 PC 端 + 移动端 Cookie
  5. 写入 .env 文件
"""

import os
import sys
import io
import re

# 修复 Windows 终端 GBK 编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )


def update_env_file(cookie_str: str):
    """将 Cookie 写入 .env 文件的 WEIBO_COOKIE 行"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")

    if not os.path.exists(env_path):
        print("⚠️  未找到 .env 文件，将创建新文件")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"WEIBO_COOKIE={cookie_str}\n")
        print(f"✅ Cookie 已写入 {env_path}")
        return

    # 读取现有 .env
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 替换或追加 WEIBO_COOKIE
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("WEIBO_COOKIE=") and not stripped.startswith("#"):
            lines[i] = f"WEIBO_COOKIE={cookie_str}\n"
            found = True
            break

    if not found:
        lines.append(f"\nWEIBO_COOKIE={cookie_str}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"✅ Cookie 已写入 {env_path}")


def main():
    print("=" * 55)
    print("  微博 Cookie 自动获取工具")
    print("=" * 55)
    print()
    print("即将打开浏览器，请在浏览器中扫码登录微博。")
    print("登录成功后脚本会自动抓取 Cookie 并写入 .env。")
    print()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ 需要安装 Playwright: pip install playwright && playwright install chromium")
        sys.exit(1)

    with sync_playwright() as p:
        # 使用持久化上下文，保留登录状态
        # 用非无头模式让用户可以看到扫码页面
        print("📌 启动浏览器...")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        # 打开微博登录页
        print("📌 打开微博登录页...")
        page.goto("https://weibo.com/login.php", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        print()
        print("╔══════════════════════════════════════════════╗")
        print("║  👆 请在浏览器中扫码登录微博                 ║")
        print("║     登录成功后脚本会自动继续...             ║")
        print("╚══════════════════════════════════════════════╝")
        print()
        print("⏳ 等待登录（最长 5 分钟）...")

        # 等待登录成功：URL 跳转到 weibo.com 首页或出现用户信息
        logged_in = False
        for _ in range(150):  # 最多等 5 分钟 (150 × 2s)
            page.wait_for_timeout(2000)
            current_url = page.url

            # 检查是否登录成功
            # 登录成功后通常会跳转到 weibo.com 首页或 u/ 用户页
            if "login.php" not in current_url and "passport.weibo.com" not in current_url:
                # 进一步验证：检查 cookie 中是否有 SUB
                cookies = context.cookies()
                has_sub = any(c["name"] == "SUB" and len(c.get("value", "")) > 10 for c in cookies)
                if has_sub:
                    logged_in = True
                    print(f"\n✅ 检测到登录成功！当前 URL: {current_url}")
                    break

            # 显示等待进度
            if _ % 15 == 0 and _ > 0:
                print(f"   ... 已等待 {_ * 2} 秒，请继续扫码登录")

        if not logged_in:
            print("\n⚠️  等待超时。请确认已登录后按 Enter 手动继续...")
            input()

        # 登录成功，等待页面完全加载
        page.wait_for_timeout(3000)

        # ---- 收集 PC 端 Cookie ----
        print("\n📌 收集 PC 端 Cookie (.weibo.com)...")
        page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        pc_cookies = context.cookies()
        print(f"   获取到 {len(pc_cookies)} 个 Cookie")

        # ---- 收集移动端 Cookie ----
        print("\n📌 收集移动端 Cookie (m.weibo.cn)...")
        page.goto("https://m.weibo.cn/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        mobile_cookies = context.cookies()
        print(f"   获取到 {len(mobile_cookies)} 个 Cookie")

        # ---- 合并去重 ----
        # 用字典去重，保留每个 name 的第一个值
        all_cookies = {}
        # 先加 PC 端 cookie
        for c in pc_cookies:
            if c.get("name") and c.get("value"):
                all_cookies[c["name"]] = c["value"]
        # 再加移动端 cookie（不覆盖已有的）
        for c in mobile_cookies:
            if c.get("name") and c.get("value"):
                if c["name"] not in all_cookies:
                    all_cookies[c["name"]] = c["value"]

        # 构建 cookie 字符串
        cookie_str = "; ".join(f"{k}={v}" for k, v in all_cookies.items())

        # 显示摘要
        print(f"\n📋 合并后共 {len(all_cookies)} 个 Cookie 字段")
        print(f"   关键 Cookie 检测:")
        key_fields = [
            "SUB", "SUBP", "SCF", "PC_TOKEN", "_s_tentry",
            "M_WEIBOCN_PARAMS", "MLOGIN", "XSRF-TOKEN",
            "mweibo_short_token", "SSOLoginState", "WEIBOCN_FROM",
            "_T_WM", "ALF", "ULV",
        ]
        for field in key_fields:
            status = "✅" if field in all_cookies else "❌"
            print(f"     {status} {field}")

        # 写入 .env
        print()
        update_env_file(cookie_str)

        # 关闭浏览器
        browser.close()

    print()
    print("=" * 55)
    print("  🎉 完成！可以运行 python main.py 测试了")
    print("=" * 55)


if __name__ == "__main__":
    main()
