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
        except requests.RequestException as e:
            logger.error(f"请求失败 [{method} {url}]: {e}")
            return None
        except ValueError as e:
            # 尝试获取响应文本的前 500 字符用于调试
            try:
                raw_preview = resp.text[:500] if hasattr(resp, 'text') else 'N/A'
            except Exception:
                raw_preview = 'N/A'
            logger.error(
                f"JSON 解析失败 [{method} {url}]: {e}"
                f" | status={resp.status_code}"
                f" | 响应预览: {raw_preview}"
            )
            return None

    def check_session_valid(self) -> bool:
        """
        检测当前 Cookie 是否仍然有效

        Returns:
            True 表示登录态有效
        """
        # 方式一：m.weibo.cn 移动端 API
        url = f"{self.API_BASE}/config"
        data = self._request("GET", url)
        logger.info(f"/api/config 响应: {data}")

        if data and data.get("data", {}).get("login"):
            uid = data.get("data", {}).get("uid", "未知")
            logger.info(f"Cookie 有效（移动端），当前登录用户 UID: {uid}")
            return True

        # 方式二：weibo.com PC 端 API（兼容从 PC 端获取的 Cookie）
        logger.info("移动端验证未通过，尝试 PC 端 API...")
        pc_url = "https://weibo.com/ajax/config"
        pc_data = self._request("GET", pc_url)
        logger.info(f"weibo.com/ajax/config 响应: {pc_data}")

        if pc_data and pc_data.get("data", {}).get("login"):
            uid = pc_data.get("data", {}).get("uid", "未知")
            logger.info(f"Cookie 有效（PC 端），当前登录用户 UID: {uid}")
            return True

        # 方式三：直接访问首页，看是否能获取到用户信息
        logger.info("PC 端 API 也未通过，尝试获取首页...")
        home_url = "https://m.weibo.cn/home"
        home_resp = self.session.get(
            home_url,
            timeout=30,
            allow_redirects=False,
        )
        logger.info(f"/home 状态码: {home_resp.status_code}")
        # 如果返回 200 且没有跳转到登录页，说明已登录
        if home_resp.status_code == 200:
            # 尝试从页面中提取 uid
            import re
            uid_match = re.search(
                r'"uid":\s*"?(\d+)"?', home_resp.text
            )
            if uid_match:
                uid = uid_match.group(1)
                logger.info(
                    f"Cookie 有效（首页验证），当前登录用户 UID: {uid}"
                )
                return True
            # 如果没有跳转到登录页，就认为有效
            if "passport" not in home_resp.url:
                logger.info("Cookie 似乎有效（首页可访问，无登录跳转）")
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
            if card.get("card_type") == 27:  # 超话类型
                # card_group 中查找超话信息
                card_group = card.get("card_group", [])
                for group in card_group:
                    title_sub = group.get("title_sub", "")
                    if name in title_sub or name in group.get("title", ""):
                        # 从 scheme 中提取 containerid
                        # 格式: sinaweibo://supergroup?containerid=100808xxx
                        scheme = group.get("scheme", "")
                        match = re.search(
                            r"containerid=(\d+)", scheme
                        )
                        if match:
                            containerid = match.group(1)
                            logger.info(
                                f"超话 '{name}' -> containerid: {containerid}"
                            )
                            return containerid

        logger.warning(f"未找到超话 '{name}'，请确认名称是否正确")
        return None

    def checkin_super_topic(self, containerid: str) -> dict:
        """
        在指定超话下签到

        Args:
            containerid: 超话的 containerid（通常以 100808 开头）

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

        # 超话签到 API（使用 weibo.com 的 ajax 接口）
        # 首先获取超话首页，获取必要的上下文
        topic_url = (
            f"{self.API_BASE}/container/getIndex"
            f"?containerid={containerid}"
        )
        topic_data = self._request("GET", topic_url)
        if topic_data is None or topic_data.get("ok") != 1:
            result["message"] = f"无法访问超话页面 (containerid: {containerid})"
            logger.error(result["message"])
            return result

        # 获取超话名称
        topic_name = "未知超话"
        try:
            cards = topic_data.get("data", {}).get("cards", [])
            if cards:
                title = (
                    cards[0]
                    .get("card_group", [{}])[0]
                    .get("title_sub", "未知超话")
                )
                topic_name = title or "未知超话"
        except Exception:
            pass

        # 执行签到
        # 微博超话签到通用接口
        checkin_url = (
            "https://weibo.com/p/aj/general/button"
            "?ajwvr=6"
            "&api=http://i.huati.weibo.com/aj/super/checkin"
            f"&id={containerid}"
            "&location=page_100808_super_index"
        )

        # 需要设置 Referer 为超话页面
        headers = {
            "Referer": (
                f"https://weibo.com/p/{containerid}/super_index"
            ),
        }

        data = self._request("GET", checkin_url, headers=headers)

        if data is not None:
            # 解析签到结果
            code = data.get("code", "")
            msg = data.get("msg", "")

            if code == "100000":
                # 签到成功
                result["success"] = True
                result["message"] = f"✅ {topic_name} 签到成功: {msg}"
                logger.info(result["message"])
            elif "已签到" in msg or "already" in msg.lower():
                # 今天已经签到过了
                result["success"] = True
                result["message"] = f"⏭ {topic_name} 今日已签到: {msg}"
                logger.info(result["message"])
            else:
                result["message"] = f"❌ {topic_name} 签到失败: {msg} (code={code})"
                logger.warning(result["message"])
        else:
            # 主接口请求失败（非 JSON 响应或网络错误），记录并尝试备选方案
            logger.warning(f"主签到接口请求失败 ({topic_name})，尝试移动端备选方案...")

        # 也尝试移动端 API 作为备选
        if not result["success"]:
            result = self._checkin_mobile_api(containerid, topic_name)
            if result["success"]:
                return result

        return result

    def _checkin_mobile_api(
        self, containerid: str, topic_name: str
    ) -> dict:
        """备用签到方案：使用移动端接口"""
        result = {
            "success": False,
            "message": "",
            "containerid": containerid,
        }

        # 移动端签到 - 通过签到按钮接口
        checkin_containerid = f"{containerid}_-_checkin"
        url = f"{self.API_BASE}/container/getIndex"
        params = {"containerid": checkin_containerid}

        data = self._request("GET", url, params=params)
        if data is None or data.get("ok") != 1:
            result["message"] = f"❌ {topic_name} 移动端签到也失败"
            return result

        msg = data.get("data", {}).get("msg", "")
        if "已签到" in msg or "already" in msg.lower():
            result["success"] = True
            result["message"] = f"⏭ {topic_name} 今日已签到（移动端）"
        elif msg:
            result["success"] = True
            result["message"] = f"✅ {topic_name} 签到成功（移动端）: {msg}"
        else:
            result["message"] = f"❌ {topic_name} 签到失败: 未知错误"

        logger.info(result["message"])
        return result

    def post_weibo(self, content: str) -> dict:
        """
        发布微博

        Args:
            content: 微博正文内容

        Returns:
            {
                "success": bool,
                "message": str,
                "weibo_id": str,  # 发布成功时返回微博 ID
            }
        """
        result = {
            "success": False,
            "message": "",
            "weibo_id": "",
        }

        if not self._csrf_token:
            result["message"] = "缺少 CSRF token，无法发帖"
            logger.error(result["message"])
            return result

        if not content.strip():
            result["message"] = "内容为空，不执行发帖"
            logger.warning(result["message"])
            return result

        # 字数限制检查（微博 140 字限制，会员更长）
        # 中文一个字算一个字符
        if len(content) > 2000:
            content = content[:1990] + "..."
            logger.warning("内容过长，已截断")

        url = f"{self.API_BASE}/statuses/update"
        headers = {
            "X-XSRF-TOKEN": self._csrf_token,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data_payload = {
            "content": content,
            "st": self._csrf_token,
        }

        data = self._request(
            "POST", url, headers=headers, data=data_payload
        )

        if data is None:
            result["message"] = "发帖请求失败"
            return result

        if data.get("ok") == 1:
            weibo_id = data.get("data", {}).get("id", "")
            result["success"] = True
            result["weibo_id"] = str(weibo_id)
            result["message"] = f"✅ 发帖成功，微博 ID: {weibo_id}"
            logger.info(result["message"])
        else:
            errno = data.get("errno", "")
            errmsg = data.get("msg", data.get("errmsg", "未知错误"))
            result["message"] = f"❌ 发帖失败: {errmsg} (errno={errno})"
            logger.warning(result["message"])

        return result
