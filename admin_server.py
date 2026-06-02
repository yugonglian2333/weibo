#!/usr/bin/env python3
"""
微博助手后台系统 — 本地管理服务器
================================
一键启动，浏览器自动打开。
集成了配置管理、Cookie 获取、手动执行、Secrets 同步等全功能。

使用方式:
    py admin_server.py
"""

import io
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

# ---- 修复 Windows 终端编码 ----
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logger = logging.getLogger("admin")

ROOT_DIR = Path(__file__).parent.resolve()
CONFIG_YAML = ROOT_DIR / "config.yaml"
ENV_FILE = ROOT_DIR / ".env"
ADMIN_HTML = ROOT_DIR / "admin.html"
LOG_BUFFER = []  # 内存日志缓冲区
LOG_MAX = 200


# ---- 日志捕获 ----
class BufferHandler(logging.Handler):
    def emit(self, record):
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        msg = f"{ts} [{record.levelname}] {record.getMessage()}"
        LOG_BUFFER.append(msg)
        if len(LOG_BUFFER) > LOG_MAX:
            del LOG_BUFFER[0 : len(LOG_BUFFER) - LOG_MAX]


buffer_handler = BufferHandler()
buffer_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(buffer_handler)
logging.getLogger("weibo_client").addHandler(buffer_handler)
logging.getLogger("ai_provider").addHandler(buffer_handler)
logging.getLogger("notifier").addHandler(buffer_handler)
logging.getLogger("main").addHandler(buffer_handler)


# ============================================================
# YAML / .env 读写
# ============================================================

