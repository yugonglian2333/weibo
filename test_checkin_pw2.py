"""
用 Playwright 测试超话签到 — 策略2: 使用 APIRequestContext + 避免 SPA 拦截
"""
import asyncio
import json
from playwright.async_api import async_playwright


async def main():
    # 读取 cookie
    with open("e:/GitHub/weibo/.env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("WEIBO_COOKIE="):
                cookie_str = line.split("WEIBO_COOKIE=", 1)[1]
                break

    # 解析 cookie
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
        # headless=False 让浏览器可视化，便于排查
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        await context.add_cookies(cookies)

        containerid = "1008086f1b7983ba4fca7456e28317e78127ed"

        # === 策略 1: 导航到 weibo.com，然后使用 page.request (APIRequestContext) ===
        # APIRequestContext 共享浏览器 Cookie，但不经过 SPA JS 拦截
        print("=== 策略 1: APIRequestContext (共享浏览器 Cookie) ===")

        page = await context.new_page()

        # 先访问 weibo.com 完成 Visitor System 验证
        print("Step 1: 访问 weibo.com...")
        await page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        print(f"当前 URL: {page.url}")

        checkin_url = (
            "https://weibo.com/p/aj/general/button"
            "?ajwvr=6"
            "&api=http://i.huati.weibo.com/aj/super/checkin"
            f"&id={containerid}"
            "&location=page_100808_super_index"
        )

        # 用 APIRequestContext (page.request) 发请求
        print(f"\nStep 2: 用 APIRequestContext 调用签到 API...")
        api_resp = await page.request.get(
            checkin_url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/plain, */*",
                "Referer": f"https://weibo.com/p/{containerid}/super_index",
            },
        )
        print(f"APIRequestContext status: {api_resp.status}")
        body = await api_resp.text()
        print(f"body: {body[:500]}")
        try:
            data = json.loads(body)
            print(f"JSON: {json.dumps(data, ensure_ascii=False, indent=2)}")
        except:
            pass

        # === 策略 2: 直接导航到签到 API URL（作为页面访问）===
        print("\n=== 策略 2: 直接导航到签到 API URL ===")
        await page.goto(checkin_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        body_text = await page.evaluate("document.body.innerText")
        print(f"页面内容: {body_text[:500]}")

        # === 策略 3: 导航到超话页面，模拟点击签到 ===
        print("\n=== 策略 3: 超话页面模拟点击 ===")
        topic_url = f"https://weibo.com/p/{containerid}/super_index"
        await page.goto(topic_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)
        print(f"超话页面 URL: {page.url[:120]}")

        # 查找签到按钮
        btn_text = await page.evaluate("""
            () => {
                const btns = document.querySelectorAll('a, button, span, div[class*="check"]');
                const found = [];
                for (const b of btns) {
                    const text = b.textContent?.trim() || '';
                    if (text.includes('签到') || text.includes('打卡') || text.includes('check')) {
                        found.push({
                            tag: b.tagName,
                            text: text.slice(0, 40),
                            class: (b.className || '').toString().slice(0, 60),
                            visible: b.offsetParent !== null,
                        });
                    }
                    if (found.length >= 10) break;
                }
                return found;
            }
        """)
        print(f"找到的签到按钮: {json.dumps(btn_text, ensure_ascii=False, indent=2)}")

        # 也检查页面 title
        title = await page.title()
        print(f"页面标题: {title}")

        await page.wait_for_timeout(3000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
