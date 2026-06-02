"""
用 Playwright 测试 PC 端发帖 — weibo.com/aj/mblog/add
"""
import asyncio
import json
import sys
import io
from playwright.async_api import async_playwright

# 修复 Windows GBK 编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


async def main():
    # 读取 cookie
    with open("e:/GitHub/weibo/.env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("WEIBO_COOKIE="):
                cookie_str = line.split("WEIBO_COOKIE=", 1)[1]
                break

    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            name, value = item.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".weibo.com",
                "path": "/",
            })

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        await context.add_cookies(cookies)

        page = await context.new_page()

        # Step 1: 访问 weibo.com 完成 Visitor System 验证
        print("Step 1: 访问 weibo.com...")
        await page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)
        print(f"当前 URL: {page.url}")

        # Step 2: 获取 st (CSRF token) — 从页面 config 中提取
        print("\nStep 2: 获取 CSRF token...")
        config_data = await page.evaluate("""
            () => {
                try {
                    return window.$CONFIG || {};
                } catch(e) {
                    return {};
                }
            }
        """)
        print(f"$CONFIG keys: {list(config_data.keys()) if config_data else 'N/A'}")
        st = config_data.get('st', '')

        # 如果 $CONFIG 没有 st，尝试从 cookie 中获取
        if not st:
            cookies_list = await context.cookies()
            for c in cookies_list:
                if c['name'] == 'XSRF-TOKEN':
                    st = c['value']
                    print(f"从 Cookie 获取 st: {st}")
                    break

        print(f"st: {st}")

        # Step 3: 用 APIRequestContext 发帖
        containerid = "1008084ae7f528d66ae91d46332e067acdfba9"
        content = "测试发帖 — PC端API方式 🌸 张婧仪超话打卡~"

        print(f"\nStep 3: 发帖...")
        print(f"内容: {content}")

        # PC 端发帖 API
        # 参考: weibo.com/aj/mblog/add
        post_url = "https://weibo.com/aj/mblog/add"
        post_data = {
            "text": content,
            "pub_source": "page_100808_super_index",
            "pdetail": containerid,
            "location": f"page_100808_super_index",
            "st": st,
            "_spr": "screen:1920x1080",
        }

        print(f"POST URL: {post_url}")
        print(f"数据: {json.dumps(post_data, ensure_ascii=False)}")

        api_resp = await page.request.post(
            post_url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": f"https://weibo.com/p/{containerid}/super_index",
            },
            form=post_data,
        )

        body = await api_resp.text()
        print(f"Status: {api_resp.status}")
        print(f"Body: {body[:500]}")

        try:
            data = json.loads(body)
            print(f"\nJSON: {json.dumps(data, ensure_ascii=False, indent=2)}")
        except:
            pass

        await page.wait_for_timeout(2000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