def read_yaml() -> dict:
    if not CONFIG_YAML.exists():
        return {}
    with open(CONFIG_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml(data: dict):
    with open(CONFIG_YAML, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _save_containerid_to_checkin(name: str, containerid: str):
    """将搜索到的 containerid 自动保存到 config.yaml checkin topics"""
    try:
        config = read_yaml()
        topics = config.setdefault("checkin", {}).setdefault("topics", [])
        updated = False
        for t in topics:
            if isinstance(t, dict) and t.get("name") == name:
                if not t.get("containerid"):
                    t["containerid"] = containerid
                    updated = True
                break
        else:
            topics.append({"name": name, "containerid": containerid})
            updated = True
        if updated:
            write_yaml(config)
            logger.info(f"已将「{name}」的 containerid 保存到 config.yaml")
    except Exception as e:
        logger.warning(f"保存 containerid 失败: {e}")


def read_env() -> dict:
    result = {}
    if not ENV_FILE.exists():
        return result
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                v = v.strip().strip("'").strip('"')
                result[k.strip()] = v
    return result


def write_env(data: dict):
    existing = {}
    comments = []
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    comments.append(line.rstrip("\n"))
                elif "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    existing[k] = line.rstrip("\n")

    lines = list(comments)
    updated = set()
    for full_line in existing.values():
        k = full_line.split("=", 1)[0].strip()
        if k in data:
            lines.append(f"{k}={data[k]}")
            updated.add(k)
        else:
            lines.append(full_line)

    for k, v in data.items():
        if k not in updated:
            lines.append(f"{k}={v}")

    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


# ============================================================
# 导入项目模块
# ============================================================

_modules_loaded = False
_WeiboClient = None
_create_provider_from_env = None
_create_notifier_from_env = None
_build_notification = None


def _ensure_modules():
    """确保项目模块已加载（只执行一次）"""
    global _modules_loaded, _WeiboClient, _create_provider_from_env
    global _create_notifier_from_env, _build_notification

    if _modules_loaded:
        return

    sys.path.insert(0, str(ROOT_DIR))

    # 先加载 .env
    from main import load_dotenv as _load_dotenv
    _load_dotenv()

    from weibo_client import WeiboClient as _WC
    from ai_provider import create_provider_from_env as _cpfe
    from notifier import create_notifier_from_env as _cnfe, build_notification as _bn

    _WeiboClient = _WC
    _create_provider_from_env = _cpfe
    _create_notifier_from_env = _cnfe
    _build_notification = _bn
    _modules_loaded = True

    logger.info("项目模块加载完成")


# ============================================================
# 操作函数
# ============================================================

def run_checkin_topic(name: str, containerid: str = "") -> dict:
    """对单个超话执行签到"""
    cookie = os.environ.get("WEIBO_COOKIE", "") or read_env().get("WEIBO_COOKIE", "")
    if not cookie:
        if not os.environ.get("WEIBO_COOKIE"):
            os.environ["WEIBO_COOKIE"] = cookie
        if not cookie:
            return {"success": False, "message": "未设置 WEIBO_COOKIE", "name": name}

    _ensure_modules()
    client = _WeiboClient(cookie)

    if not containerid:
        containerid = client.get_containerid_by_name(name) or ""
        if containerid:
            _save_containerid_to_checkin(name, containerid)

    if not containerid:
        client.cleanup()
        return {"success": False, "message": f"未找到超话 '{name}'", "name": name}

    result = client.checkin_super_topic(containerid, topic_name=name)
    result["name"] = name
    client.cleanup()
    return result


def run_posting_topics(topics: list[str], style: str = "自然随性",
                       min_w: int = 50, max_w: int = 200) -> list[dict]:
    """执行 AI 发帖"""
    results = []
    _ensure_modules()
    env = read_env()

    cookie = os.environ.get("WEIBO_COOKIE", "") or env.get("WEIBO_COOKIE", "")
    if not cookie:
        return [{"topic": t, "success": False, "message": "未设置 WEIBO_COOKIE"} for t in topics]

    for k, v in env.items():
        if k not in os.environ:
            os.environ[k] = v

    client = _WeiboClient(cookie)
    ai = _create_provider_from_env()

    # 获取 containerid 映射
    config = read_yaml()
    topic_to_cid = {}
    for t in topics:
        for ct in config.get("checkin", {}).get("topics", []):
            if ct.get("name") == t and ct.get("containerid"):
                topic_to_cid[t] = ct["containerid"]
                break
        if t not in topic_to_cid:
            cid = client.get_containerid_by_name(t)
            if cid:
                topic_to_cid[t] = cid
                _save_containerid_to_checkin(t, cid)

    for topic in topics:
        content = ai.generate_post(topics=[topic], style=style,
                                    min_words=min_w, max_words=max_w)
        if not content:
            results.append({"topic": topic, "success": False,
                           "message": "AI 生成内容失败", "content": ""})
            continue

        containerid = topic_to_cid.get(topic)
        result = client.post_weibo(content, containerid=containerid)
        result["topic"] = topic
        results.append(result)
        time.sleep(2)

    client.cleanup()
    return results


def run_full_flow() -> dict:
    """执行完整签到+发帖流程"""
    import time as time_mod

    logger.info("=" * 50)
    logger.info("完整执行流程 启动")
    logger.info("=" * 50)

    env = read_env()
    cookie = os.environ.get("WEIBO_COOKIE", "") or env.get("WEIBO_COOKIE", "")
    if not cookie:
        logger.error("未设置 WEIBO_COOKIE，无法继续")
        return {"ok": False, "message": "未设置 WEIBO_COOKIE"}

    for k, v in env.items():
        if k not in os.environ:
            os.environ[k] = v

    config = read_yaml()
    _ensure_modules()

    logger.info("初始化微博客户端...")
    client = _WeiboClient(cookie)

    if not client.check_session_valid():
        logger.error("Cookie 已失效，请重新获取")
        client.cleanup()
        return {"ok": False, "message": "Cookie 已失效"}

    checkin_results = []
    post_results = []

    try:
        # 签到
        checkin_topics = config.get("checkin", {}).get("topics", [])
        if checkin_topics:
            logger.info(f"===== 超话签到开始（共 {len(checkin_topics)} 个）=====")
            for topic in checkin_topics:
                name = topic.get("name", "未知")
                cid = topic.get("containerid", "")
                if not cid:
                    logger.info(f"搜索超话 '{name}'...")
                    cid = client.get_containerid_by_name(name) or ""
                    if cid:
                        _save_containerid_to_checkin(name, cid)
                if cid:
                    r = client.checkin_super_topic(cid, topic_name=name)
                else:
                    r = {"success": False, "message": f"未找到超话 '{name}'", "name": name}
                r["name"] = name
                checkin_results.append(r)
                logger.info(f"签到: {name} -> {r.get('message','?')}")
                time_mod.sleep(2)
            ok = sum(1 for r in checkin_results if r["success"])
            logger.info(f"签到完成: {ok}/{len(checkin_topics)} 成功")
        else:
            logger.info("没有配置签到列表，跳过")

        # AI 发帖
        posting_cfg = config.get("posting", {})
        if posting_cfg.get("enabled", True):
            topics = posting_cfg.get("topics", [])
            style = posting_cfg.get("style", "自然随性")
            min_w = posting_cfg.get("min_words", 50)
            max_w = posting_cfg.get("max_words", 200)

            if topics:
                logger.info(f"===== AI 发帖开始（共 {len(topics)} 个话题）=====")
                ai = _create_provider_from_env()

                for topic in topics:
                    logger.info(f"为话题「{topic}」生成内容...")
                    content = ai.generate_post(topics=[topic], style=style,
                                                min_words=min_w, max_words=max_w)
                    if not content:
                        logger.warning(f"「{topic}」AI 生成内容失败")
                        post_results.append({"topic": topic, "success": False,
                                            "message": "AI 生成内容失败"})
                        continue

                    logger.info(f"AI 生成 [{topic}]: {content[:80]}...")
                    cid = ""
                    for ct_item in checkin_topics:
                        if ct_item.get("name") == topic and ct_item.get("containerid"):
                            cid = ct_item["containerid"]
                            break
                    if not cid:
                        cid = client.get_containerid_by_name(topic) or ""
                        if cid:
                            _save_containerid_to_checkin(topic, cid)

                    r = client.post_weibo(content, containerid=cid)
                    r["topic"] = topic
                    post_results.append(r)
                    logger.info(f"发帖 [{topic}]: {r.get('message','?')}")
                    time_mod.sleep(3)

                ok = sum(1 for r in post_results if r.get("success"))
                logger.info(f"发帖完成: {ok}/{len(topics)} 成功")
            else:
                logger.info("没有配置发帖话题，跳过")
        else:
            logger.info("发帖已禁用，跳过")

        # 通知
        if config.get("notification", {}).get("enabled", True):
            try:
                notifier = _create_notifier_from_env()
                if notifier:
                    title, content = _build_notification(checkin_results, post_results)
                    if notifier.send(title, content):
                        logger.info("通知已发送")
                    else:
                        logger.warning("通知发送失败")
            except Exception as e:
                logger.warning(f"通知异常: {e}")

        logger.info("=" * 50)
        logger.info("完整执行流程 结束")
        logger.info("=" * 50)

        return {"ok": True, "message": "执行完成",
                "checkin": len(checkin_results),
                "posted": len(post_results)}

    except Exception as e:
        logger.error(f"执行异常: {e}")
        return {"ok": False, "message": str(e)}
    finally:
        client.cleanup()


def search_containerid(name: str) -> dict:
    """搜索超话 containerid（复用 weibo_client 的统一实现）"""
    env = read_env()
    cookie = env.get("WEIBO_COOKIE", "")
    if not cookie:
        return {"ok": False, "message": "未设置 WEIBO_COOKIE"}

    logger.info(f"搜索 containerid: {name}")
    _ensure_modules()
    client = _WeiboClient(cookie)
    cid = client.get_containerid_by_name(name)
    client.cleanup()

    if cid:
        _save_containerid_to_checkin(name, cid)
        return {"ok": True, "name": name, "containerid": cid}
    else:
        return {"ok": True, "name": name, "containerid": "", "message": f"未找到超话 '{name}'"}


def sync_to_github(dry_run: bool = True) -> dict:
    """同步 .env 到 GitHub Secrets"""
    import subprocess as sp
    result = sp.run(
        [sys.executable, str(ROOT_DIR / "sync_secrets.py")] +
        (["--dry-run"] if dry_run else []),
        capture_output=True, text=True, encoding="utf-8", timeout=30, cwd=str(ROOT_DIR)
    )
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    return {
        "ok": result.returncode == 0,
        "message": stdout or stderr or "同步完成",
        "dry_run": dry_run,
    }


# ============================================================
# Cookie 提取（Playwright）
# ============================================================

cookie_fetch_status = {"running": False, "result": None}


def fetch_cookie_via_playwright():
    """启动 Playwright 浏览器让用户手动登录，然后提取 Cookie"""
    global cookie_fetch_status
    cookie_fetch_status = {"running": True, "result": None}

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = context.new_page()

            page.goto("https://m.weibo.cn/login", wait_until="domcontentloaded", timeout=30000)
            logger.info("已打开微博登录页，请在浏览器中完成登录")

            # 等待用户登录，最长 5 分钟
            for i in range(300):
                time.sleep(1)
                url = page.url
                cookies = context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}

                has_sub = "SUB" in cookie_dict and cookie_dict.get("SUB", "") != ""
                has_login = any(k in cookie_dict for k in ("M_WEIBOCN_PARAMS", "MLOGIN", "PC_TOKEN"))
                is_logged_in_page = "login" not in url.split("?")[0]

                if has_sub and (has_login or is_logged_in_page):
                    logger.info(f"检测到登录成功 (耗时 {i+1}s)")
                    break
            else:
                browser.close()
                cookie_fetch_status = {
                    "running": False,
                    "result": {"ok": False, "message": "登录超时（5分钟），请重试"}
                }
                return

            page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)

            all_cookies = context.cookies()
            cookie_parts = [f"{c['name']}={c['value']}" for c in all_cookies]
            cookie_str = "; ".join(cookie_parts)
            browser.close()

            if cookie_str:
                important = [k for k in ["SUB", "PC_TOKEN", "XSRF-TOKEN", "M_WEIBOCN_PARAMS"]
                            if k in {c["name"] for c in all_cookies}]
                cookie_fetch_status = {
                    "running": False,
                    "result": {
                        "ok": True,
                        "message": "Cookie 获取成功！",
                        "cookie": cookie_str,
                        "cookie_length": len(cookie_str),
                        "key_fields": important,
                    }
                }
            else:
                cookie_fetch_status = {
                    "running": False,
                    "result": {"ok": False, "message": "未能提取到有效 Cookie"}
                }

    except ImportError:
        cookie_fetch_status = {
            "running": False,
            "result": {"ok": False, "message": "Playwright 未安装，请运行: pip install playwright && playwright install chromium"}
        }
    except Exception as e:
        cookie_fetch_status = {
            "running": False,
            "result": {"ok": False, "message": f"Cookie 获取异常: {e}"}
        }


