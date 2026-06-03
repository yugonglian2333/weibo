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

        自动尝试多种搜索词变体（原名、原名+超话后缀）。
        PC 端 Cookie 场景下跳过 requests 直接走 Playwright（避免触发验证码）。

        Args:
            name: 超话名称

        Returns:
            containerid 字符串，未找到返回 None
        """
        # 判断是否为 PC 端 Cookie
        cookie_dict = self._parse_cookie_string(self.cookie)
        is_pc_cookie = bool(cookie_dict.get("PC_TOKEN") or cookie_dict.get("_s_tentry"))

        # 构建搜索词变体列表
        search_names = [name]
        if "超话" not in name:
            search_names.append(name + "超话")

        for search_name in search_names:
            logger.info(f"搜索 '{search_name}'...")

            if not is_pc_cookie:
                # 移动端 Cookie：requests 优先
                url = f"{self.API_BASE}/container/getIndex"
                params = {
                    "containerid": f"100103type=1&q={search_name}",
                    "page_type": "searchall",
                }
                data = self._request("GET", url, params=params)
                if data is not None and data.get("ok") == 1:
                    cid = self._parse_containerid_from_search(data, search_name)
                    if cid:
                        return cid
                logger.info("移动端 API 搜索失败，尝试 Playwright 浏览器搜索...")
            else:
                logger.info("PC 端 Cookie，直接使用 Playwright 浏览器搜索...")

            # Playwright 浏览器搜索
            cid = self._search_containerid_with_playwright(search_name)
            if cid:
                return cid

        logger.warning(f"未找到超话 '{name}'，请确认名称是否正确")
        return None

    def _parse_containerid_from_search(
        self, data: dict, name: str
    ) -> Optional[str]:
        """
        从移动端搜索 API 响应中解析 containerid

        超话卡片 (card_type=4) 可能出现在两个位置：
        1. 搜索结果顶层的 card
        2. 嵌套在其他 card 的 card_group 中

        注意：卡片中的 title/title_sub 可能为空（搜索 API 已按名称过滤），
        此时只要是 card_type=4 且有有效 containerid 就返回。
        """
        cards = data.get("data", {}).get("cards", [])
        search_name = name.replace("超话", "")

        for card in cards:
            # 遍历顶层 card 及所有 card_group 子项
            all_groups = list(card.get("card_group", []))
            if card.get("card_type") == 4:
                all_groups.insert(0, card)

            for group in all_groups:
                if group.get("card_type") != 4:
                    continue
                title_sub = group.get("title_sub", "")
                title = group.get("title", "")
                scheme = group.get("scheme", "")

                # 提取 containerid
                match = re.search(
                    r"containerid=([a-f0-9]+)", scheme
                )
                if not match:
                    continue
                containerid = match.group(1)

                # 名称匹配：放宽条件，title 为空时也接受
                # （搜索 API 已按名称过滤，card_type=4 即为目标超话）
                name_matched = (
                    search_name in title_sub
                    or search_name in title
                    or name in title_sub
                    or name in title
                )
                if name_matched or (not title and not title_sub):
                    logger.info(
                        f"超话 '{title or title_sub or name}' -> containerid: {containerid}"
                    )
                    return containerid

        return None

    def _search_containerid_with_playwright(
        self, name: str
    ) -> Optional[str]:
        """
        使用 Playwright 浏览器在 weibo.com PC 端搜索超话

        PC 端 Cookie 无法通过 m.weibo.cn 移动端 API 的登录验证（返回
        retcode=6102 / ok=-100），因此改用 weibo.com PC 端搜索：

        1. 登录 weibo.com → 调用 ajax/statuses/search 搜索帖子
        2. 从帖子中提取所有 100808 开头的超话 containerid（按频率排序）
        3. 逐个访问超话页面，从页面标题提取超话名称
        4. 精确匹配优先（"鞠婧祎超话" > "鞠婧祎周边市场超话"）
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright 未安装，无法搜索超话")
            return None

        import json as _json

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context()

                # 添加 Cookie（PC 端只需 weibo.com 域）
                cookie_dict = self._parse_cookie_string(self.cookie)
                pw_cookies = []
                for key, value in cookie_dict.items():
                    for domain in (".weibo.com", "weibo.com"):
                        pw_cookies.append({
                            "name": key, "value": value,
                            "domain": domain, "path": "/",
                        })
                ctx.add_cookies(pw_cookies)

                page = ctx.new_page()

                # 登录 weibo.com PC 端
                logger.info("登录 weibo.com（PC 端）...")
                page.goto(
                    "https://weibo.com/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                page.wait_for_timeout(3000)

                # —— 第 1 步：搜索帖子，提取超话 containerid 候选项 ——
                search_names = [name]
                if "超话" not in name:
                    search_names.append(name + "超话")

                all_candidates = {}  # {cid: 出现次数}

                for sname in search_names:
                    logger.info(f"PC 端搜索: {sname}")
                    resp = page.request.get(
                        "https://weibo.com/ajax/statuses/search",
                        params={"q": sname, "type": "all", "page": "1"},
                        headers={
                            "X-Requested-With": "XMLHttpRequest",
                            "Accept": "application/json",
                            "Referer": "https://weibo.com/",
                        },
                    )
                    if resp.status != 200:
                        continue
                    try:
                        data = _json.loads(resp.text())
                    except Exception:
                        continue
                    if data.get("ok") != 1:
                        continue

                    # 提取所有 100808 开头的超话 containerid
                    flat = _json.dumps(data, ensure_ascii=False)
                    cids = re.findall(r"(100808[a-f0-9]{26,})", flat)
                    for cid in cids:
                        all_candidates[cid] = all_candidates.get(cid, 0) + 1

                if not all_candidates:
                    logger.warning(f"PC 端搜索未找到超话候选项: {name}")
                    page.close()
                    browser.close()
                    return None

                # 按出现频率降序排列（主超话出现频率最高）
                sorted_candidates = sorted(
                    all_candidates.items(), key=lambda x: -x[1]
                )
                logger.info(
                    f"找到 {len(sorted_candidates)} 个候选超话，"
                    f"按频率排序: {[(c[0][:16] + '...', c[1]) for c in sorted_candidates[:5]]}"
                )

                # —— 第 2 步：逐个访问候选超话页面，验证名称 ——
                search_name_clean = name.replace("超话", "")

                best_partial_match = None  # (cid, topic_name_clean, extra_len)

                for cid, freq in sorted_candidates[:5]:
                    try:
                        topic_url = (
                            f"https://weibo.com/p/{cid}/super_index"
                        )
                        resp2 = page.goto(
                            topic_url,
                            wait_until="domcontentloaded",
                            timeout=15000,
                        )
                        page.wait_for_timeout(1500)

                        if "sorry" in page.url or resp2.status != 200:
                            continue

                        # 从页面标题提取超话名称
                        page_title = page.title()
                        content = page.content()
                        topic_name = None

                        if page_title:
                            topic_name = page_title
                        # 也尝试从页面内嵌 JSON 中提取
                        title_matches = re.findall(
                            r'"page_title"\s*:\s*"([^"]+)"', content
                        )
                        if title_matches:
                            topic_name = title_matches[0]

                        if not topic_name:
                            continue

                        # 清洗名称：去掉 "超话" 后缀和 "-微博超话社区" 等
                        topic_name_clean = (
                            topic_name
                            .replace("-微博超话社区", "")
                            .replace("超话", "")
                            .strip()
                        )

                        # 精确匹配：立即返回
                        if topic_name_clean == search_name_clean:
                            logger.info(
                                f"精确匹配: 「{topic_name_clean}」"
                                f" -> containerid: {cid}"
                            )
                            page.close()
                            browser.close()
                            return cid

                        # 反向包含：候选名称是搜索词的子串
                        # 例: 搜索"鞠婧祎阿黛" → 候选"鞠婧祎" in 搜索词 ✓
                        if topic_name_clean and topic_name_clean in search_name_clean:
                            logger.info(
                                f"反向匹配: 「{topic_name_clean}」"
                                f" 在搜索词「{search_name_clean}」中"
                                f" -> containerid: {cid}"
                            )
                            page.close()
                            browser.close()
                            return cid

                        # 模糊匹配：记录最佳候选项
                        if search_name_clean in topic_name_clean:
                            extra = topic_name_clean.replace(
                                search_name_clean, ""
                            )
                            # 额外字符 <= 2 视为近似匹配，立即返回
                            if len(extra) <= 2:
                                logger.info(
                                    f"近似匹配: 「{topic_name_clean}」"
                                    f" -> containerid: {cid}"
                                )
                                page.close()
                                browser.close()
                                return cid
                            # 额外字符 > 2：记录为备选（选最短的）
                            if (
                                best_partial_match is None
                                or len(extra) < best_partial_match[2]
                            ):
                                best_partial_match = (
                                    cid,
                                    topic_name_clean,
                                    len(extra),
                                )
                                logger.info(
                                    f"备选匹配: 「{topic_name_clean}」"
                                    f"（包含 '{search_name_clean}'，"
                                    f"额外 {len(extra)} 字符）"
                                )
                    except Exception as e:
                        logger.debug(
                            f"检查候选 {cid[:20]}... 失败: {e}"
                        )
                        continue

                # 没有精确匹配时，返回最佳模糊匹配
                if best_partial_match:
                    cid, topic_name_clean, _ = best_partial_match
                    logger.info(
                        f"使用最佳模糊匹配: 「{topic_name_clean}」"
                        f" -> containerid: {cid}"
                    )
                    page.close()
                    browser.close()
                    return cid

                # 兜底：没有任何匹配规则命中，但候选存在
                # 如果只有 1 个候选 或 最高频候选频率远超其他，直接返回
                if len(sorted_candidates) == 1:
                    cid = sorted_candidates[0][0]
                    logger.info(f"唯一候选，直接使用: containerid={cid}")
                    page.close()
                    browser.close()
                    return cid
                if (len(sorted_candidates) >= 1
                        and sorted_candidates[0][1] > sorted_candidates[1][1] * 5):
                    cid = sorted_candidates[0][0]
                    logger.info(
                        f"最高频候选远超其他（{sorted_candidates[0][1]} vs "
                        f"{sorted_candidates[1][1]}），直接使用: containerid={cid}"
                    )
                    page.close()
                    browser.close()
                    return cid

                page.close()
                browser.close()
                logger.warning(f"未找到匹配超话: {name}")
                return None

        except Exception as e:
            logger.error(f"PC 端搜索超话 '{name}' 异常: {e}")
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

    def get_super_topic_posts(
        self, containerid: str, count: int = 3
    ) -> list[dict]:
        """
        获取超话下的前 N 个帖子

        Args:
            containerid: 超话的 containerid
            count: 获取帖子数量（默认 3）

        Returns:
            [{"id": str, "mid": str, "text": str, "user": str}, ...]
            其中 text 已去除 HTML 标签，mid 用于评论 API
        """

        # 检测 Cookie 类型：PC 端 Cookie 无法通过移动端 API 获取帖子
        cookie_dict = self._parse_cookie_string(self.cookie)
        has_pc_cookie = bool(
            cookie_dict.get("PC_TOKEN") or cookie_dict.get("_s_tentry")
        )

        # PC 端 Cookie → 优先使用 Playwright 浏览器获取帖子
        if has_pc_cookie:
            logger.info("PC 端 Cookie，使用 Playwright 获取超话帖子...")
            pw_posts = self._get_super_topic_posts_with_playwright(
                containerid, count
            )
            if pw_posts:
                logger.info(
                    f"PC 端获取到 {len(pw_posts)} 条帖子 "
                    f"(containerid={containerid})"
                )
                return pw_posts
            logger.warning(
                "PC 端获取帖子失败，尝试移动端 API..."
            )

        # 移动端 Cookie 或 PC 端回退 → 移动端 API
        url = f"{self.API_BASE}/container/getIndex"
        params = {"containerid": containerid, "page": 1}
        data = self._request("GET", url, params=params)

        posts = []
        if data is None or data.get("ok") != 1:
            logger.warning(f"获取超话帖子失败: containerid={containerid}")
            return posts

        cards = data.get("data", {}).get("cards", [])

        for card in cards:
            # 有些帖子直接就是卡片，有些嵌套在 card_group 中
            candidates = []
            card_type = card.get("card_type")
            if card_type in (9, 11, 0):
                candidates.append(card)
            for group in card.get("card_group", []):
                group_type = group.get("card_type")
                if group_type in (9, 11):
                    candidates.append(group)

            for c in candidates:
                mblog = c.get("mblog", {})
                if not mblog:
                    continue

                text_raw = mblog.get("text", "")
                # 去除 HTML 标签
                text_clean = re.sub(r"<[^>]*>", "", text_raw).strip()
                # 截取前 200 字给 AI 做上下文
                text_preview = text_clean[:200]

                post = {
                    "id": mblog.get("id", ""),
                    "mid": mblog.get("mid", ""),
                    "text": text_preview,
                    "user": mblog.get("user", {}).get("screen_name", ""),
                }
                posts.append(post)

                if len(posts) >= count:
                    break

            if len(posts) >= count:
                break

        logger.info(
            f"获取到 {len(posts)} 条帖子 (containerid={containerid})"
        )
        return posts

    def _get_super_topic_posts_with_playwright(
        self, containerid: str, count: int = 3
    ) -> list[dict]:
        """
        使用 Playwright 浏览器获取超话帖子列表

        核心思路：requests 库调移动端 API 会被 Sina Visitor System 拦截，
        但 Playwright 的 page.request（APIRequestContext）使用浏览器级
        HTTP 栈（TLS 指纹 + Cookie 共享），可以绕过反爬，正常拿到 JSON。

        三级回退策略：
        1. PC 端超话 API (ajax_proxy/chaohua/page)  —— 实测有效，主力方案
        2. 导航到超话页面，从 article DOM 中提取
        3. 移动端 API 作为最后手段
        """
        posts = []

        context = self._get_pw_context()
        if context is None:
            logger.warning("Playwright 未安装，无法获取超话帖子")
            return posts

        page = context.new_page()

        try:
            import json as _json

            topic_url = (
                f"https://weibo.com/p/{containerid}/super_index"
            )

            # — 方案 1: PC 端超话 API（实际页面使用的接口，实测有效）—
            logger.info("尝试 PC 端超话 API (ajax_proxy/chaohua/page)...")

            # 先导航到超话页面，让浏览器建立正确的会话上下文
            page.goto(
                topic_url,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(3000)

            chaohua_api = "https://weibo.com/ajax_proxy/chaohua/page"
            api_resp = page.request.get(
                chaohua_api,
                params={"flowId": containerid},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/plain, */*",
                    "Referer": topic_url,
                },
            )

            if api_resp.status == 200:
                try:
                    data = _json.loads(api_resp.text())
                except Exception:
                    data = None

                if data:
                    # 该 API 返回 {"items": [...]}，每个 item 有 category 字段
                    # 帖子对应的 item category 为 "feed"
                    items = data.get("items", [])
                    for item in items:
                        if item.get("category") != "feed":
                            continue
                        d = item.get("data", {})
                        if not d:
                            continue
                        text_raw = d.get("text_raw", "") or d.get("text", "")
                        text_clean = re.sub(
                            r"<[^>]*>", "", text_raw
                        ).strip()[:200]
                        posts.append({
                            "id": str(d.get("id", "")),
                            "mid": str(d.get("mid", "")),
                            "text": text_clean,
                            "user": d.get("user", {}).get("screen_name", ""),
                        })
                        if len(posts) >= count:
                            break

                    if posts:
                        logger.info(
                            f"PC 端超话 API 获取到 {len(posts)} 条帖子"
                        )
                    else:
                        logger.info(
                            f"PC 端超话 API: items={len(items)}, "
                            f"但未找到 feed 类目"
                        )
                else:
                    logger.info("PC 端超话 API: 响应解析失败")
            else:
                logger.info(
                    f"PC 端超话 API: HTTP {api_resp.status}"
                )

            # — 方案 2: 导航到超话页面，从 article DOM 提取 —
            # PC 端页面不使用 [mid] 属性，帖子在 <article> 元素中
            # 但 mid 可以从 article 内部的链接中提取
            if not posts:
                logger.info(
                    "API 方案失败，从超话页面 article DOM 提取..."
                )
                # 如果之前没导航到页面（方案 1 没执行到），现在导航
                if "super_index" not in page.url:
                    page.goto(
                        topic_url,
                        wait_until="networkidle",
                        timeout=30000,
                    )
                    page.wait_for_timeout(3000)
                else:
                    # 已在方案 1 中导航过，等 JS 完全渲染
                    page.wait_for_timeout(3000)

                logger.info(
                    f"超话页面: title={page.title()}, "
                    f"url={page.url[:120]}"
                )

                # 从 article 元素中提取帖子 mid 和文本
                dom_posts = page.evaluate("""() => {
                    var results = [];
                    var articles = document.querySelectorAll('article');
                    for (var i = 0; i < articles.length; i++) {
                        var art = articles[i];
                        var text = (art.textContent || '')
                            .trim().substring(0, 200);

                        // 尝试从链接提取 mid: /detail/{mid}
                        var mid = '';
                        var links = art.querySelectorAll('a[href*="/detail/"]');
                        for (var l of links) {
                            var m = l.href.match(/\\/detail\\/([A-Za-z0-9]+)/);
                            if (m) { mid = m[1]; break; }
                        }

                        // 回退：尝试 data/mid 属性
                        if (!mid) {
                            var attrs = ['mid', 'data-mid', 'data-id', 'action-data'];
                            for (var a of attrs) {
                                var val = art.getAttribute(a);
                                if (val) {
                                    var dm = val.match(/mid=(\\d{10,})/);
                                    if (dm) { mid = dm[1]; break; }
                                    if (/^\\d{10,}$/.test(val)) {
                                        mid = val; break;
                                    }
                                }
                            }
                        }

                        if (text) {
                            results.push({mid: mid, text: text});
                        }
                        if (results.length >= 10) break;
                    }
                    return results;
                }""")
                for dp in dom_posts[:count]:
                    posts.append({
                        "id": dp.get("mid", ""),
                        "mid": dp.get("mid", ""),
                        "text": dp.get("text", ""),
                        "user": "",
                    })
                if posts:
                    logger.info(
                        f"DOM (article) 提取到 {len(posts)} 条帖子"
                    )
                else:
                    logger.info(
                        f"DOM 提取失败，article 元素数={len(dom_posts)}"
                    )

            # — 方案 3: 回退到移动端 API —
            if not posts:
                logger.info(
                    "PC 端方案均失败，尝试移动端 API..."
                )
                mobile_url = (
                    f"{self.API_BASE}/container/getIndex"
                    f"?containerid={containerid}&page=1"
                )
                mobile_resp = page.request.get(
                    mobile_url,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json, text/plain, */*",
                        "Referer": "https://m.weibo.cn/",
                        "User-Agent": (
                            "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 "
                            "like Mac OS X) AppleWebKit/605.1.15 "
                            "(KHTML, like Gecko) Version/15.0 "
                            "Mobile/15E148 Safari/604.1"
                        ),
                    },
                )

                if mobile_resp.status == 200:
                    raw_text = mobile_resp.text()
                    logger.info(
                        f"移动端 API 响应: status={mobile_resp.status}, "
                        f"len={len(raw_text)}, "
                        f"preview={raw_text[:200]}"
                    )
                    try:
                        data = _json.loads(raw_text)
                    except Exception:
                        logger.warning(
                            f"移动端 API 返回非 JSON，"
                            f"前200字符: {raw_text[:200]}"
                        )
                        data = None

                    if data and data.get("ok") == 1:
                        cards = data.get("data", {}).get("cards", [])
                        for card in cards:
                            candidates = []
                            card_type = card.get("card_type")
                            if card_type in (9, 11, 0):
                                candidates.append(card)
                            for group in card.get("card_group", []):
                                group_type = group.get("card_type")
                                if group_type in (9, 11):
                                    candidates.append(group)

                            for c in candidates:
                                mblog = c.get("mblog", {})
                                if not mblog:
                                    continue
                                text_raw = mblog.get("text", "")
                                text_clean = re.sub(
                                    r"<[^>]*>", "", text_raw
                                ).strip()[:200]
                                posts.append({
                                    "id": mblog.get("id", ""),
                                    "mid": mblog.get("mid", ""),
                                    "text": text_clean,
                                    "user": mblog.get("user", {}).get(
                                        "screen_name", ""
                                    ),
                                })
                                if len(posts) >= count:
                                    break
                            if len(posts) >= count:
                                break
                        if posts:
                            logger.info(
                                f"移动端 API 获取到 {len(posts)} 条帖子"
                            )
                    else:
                        ok_val = data.get("ok") if data else "N/A"
                        logger.info(f"移动端 API: ok={ok_val}")
                else:
                    logger.info(
                        f"移动端 API: HTTP {mobile_resp.status}"
                    )

        except Exception as e:
            logger.warning(f"Playwright 获取超话帖子异常: {e}")
        finally:
            page.close()

        logger.info(
            f"Playwright 获取帖子结果: {len(posts)} 条 "
            f"(containerid={containerid})"
        )
        return posts

    def comment_post(
        self, post_mid: str, content: str, post_id: str = ""
    ) -> dict:
        """
        评论一条微博

        Args:
            post_mid: 微博 mid（评论 API 使用）
            content: 评论内容
            post_id: 微博 id（可选，用于 Referer）

        Returns:
            {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        if not content.strip():
            result["message"] = "评论内容为空，跳过"
            return result

        # 字数截断（微博评论限制）
        if len(content) > 140:
            content = content[:137] + "..."

        # 检测 Cookie 类型
        cookie_dict = self._parse_cookie_string(self.cookie)
        has_pc_cookie = bool(
            cookie_dict.get("PC_TOKEN") or cookie_dict.get("_s_tentry")
        )

        if has_pc_cookie:
            logger.info("检测到 PC 端 Cookie，使用 Playwright 评论...")
            return self._comment_with_playwright(post_mid, content)

        # ---- 移动端评论 ----
        st = self._get_fresh_st()
        if not st:
            result["message"] = "缺少 CSRF token，无法评论"
            logger.warning(result["message"])
            return result

        url = f"{self.API_BASE}/api/comments/create"
        headers = {
            "X-XSRF-TOKEN": st,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": (
                f"https://m.weibo.cn/detail/{post_id}"
                if post_id
                else "https://m.weibo.cn/"
            ),
        }
        data_payload = {
            "content": content,
            "mid": post_mid,
            "st": st,
        }

        resp_data = self._request(
            "POST", url, headers=headers, data=data_payload
        )

        if resp_data is None:
            result["message"] = f"❌ 评论请求失败（网络错误）mid={post_mid}"
            logger.warning(result["message"])
            return result

        if resp_data.get("ok") == 1:
            result["success"] = True
            result["message"] = f"✅ 评论成功: {content[:30]}..."
        else:
            errmsg = resp_data.get("msg", "未知错误")
            errno = resp_data.get("errno", "")
            result["message"] = (
                f"❌ 评论失败: {errmsg} (errno={errno})"
            )

        logger.info(result["message"])
        return result

    def _comment_with_playwright(
        self, post_mid: str, content: str
    ) -> dict:
        """
        使用 Playwright 浏览器在 PC 端评论
        复用签到时的浏览器上下文

        PC 端真实评论 API:
        POST https://weibo.com/ajax/comments/create
        form: id={post_mid}&comment={content}&pic_id=&is_repost=0&
              comment_ori=0&is_comment=0
        header: x-xsrf-token={st}
        """
        result = {"success": False, "message": ""}

        context = self._get_pw_context()
        if context is None:
            result["message"] = "Playwright 未安装，无法使用 PC 端评论"
            logger.warning(result["message"])
            return result

        cookie_dict = self._parse_cookie_string(self.cookie)
        st = cookie_dict.get("XSRF-TOKEN", "")

        page = context.new_page()

        try:
            # 使用真实有效的 PC 端评论 API（非 aj/v6/comment/add）
            comment_url = "https://weibo.com/ajax/comments/create"
            api_resp = page.request.post(
                comment_url,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
                    "x-xsrf-token": st,
                    "Referer": f"https://weibo.com/{post_mid}",
                },
                form={
                    "id": post_mid,
                    "comment": content,
                    "pic_id": "",
                    "is_repost": "0",
                    "comment_ori": "0",
                    "is_comment": "0",
                },
            )

            body = api_resp.text()
            logger.info(
                f"PC端评论响应: status={api_resp.status}, "
                f"body[:200]={body[:200]}"
            )

            try:
                import json

                data = json.loads(body)
            except Exception:
                result["message"] = "评论响应解析失败"
                logger.warning(result["message"])
                return result

            # 新的 API 返回 {"ok": 1} 表示成功
            if data.get("ok") == 1:
                result["success"] = True
                result["message"] = (
                    f"✅ 评论成功（PC端）: {content[:30]}..."
                )
            else:
                msg = data.get("msg", "")
                code = data.get("code", "")
                result["message"] = (
                    f"❌ 评论失败（PC端）: {msg}"
                    f"{' (code=' + str(code) + ')' if code else ''}"
                )

            logger.info(result["message"])

        except Exception as e:
            logger.error(f"PC端评论异常: {e}")
            result["message"] = f"❌ PC端评论异常: {e}"
        finally:
            page.close()

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
        self._pw_browser = self._pw_playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._pw_context = self._pw_browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        # 解析并添加 Cookie（同时覆盖 .weibo.com 和 .weibo.cn 域）
        cookie_dict = self._parse_cookie_string(self.cookie)
        pw_cookies = []
        for key, value in cookie_dict.items():
            pw_cookies.append({
                "name": key,
                "value": value,
                "domain": ".weibo.com",
                "path": "/",
            })
            pw_cookies.append({
                "name": key,
                "value": value,
                "domain": ".weibo.cn",
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

    # ============================================================
    # 转发微博
    # ============================================================

    def repost_weibo(
        self, post_id: str, post_mid: str, content: str = "转发微博"
    ) -> dict:
        """
        转发一条微博

        Args:
            post_id: 微博 id
            post_mid: 微博 mid
            content: 转发语（默认"转发微博"，上限140字）

        Returns:
            {"success": bool, "message": str}
        """
        result = {"success": False, "message": ""}

        # 字数截断
        if len(content) > 140:
            content = content[:137] + "..."

        # 检测 Cookie 类型
        cookie_dict = self._parse_cookie_string(self.cookie)
        has_pc_cookie = bool(
            cookie_dict.get("PC_TOKEN") or cookie_dict.get("_s_tentry")
        )

        if has_pc_cookie:
            logger.info("检测到 PC 端 Cookie，使用 Playwright 转发...")
            return self._repost_with_playwright(post_id, post_mid, content)

        # ---- 移动端转发 ----
        return self._repost_mobile_api(post_id, post_mid, content)

    def _repost_mobile_api(
        self, post_id: str, post_mid: str, content: str
    ) -> dict:
        """移动端转发：POST m.weibo.cn/api/statuses/repost"""
        result = {"success": False, "message": ""}

        st = self._get_fresh_st()
        if not st:
            result["message"] = "缺少 CSRF token，无法转发"
            logger.warning(result["message"])
            return result

        url = f"{self.API_BASE}/api/statuses/repost"
        headers = {
            "X-XSRF-TOKEN": st,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": (
                f"https://m.weibo.cn/detail/{post_id}"
                if post_id
                else "https://m.weibo.cn/"
            ),
        }
        data_payload = {
            "id": post_id,
            "mid": post_mid,
            "content": content,
            "st": st,
        }

        resp_data = self._request(
            "POST", url, headers=headers, data=data_payload
        )

        if resp_data is None:
            result["message"] = f"❌ 转发请求失败（网络错误）id={post_id}"
            logger.warning(result["message"])
            return result

        if resp_data.get("ok") == 1:
            result["success"] = True
            result["message"] = f"✅ 转发成功: {content[:30]}..."
        else:
            errmsg = resp_data.get("msg", "未知错误")
            errno = resp_data.get("errno", "")
            result["message"] = (
                f"❌ 转发失败: {errmsg} (errno={errno})"
            )

        logger.info(result["message"])
        return result

    def _repost_with_playwright(
        self, post_id: str, post_mid: str, content: str
    ) -> dict:
        """
        使用 Playwright 浏览器在 PC 端转发
        POST https://weibo.com/aj/v6/mblog/forward
        """
        result = {"success": False, "message": ""}

        context = self._get_pw_context()
        if context is None:
            result["message"] = "Playwright 未安装，无法使用 PC 端转发"
            logger.warning(result["message"])
            return result

        cookie_dict = self._parse_cookie_string(self.cookie)
        st = cookie_dict.get("XSRF-TOKEN", "")

        page = context.new_page()

        try:
            forward_url = "https://weibo.com/aj/v6/mblog/forward"
            api_resp = page.request.post(
                forward_url,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": (
                        "application/x-www-form-urlencoded; charset=UTF-8"
                    ),
                    "Referer": f"https://weibo.com/{post_id}",
                },
                form={
                    "id": post_mid,
                    "mid": post_mid,
                    "reason": content,
                    "st": st,
                    "_t": "0",
                },
            )

            body = api_resp.text()
            logger.info(
                f"PC端转发响应: status={api_resp.status}, "
                f"body[:200]={body[:200]}"
            )

            try:
                import json
                data = json.loads(body)
            except Exception:
                result["message"] = "转发响应解析失败"
                logger.warning(result["message"])
                return result

            code = str(data.get("code", ""))
            if code == "100000":
                result["success"] = True
                result["message"] = f"✅ 转发成功（PC端）: {content[:30]}..."
            else:
                msg = data.get("msg", "")
                result["message"] = (
                    f"❌ 转发失败（PC端）: {msg}"
                    f"{' (code=' + str(code) + ')' if code else ''}"
                )

            logger.info(result["message"])

        except Exception as e:
            logger.error(f"PC端转发异常: {e}")
            result["message"] = f"❌ PC端转发异常: {e}"
        finally:
            page.close()

        return result
