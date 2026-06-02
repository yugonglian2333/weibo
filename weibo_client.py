"""
微博 API 客户端
支持超话签到、发帖等功能
使用移动端 API (m.weibo.cn)，接口相对稳定
"""

import re
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class WeiboClient:
    """微博客户端，封装超话签到和发帖等操作"""

    BASE_URL = "https://m.weibo.cn"
    API_BASE = "https://m.weibo.cn/api"

    def __init__(self, cookie: str):
        """
        初始化客户端

        Args:
            cookie: 微博登录后的 Cookie 字符串
        """
        self.cookie = cookie
        self.session = requests.Session()
        self._setup_session()
        self._csrf_token = self._extract_csrf()

    def _setup_session(self):
        """配置请求会话，设置 Cookie 和通用请求头"""
        cookie_dict = self._parse_cookie_string(self.cookie)
        logger.info(f"解析到 {len(cookie_dict)} 个 Cookie 字段: {list(cookie_dict.keys())}")

        # 显式设置 Cookie 域名，同时覆盖 .weibo.cn 和 .weibo.com
        # 这样无论 Cookie 来自 PC 端还是移动端都能正常工作
        for key, value in cookie_dict.items():
            self.session.cookies.set(key, value, domain=".weibo.cn", path="/")
            self.session.cookies.set(key, value, domain=".weibo.com", path="/")
            self.session.cookies.set(key, value, domain="m.weibo.cn", path="/")
            self.session.cookies.set(key, value, domain="weibo.com", path="/")

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/15.0 Mobile/15E148 Safari/604.1"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://m.weibo.cn/",
            "X-Requested-With": "XMLHttpRequest",
        })

    @staticmethod
    def _parse_cookie_string(cookie_str: str) -> dict:
        """将 Cookie 字符串解析为字典"""
        cookies = {}
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                cookies[key.strip()] = value.strip()
        return cookies

    def _extract_csrf(self) -> str:
        """从 Cookie 中提取 CSRF token（用于 POST 请求）"""
        cookie_dict = self._parse_cookie_string(self.cookie)

        # 尝试多种可能的 CSRF token 字段名
        for key in ("XSRF-TOKEN", "xsrf-token", "_xsrf"):
            if key in cookie_dict:
                token = cookie_dict[key]
                # URL decode if needed
                return requests.utils.unquote(token)

        logger.warning(
            "未能从 Cookie 中提取 CSRF token，发帖功能可能不可用"
        )
        return ""

    def _request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> Optional[dict]:
        """
        发送请求并返回 JSON 响应

        Args:
            method: HTTP 方法 (GET/POST)
            url: 请求 URL
            **kwargs: 传递给 requests 的其他参数

        Returns:
            JSON 响应字典，失败返回 None
        """
        try:
            resp = self.session.request(method, url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except ValueError as e:
            # JSON 解析失败 — 响应不是有效的 JSON
            raw_preview = 'N/A'
            resp_status = 'N/A'
            if 'resp' in locals():
                resp_status = resp.status_code
                try:
                    raw_preview = resp.text[:500]
                except Exception:
                    pass

            # 若响应是 HTML（如被反爬拦截），属于预期行为，降级为 WARNING
            is_html = raw_preview.strip().startswith('<!') or raw_preview.strip().startswith('<html')
            log_level = logger.warning if is_html else logger.error
            log_level(
                f"JSON 解析失败 [{method} {url}]: {e}"
                f" | status={resp_status}"
                f" | 响应预览: {raw_preview}"
            )
            return None
        except requests.RequestException as e:
            # HTTP 错误（4xx, 5xx）或网络错误
            resp_status = 'N/A'
            raw_preview = 'N/A'
            try:
                if hasattr(e, 'response') and e.response is not None:
                    resp_status = e.response.status_code
                    raw_preview = e.response.text[:500]
            except Exception:
                pass

            # 403 且来自 weibo.com 属于反爬拦截（预期行为），降级为 WARNING
            is_expected_403 = (resp_status == 403 and 'weibo.com' in url)
            log_level = logger.warning if is_expected_403 else logger.error
            log_level(
                f"请求失败 [{method} {url}]: {e}"
                f" | status={resp_status}"
                f" | 响应预览: {raw_preview}"
            )
            return None

    def check_session_valid(self) -> bool:
        """
        检测当前 Cookie 是否仍然有效

        Returns:
            True 表示登录态有效
        """
        cookie_dict = self._parse_cookie_string(self.cookie)
        has_pc_cookie = bool(cookie_dict.get("PC_TOKEN") or cookie_dict.get("_s_tentry"))
        has_mobile_cookie = bool(cookie_dict.get("M_WEIBOCN_PARAMS") or cookie_dict.get("MLOGIN"))

        # 有 PC 端 Cookie → 直接判定有效（后续走 Playwright 路线）
        # 无需浪费时间去尝试明知会失败的移动端 API / PC 端 API
        if has_pc_cookie:
            logger.info("检测到 PC 端 Cookie（PC_TOKEN/_s_tentry），登录态有效。")
            logger.info("签到时将使用 Playwright 浏览器绕过反爬系统。")
            return True

        # 纯移动端 Cookie → 调用移动端 API 验证
        if has_mobile_cookie:
            url = f"{self.API_BASE}/config"
            data = self._request("GET", url)
            logger.info(f"/api/config 响应: {data}")

            if data and data.get("data", {}).get("login"):
                uid = data.get("data", {}).get("uid", "未知")
                logger.info(f"Cookie 有效（移动端），当前登录用户 UID: {uid}")
                return True
            logger.warning("移动端验证未通过...")

        # 尝试访问超话页面作为最后手段
        logger.info("尝试通过访问超话页面验证 Cookie...")
        test_url = (
            f"{self.API_BASE}/container/getIndex"
            f"?containerid=1008086f1b7983ba4fca7456e28317e78127ed"
        )
        test_data = self._request("GET", test_url)
        if test_data is not None and test_data.get("ok") == 1:
            logger.info("Cookie 似乎有效（超话页面可访问）")
            return True

        logger.warning("所有验证方式均失败，Cookie 可能已过期或无效")
        return False

    def get_containerid_by_name(self, name: str) -> Optional[str]:
        """
        通过超话名称搜索获取 containerid

        Args:
            name: 超话名称

        Returns:
            containerid 字符串，未找到返回 None
        """
        url = f"{self.API_BASE}/container/getIndex"
        params = {
            "containerid": f"100103type=1&q={name}",
            "page_type": "searchall",
        }
        data = self._request("GET", url, params=params)
        if data is None or data.get("ok") != 1:
            logger.error(f"搜索超话 '{name}' 失败")
            return None

        # 从搜索结果中筛选超话卡片
        cards = data.get("data", {}).get("cards", [])
        for card in cards:
            if card.get("card_type") == 4:  # 超话类型 (card_type=4)
                # card_group 中查找超话信息
                card_group = card.get("card_group", [])
                for group in card_group:
                    title_sub = group.get("title_sub", "")
                    if name in title_sub or name in group.get("title", ""):
                        # 从 scheme 中提取 containerid
                        # 格式: sinaweibo://supergroup?containerid=100808xxx
                        scheme = group.get("scheme", "")
                        # containerid 是 hex 字符串，必须匹配 [a-f0-9]
                        match = re.search(
                            r"containerid=([a-f0-9]+)", scheme
                        )
                        if match:
                            containerid = match.group(1)
                            logger.info(
                                f"超话 '{name}' -> containerid: {containerid}"
                            )
                            return containerid

        logger.warning(f"未找到超话 '{name}'，请确认名称是否正确")
        return None

    def checkin_super_topic(
        self, containerid: str, topic_name: str = None
    ) -> dict:
        """
        在指定超话下签到

        Args:
            containerid: 超话的 containerid（通常以 100808 开头）
            topic_name: 超话名称（可选，不传则尝试从 API 获取）

        Returns:
            {
                "success": bool,
                "message": str,
                "containerid": str,
            }
        """
        result = {
            "success": False,
            "message": "",
            "containerid": containerid,
        }

        # 尝试获取超话名称（如果调用方没提供）
        if not topic_name:
            topic_url = (
                f"{self.API_BASE}/container/getIndex"
                f"?containerid={containerid}"
            )
            topic_data = self._request("GET", topic_url)
            if topic_data is not None and topic_data.get("ok") == 1:
                try:
                    page_info = topic_data.get("data", {})
                    if page_info.get("pageInfo", {}).get("page_title"):
                        topic_name = page_info["pageInfo"]["page_title"]
                    elif page_info.get("pageInfo", {}).get("title"):
                        topic_name = page_info["pageInfo"]["title"]
                    if not topic_name:
                        cards = page_info.get("cards", [])
                        for card in cards:
                            for field in ("title_sub", "title", "item_name", "name"):
                                n = card.get(field, "")
                                if n:
                                    topic_name = n
                                    break
                            if topic_name:
                                break
                except Exception:
                    pass

        if not topic_name:
            topic_name = f"超话({containerid[:12]}...)"
            logger.info(f"未能获取超话名称，使用 ID 标识: {topic_name}")

        # 检测是否有 PC 端 Cookie（weibo.com 域）
        cookie_dict = self._parse_cookie_string(self.cookie)
        has_pc_cookie = bool(cookie_dict.get("PC_TOKEN") or cookie_dict.get("_s_tentry"))

        # ---- 方案 1（主力, PC 端 Cookie 存在时）: Playwright 浏览器签到 ----
        # requests 库 100% 会被 Sina Visitor System 拦截（返回 HTML），
        # 因此直接走 Playwright，不再浪费时间尝试 requests
        if has_pc_cookie:
            logger.info(f"检测到 PC 端 Cookie，使用 Playwright 浏览器签到...")
            pw_result = self._checkin_with_playwright(containerid, topic_name)
            if pw_result["success"]:
                return pw_result
            logger.info(f"Playwright 签到失败，回退到移动端方案...")

        # ---- 方案 2（备用）: 移动端签到按钮接口 ----
        # 当没有 PC 端 Cookie 或 Playwright 方案失败时使用
        result = self._checkin_mobile_api(containerid, topic_name)
        return result

    def cleanup(self):
        """清理资源（关闭 Playwright 浏览器等）"""
        self._close_pw()

    def _get_pw_context(self):
        """获取或创建 Playwright 浏览器上下文（懒加载，复用）"""
        if hasattr(self, '_pw_context') and self._pw_context is not None:
            return self._pw_context

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright 未安装，跳过浏览器签到方案")
            return None

        logger.info("启动 Playwright 浏览器（复用会话）...")
        self._pw_playwright = sync_playwright().start()
        self._pw_browser = self._pw_playwright.chromium.launch(headless=True)
        self._pw_context = self._pw_browser.new_context()

        # 解析并添加 Cookie
        cookie_dict = self._parse_cookie_string(self.cookie)
        pw_cookies = []
        for key, value in cookie_dict.items():
            pw_cookies.append({
                "name": key,
                "value": value,
                "domain": ".weibo.com",
                "path": "/",
            })
        self._pw_context.add_cookies(pw_cookies)

        # 访问 weibo.com 首页完成 Visitor System 验证
        page = self._pw_context.new_page()
        logger.info("访问 weibo.com 建立浏览器会话...")
        page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        page.close()

        return self._pw_context

    def _close_pw(self):
        """关闭 Playwright 浏览器"""
        try:
            if hasattr(self, '_pw_browser') and self._pw_browser:
                self._pw_browser.close()
            if hasattr(self, '_pw_playwright') and self._pw_playwright:
                self._pw_playwright.stop()
        except Exception:
            pass
        self._pw_context = None
        self._pw_browser = None
        self._pw_playwright = None

    def _checkin_with_playwright(self, containerid: str, topic_name: str) -> dict:
        """
        使用 Playwright 浏览器调用签到 API，绕过 Sina Visitor System
        （复用浏览器会话，多次签到只启动一次浏览器）
        """
        result = {
            "success": False,
            "message": "",
            "containerid": containerid,
        }

        context = self._get_pw_context()
        if context is None:
            result["message"] = f"❌ {topic_name} Playwright 未安装"
            return result

        checkin_url = (
            "https://weibo.com/p/aj/general/button"
            "?ajwvr=6"
            "&api=http://i.huati.weibo.com/aj/super/checkin"
            f"&id={containerid}"
            "&location=page_100808_super_index"
        )

        try:
            page = context.new_page()
            logger.info(f"调用签到 API ({topic_name})...")
            api_resp = page.request.get(
                checkin_url,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/plain, */*",
                    "Referer": f"https://weibo.com/p/{containerid}/super_index",
                },
            )

            body = api_resp.text()
            logger.info(f"Playwright 签到响应: status={api_resp.status}, body={body[:300]}")
            page.close()

            try:
                import json
                data = json.loads(body)
            except Exception:
                result["message"] = f"❌ {topic_name} 签到响应解析失败"
                logger.warning(result["message"])
                return result

            code = str(data.get("code", ""))
            msg = data.get("msg", "")

            if code == "100000":
                result["success"] = True
                result["message"] = f"✅ {topic_name} 签到成功（浏览器）: {msg}"
            elif code == "382004" or "已签到" in msg or "already" in msg.lower():
                result["success"] = True
                result["message"] = f"⏭ {topic_name} 今日已签到（浏览器）: {msg}"
            else:
                result["message"] = f"❌ {topic_name} 签到失败（浏览器）: {msg} (code={code})"

            logger.info(result["message"])

        except Exception as e:
            logger.error(f"Playwright 签到异常: {e}")
            result["message"] = f"❌ {topic_name} 浏览器签到异常: {e}"

        return result

    def _checkin_mobile_api(
        self, containerid: str, topic_name: str
    ) -> dict:
        """主力签到方案：使用移动端签到按钮接口"""
        result = {
            "success": False,
            "message": "",
            "containerid": containerid,
        }

        # 移动端签到按钮接口
        checkin_containerid = f"{containerid}_-_checkin"
        url = f"{self.API_BASE}/container/getIndex"
        params = {"containerid": checkin_containerid}

        data = self._request("GET", url, params=params)

        if data is None:
            result["message"] = f"❌ {topic_name} 移动端签到请求失败"
            logger.warning(result["message"])
            return result

        if data.get("ok") != 1:
            logger.warning(
                f"移动端签到响应异常: ok={data.get('ok')}, "
                f"msg={data.get('msg', 'N/A')}"
            )
            result["message"] = f"❌ {topic_name} 移动端签到失败"
            return result

        # ok=1，打印完整响应结构以便分析真正的签到触发方式
        data_block = data.get("data", {})
        logger.info(f"签到页面 data keys: {list(data_block.keys()) if isinstance(data_block, dict) else 'N/A'}")
        if isinstance(data_block, dict):
            cards = data_block.get("cards", [])
            logger.info(f"签到页面 cards 数量: {len(cards)}")
            # 打印所有有 card_group 或特殊字段的卡片
            for i, card in enumerate(cards):
                cg = card.get("card_group", [])
                # 优先打印有 card_group、有按钮、或有签到相关字段的卡片
                has_content = cg or card.get("buttons") or "签到" in str(card)
                if has_content or i < 5:
                    logger.info(
                        f"  card[{i}]: card_type={card.get('card_type')}, "
                        f"title={str(card.get('title', ''))[:60]}, "
                        f"title_sub={str(card.get('title_sub', ''))[:60]}, "
                        f"itemid={str(card.get('itemid', ''))[:80]}, "
                        f"card_group数={len(cg)}, "
                        f"buttons={str(card.get('buttons', ''))[:80]}"
                    )
                    for j, group in enumerate(cg[:5]):
                        non_empty = {k: str(v)[:100] for k, v in group.items() if v}
                        if non_empty:
                            logger.info(f"    group[{j}]: {non_empty}")

        # 之前的简化判断逻辑（待分析完数据结构后更新）
        msg = ""
        if isinstance(data_block, dict):
            msg = data_block.get("msg", "")
            cards = data_block.get("cards", [])
            for card in cards:
                card_group = card.get("card_group", [])
                for group in card_group:
                    for field in ("title_sub", "desc1", "desc2", "item_name"):
                        text = group.get(field, "")
                        if "已签到" in text or "签到成功" in text or "连续签到" in text:
                            msg = text
                            break
                    if msg:
                        break
                if msg:
                    break
            if not msg and cards:
                for card in cards:
                    title = card.get("title_sub", "") or card.get("title", "")
                    if "签到" in title:
                        msg = title
                        break

        if "已签到" in msg:
            result["success"] = True
            result["message"] = f"⏭ {topic_name} 今日已签到"
        elif "签到成功" in msg or "成功" in msg:
            result["success"] = True
            result["message"] = f"✅ {topic_name} 签到成功: {msg}"
        elif "连续签到" in msg:
            result["success"] = True
            result["message"] = f"✅ {topic_name} 签到成功（{msg}）"
        else:
            # 没有找到签到成功的标志 — 说明只是读取了页面但未真正签到
            result["success"] = False
            result["message"] = f"❌ {topic_name} 移动端签到未确认: 页面可访问但未找到签到成功标志"
            logger.warning(result["message"])

        return result

    def _get_fresh_st(self) -> str:
        """
        从 /api/config 获取最新的 st (CSRF token)。
        微博的 st 是动态的，Cookie 里的 XSRF-TOKEN 可能是旧的。
        """
        try:
            data = self._request("GET", f"{self.API_BASE}/config")
            if data and data.get("ok") == 1:
                st = data.get("data", {}).get("st", "")
                if st:
                    logger.info(f"获取到最新 st: {st}")
                    return st
        except Exception:
            pass
        # fallback 到 Cookie 中的值
        logger.warning("无法获取最新 st，使用 Cookie 中的 XSRF-TOKEN")
        return self._csrf_token

    def post_weibo(self, content: str, containerid: str = None) -> dict:
        """
        发布微博（支持发布到指定超话内部）

        Args:
            content: 微博正文内容
            containerid: 超话 containerid（可选）。
                        传入后，微博将发布到该超话内部而非个人主页。

        Returns:
            {
                "success": bool,
                "message": str,
                "weibo_id": str,
            }
        """
        result = {
            "success": False,
            "message": "",
            "weibo_id": "",
        }

        if not content.strip():
            result["message"] = "内容为空，不执行发帖"
            logger.warning(result["message"])
            return result

        # 字数限制检查
        if len(content) > 2000:
            content = content[:1990] + "..."
            logger.warning("内容过长，已截断")

        # 检测是否有 PC 端 Cookie — 优先使用 Playwright PC 端发帖
        cookie_dict = self._parse_cookie_string(self.cookie)
        has_pc_cookie = bool(cookie_dict.get("PC_TOKEN") or cookie_dict.get("_s_tentry"))

        if has_pc_cookie:
            logger.info("检测到 PC 端 Cookie，使用 Playwright PC 端发帖...")
            return self._post_with_playwright(content, containerid)

        # ---- 以下为移动端发帖流程 ----

        # 获取最新的 st（CSRF token），Cookie 中的可能已过期
        st = self._get_fresh_st()
        if not st:
            result["message"] = "缺少 CSRF token，无法发帖"
            logger.error(result["message"])
            return result

        url = f"{self.API_BASE}/statuses/update"
        headers = {
            "X-XSRF-TOKEN": st,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data_payload = {
            "content": content,
            "st": st,
        }

        if containerid:
            data_payload["page_id"] = containerid
            headers["Referer"] = (
                f"https://m.weibo.cn/compose/?page_id={containerid}"
            )
            logger.info(
                f"发帖请求（移动端-超话内部）: URL={url}, "
                f"containerid={containerid}, content_len={len(content)}"
            )
        else:
            headers["Referer"] = "https://m.weibo.cn/compose/"
            logger.info(
                f"发帖请求（移动端-普通）: URL={url}, "
                f"content_len={len(content)}"
            )

        data = self._request(
            "POST", url, headers=headers, data=data_payload
        )

        if data is None:
            result["message"] = "发帖请求失败（网络错误或非JSON响应）"
            logger.error(result["message"])
            return result

        logger.info(
            f"发帖响应: ok={data.get('ok')}, "
            f"errno={data.get('errno', 'N/A')}, "
            f"msg={data.get('msg', 'N/A')}"
        )

        if data.get("ok") == 1:
            weibo_id = data.get("data", {}).get("id", "")
            result["success"] = True
            result["weibo_id"] = str(weibo_id)
            where = "超话内" if containerid else "个人主页"
            result["message"] = f"✅ 发布成功（{where}），微博 ID: {weibo_id}"
            logger.info(result["message"])
        else:
            errno = data.get("errno", "")
            errmsg = data.get("msg", data.get("errmsg", "未知错误"))
            result["message"] = f"❌ 发帖失败: {errmsg} (errno={errno})"
            logger.warning(result["message"])

            if containerid and errno:
                logger.warning(
                    "超话内部发帖失败，page_id 参数可能需要调整。"
                    "建议通过浏览器 DevTools 抓包确认正确的请求参数。"
                )

        return result

    def _post_with_playwright(
        self, content: str, containerid: str = None
    ) -> dict:
        """
        使用 Playwright 浏览器在 PC 端发帖（weibo.com/aj/mblog/add）
        复用签到时的浏览器上下文
        """
        result = {
            "success": False,
            "message": "",
            "weibo_id": "",
        }

        context = self._get_pw_context()
        if context is None:
            result["message"] = "Playwright 未安装，无法使用 PC 端发帖"
            logger.error(result["message"])
            return result

        # 从 Cookie 获取 st
        cookie_dict = self._parse_cookie_string(self.cookie)
        st = cookie_dict.get("XSRF-TOKEN", "")

        page = context.new_page()

        try:
            # PC 端发帖 API
            post_url = "https://weibo.com/aj/mblog/add"
            post_data = {
                "text": content,
                "st": st,
                "_spr": "screen:1920x1080",
            }

            if containerid:
                # 超话内发帖
                post_data["pub_source"] = "page_100808_super_index"
                post_data["pdetail"] = containerid
                post_data["location"] = "page_100808_super_index"
                referer = f"https://weibo.com/p/{containerid}/super_index"
                logger.info(
                    f"发帖请求（PC端-超话内部）: containerid={containerid}, "
                    f"content_len={len(content)}"
                )
            else:
                # 个人主页发帖
                post_data["pub_source"] = "mainpage"
                referer = "https://weibo.com/"
                logger.info(
                    f"发帖请求（PC端-普通）: content_len={len(content)}"
                )

            api_resp = page.request.post(
                post_url,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Referer": referer,
                },
                form=post_data,
            )

            body = api_resp.text()
            logger.info(f"PC端发帖响应: status={api_resp.status}, body[:200]={body[:200]}")

            try:
                import json
                data = json.loads(body)
            except Exception:
                result["message"] = "发帖响应解析失败"
                logger.error(result["message"])
                return result

            code = str(data.get("code", ""))

            if code == "100000":
                # 从 HTML 中提取微博 ID (mid)
                html = data.get("data", {}).get("html", "")
                weibo_id = ""
                import re as re_mod
                mid_match = re_mod.search(r'mid="(\d+)"', html)
                if mid_match:
                    weibo_id = mid_match.group(1)

                result["success"] = True
                result["weibo_id"] = weibo_id
                where = "超话内" if containerid else "个人主页"
                result["message"] = f"✅ 发布成功（PC端-{where}），微博 ID: {weibo_id}"
                logger.info(result["message"])
            else:
                msg = data.get("msg", "")
                result["message"] = f"❌ 发帖失败（PC端）: {msg} (code={code})"
                logger.warning(result["message"])

        except Exception as e:
            logger.error(f"PC端发帖异常: {e}")
            result["message"] = f"❌ PC端发帖异常: {e}"
        finally:
            page.close()

        return result