# ============================================================
# HTTP 处理器
# ============================================================

class AdminHandler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_GET(self):
        path = urlparse(self.path).path

        routes = {
            "/api/config": self._get_config,
            "/api/env": self._get_env,
            "/api/logs": self._get_logs,
            "/api/cookie/status": self._get_cookie_status,
        }

        if path in routes:
            routes[path]()
        elif path == "/" or path == "":
            self._redirect("/admin.html")
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

        routes = {
            "/api/config":          lambda: self._save_config(body),
            "/api/env":             lambda: self._save_env(body),
            "/api/ping":            lambda: self._ping(),
            "/api/checkin/run":     lambda: self._checkin(body),
            "/api/posting/run":     lambda: self._posting(body),
            "/api/run-all":         lambda: self._run_all(),
            "/api/containerid":     lambda: self._search_cid(body),
            "/api/cookie/fetch":    lambda: self._fetch_cookie(),
            "/api/cookie/save":     lambda: self._save_cookie(body),
            "/api/secrets/sync":    lambda: self._sync_secrets(body),
        }

        if path in routes:
            routes[path]()
        else:
            self._send_json({"ok": False, "error": f"未知路径: {path}"}, 404)

    def do_OPTIONS(self):
        self._cors_headers(204)

    # ---- 静态文件 ----
    def _redirect(self, url: str):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self, status: int = 200):
        if status != 200:
            self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if status != 200:
            self.end_headers()

    # ---- API: 配置 ----
    def _get_config(self):
        try:
            self._send_json({"ok": True, "data": read_yaml()})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _save_config(self, data: dict):
        try:
            write_yaml(data)
            logger.info("config.yaml 已保存")
            self._send_json({"ok": True, "message": "config.yaml 已保存"})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _get_env(self):
        try:
            self._send_json({"ok": True, "data": read_env()})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _save_env(self, data: dict):
        try:
            write_env(data)
            logger.info(".env 已保存")
            self._send_json({"ok": True, "message": ".env 已保存"})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    # ---- API: 日志 ----
    def _get_logs(self):
        limit = int(urlparse(self.path).query.split("=")[-1]) if "limit" in self.path else 100
        logs = LOG_BUFFER[-limit:] if len(LOG_BUFFER) > limit else list(LOG_BUFFER)
        self._send_json({"ok": True, "logs": logs, "total": len(LOG_BUFFER)})

    # ---- API: 签到 ----
    def _checkin(self, data: dict):
        name = data.get("name", "").strip()
        cid = data.get("containerid", "").strip()
        if not name:
            return self._send_json({"ok": False, "message": "请提供超话名称"}, 400)
        logger.info(f"手动签到: {name}")
        result = run_checkin_topic(name, cid)
        self._send_json({"ok": result["success"], "data": result})

    # ---- API: 发帖 ----
    def _posting(self, data: dict):
        topics = data.get("topics", [])
        style = data.get("style", "自然随性")
        min_w = data.get("min_words", 50)
        max_w = data.get("max_words", 200)
        if not topics:
            return self._send_json({"ok": False, "message": "请提供发帖话题"}, 400)
        logger.info(f"手动发帖: {topics}, 风格={style}")
        results = run_posting_topics(topics, style, min_w, max_w)
        ok = all(r.get("success") for r in results)
        self._send_json({"ok": ok, "data": results})

    # ---- API: 完整执行 ----
    def _run_all(self):
        logger.info("手动触发完整执行流程")
        threading.Thread(target=run_full_flow, daemon=True).start()
        self._send_json({"ok": True, "message": "已在后台启动，请查看日志"})

    # ---- API: 容器ID查询 ----
    def _search_cid(self, data: dict):
        name = data.get("name", "").strip()
        if not name:
            return self._send_json({"ok": False, "message": "请提供超话名称"}, 400)
        logger.info(f"搜索 containerid: {name}")
        result = search_containerid(name)
        self._send_json(result)

    # ---- API: Cookie 获取 ----
    def _fetch_cookie(self):
        global cookie_fetch_status
        if cookie_fetch_status.get("running"):
            return self._send_json({"ok": False, "message": "Cookie 获取已在运行中"}, 409)
        logger.info("启动 Cookie 获取流程...")
        threading.Thread(target=fetch_cookie_via_playwright, daemon=True).start()
        self._send_json({"ok": True, "message": "浏览器已打开，请在浏览器中登录微博"})

    def _get_cookie_status(self):
        self._send_json(cookie_fetch_status)

    def _save_cookie(self, data: dict):
        cookie = data.get("cookie", "").strip()
        key_name = data.get("key", "WEIBO_COOKIE").strip().upper() or "WEIBO_COOKIE"
        if not cookie:
            return self._send_json({"ok": False, "message": "Cookie 为空"}, 400)
        try:
            env = read_env()
            env[key_name] = cookie
            write_env(env)
            os.environ[key_name] = cookie
            logger.info(f"Cookie 已保存到 .env，key={key_name}")
            self._send_json({"ok": True, "message": f"Cookie 已保存到 .env (key={key_name})", "key": key_name})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    # ---- API: Secrets 同步 ----
    def _sync_secrets(self, data: dict):
        dry_run = data.get("dry_run", False)
        logger.info(f"同步 GitHub Secrets (dry_run={dry_run})...")
        result = sync_to_github(dry_run=dry_run)
        self._send_json(result)

    # ---- API: Ping ----
    def _ping(self):
        self._send_json({
            "ok": True,
            "message": "微博助手后台系统运行中",
            "version": "2.0",
        })

    def log_message(self, format, *args):
        logger.info(f"{self.address_string()} - {format % args}")


# ============================================================
# 启动
# ============================================================

def main():
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s", datefmt="%H:%M:%S"
    ))
    root.addHandler(console)

    port = 8080
    host = "127.0.0.1"

    if not ADMIN_HTML.exists():
        logger.error(f"找不到 admin.html")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("微博助手后台系统 v2.0 启动中...")
    logger.info("=" * 50)
    logger.info(f"项目目录: {ROOT_DIR}")
    logger.info(f"config.yaml: {'✅' if CONFIG_YAML.exists() else '❌ 不存在'}")
    logger.info(f".env:        {'✅' if ENV_FILE.exists() else '❌ 不存在（首次保存自动创建）'}")

    try:
        server = HTTPServer((host, port), AdminHandler)
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            logger.error(f"端口 {port} 已被占用，请先关闭占用该端口的程序")
        else:
            logger.error(f"启动失败: {e}")
        sys.exit(1)

    logger.info("")
    logger.info(f"✅ 后台系统已启动")
    logger.info(f"📋 浏览器即将自动打开...")
    logger.info(f"🛑 按 Ctrl+C 停止")
    logger.info("")

    threading.Timer(0.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("")
        logger.info("后台系统已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
