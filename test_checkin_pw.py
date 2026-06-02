"""
用 Playwright 测试超话签到（绕过 Sina Visitor System）
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
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)

        page = await context.new_page()

        containerid = "1008086f1b7983ba4fca7456e28317e78127ed"

        # Step 1: 先访问 weibo.com 首页，通过 Visitor System
        print("=== Step 1: 访问 weibo.com 首页 ===")
        await page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        print(f"当前 URL: {page.url}")

        # Step 2: 用原生 XMLHttpRequest（不被 SPA 拦截）
        print("\n=== Step 2: XHR 调用签到 API ===")

        result = await page.evaluate("""
            (containerid) => {
                return new Promise((resolve, reject) => {
                    const url = 'https://weibo.com/p/aj/general/button'
                        + '?ajwvr=6'
                        + '&api=http://i.huati.weibo.com/aj/super/checkin'
                        + '&id=' + containerid
                        + '&location=page_100808_super_index';

                    const xhr = new XMLHttpRequest();
                    xhr.open('GET', url, true);
                    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
                    xhr.setRequestHeader('Accept', 'application/json, text/plain, */*');
                    xhr.setRequestHeader('Referer', 'https://weibo.com/p/' + containerid + '/super_index');
                    xhr.onload = () => resolve({status: xhr.status, body: xhr.responseText});
                    xhr.onerror = () => resolve({status: 0, body: xhr.statusText});
                    xhr.send();
                });
            }
        """, containerid)

        print(f"status: {result['status']}")
        try:
            data = json.loads(result['body'])
            print(f"JSON: {json.dumps(data, ensure_ascii=False, indent=2)}")
        except:
            print(f"body[:500]: {result['body'][:500]}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
