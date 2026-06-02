#!/usr/bin/env python3
"""
微博助手后台系统 — 本地管理服务器
================================
启动后在浏览器中打开 http://localhost:8080 即可进行可视化配置。
修改会自动同步到本地的 config.yaml 和 .env 文件。

使用方式:
    python admin_server.py
    # 然后浏览器打开 http://localhost:8080
"""

import json
import logging
import os
import sys
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

logger = logging.getLogger(__name__)

# 项目根目录
ROOT_DIR = Path(__file__).parent.resolve()

# 文件路径
CONFIG_YAML = ROOT_DIR / "config.yaml"
ENV_FILE = ROOT_DIR / ".env"
ADMIN_HTML = ROOT_DIR / "admin.html"


# ============================================================
# YAML / .env 读写
# ============================================================

def read_config_yaml() -> dict:
    """读取 config.yaml，返回字典"""
    if not CONFIG_YAML.exists():
        return {}
    with open(CONFIG_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_config_yaml(data: dict):
    """写入 config.yaml"""
    with open(CONFIG_YAML, "w", encoding="utf-8") as f:
        yaml.dump(
            data,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def read_env_file() -> dict:
    """读取 .env 文件，返回键值对字典"""
    result = {}
    if not ENV_FILE.exists():
        return result
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # 移除引号
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                result[key] = value
    return result


def write_env_file(data: dict):
    """写入 .env 文件（保留原有注释结构，仅更新键值）"""
    # 读取已有注释和结构
    existing_lines = []
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()

    updated_keys = set()

    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line.rstrip("\n"))
        elif "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in data:
                new_lines.append(f"{key}={data[key]}")
                updated_keys.add(key)
            else:
                new_lines.append(line.rstrip("\n"))
        else:
            new_lines.append(line.rstrip("\n"))

    # 追加新增的键
    for key, value in data.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(new_lines) + "\n")


# ============================================================
# HTTP 请求处理器
# ============================================================

class AdminHandler(SimpleHTTPRequestHandler):
    """处理 API 请求 + 静态文件服务"""

    def __init__(self, *args, **kwargs):
        # 静态文件从项目根目录提供
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    # ---- API 路由 ----

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            self._handle_get_config()
        elif path == "/api/env":
            self._handle_get_env()
        elif path == "/" or path == "":
            # 默认重定向到 admin.html
            self.send_response(302)
            self.send_header("Location", "/admin.html")
            self.end_headers()
        else:
            # 静态文件
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else ""

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求体不是有效的 JSON"}, 400)
            return

        if path == "/api/config":
            self._handle_save_config(data)
        elif path == "/api/env":
            self._handle_save_env(data)
        elif path == "/api/ping":
            self._send_json({
                "ok": True,
                "message": "微博助手后台系统运行中",
                "files": {
                    "config_yaml": str(CONFIG_YAML),
                    "env": str(ENV_FILE),
                }
            })
        else:
            self._send_json({"ok": False, "error": f"未知的 API 路径: {path}"}, 404)

    # ---- API 实现 ----

    def _handle_get_config(self):
        """GET /api/config — 读取 config.yaml"""
        try:
            config = read_config_yaml()
            self._send_json({"ok": True, "data": config})
        except Exception as e:
            logger.error(f"读取 config.yaml 失败: {e}")
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _handle_save_config(self, data: dict):
        """POST /api/config — 保存 config.yaml"""
        try:
            write_config_yaml(data)
            logger.info("config.yaml 已保存")
            self._send_json({"ok": True, "message": "config.yaml 已保存"})
        except Exception as e:
            logger.error(f"保存 config.yaml 失败: {e}")
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _handle_get_env(self):
        """GET /api/env — 读取 .env"""
        try:
            env_data = read_env_file()
            self._send_json({"ok": True, "data": env_data})
        except Exception as e:
            logger.error(f"读取 .env 失败: {e}")
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _handle_save_env(self, data: dict):
        """POST /api/env — 保存 .env"""
        try:
            write_env_file(data)
            logger.info(".env 已保存")
            self._send_json({"ok": True, "message": ".env 已保存"})
        except Exception as e:
            logger.error(f"保存 .env 失败: {e}")
            self._send_json({"ok": False, "error": str(e)}, 500)

    # ---- 工具方法 ----

    def _send_json(self, data: dict, status: int = 200):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """重写日志，使用更清晰的格式"""
        logger.info(f"{self.address_string()} - {format % args}")


# ============================================================
# 启动服务器
# ============================================================

def main():
    """启动管理后台服务器"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%H:%M:%S",
    )

    port = 8080
    host = "127.0.0.1"

    # 检查必需文件
    if not ADMIN_HTML.exists():
        logger.error(f"找不到 admin.html，请确保文件在项目根目录")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("微博助手后台系统 启动中...")
    logger.info("=" * 50)
    logger.info(f"项目目录: {ROOT_DIR}")
    logger.info(f"config.yaml: {CONFIG_YAML}")
    logger.info(f".env: {ENV_FILE}")

    # 提示 .env 如果不存在
    if not ENV_FILE.exists():
        logger.warning(".env 文件不存在，首次保存时会自动创建")

    try:
        server = HTTPServer((host, port), AdminHandler)
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            logger.error(f"端口 {port} 已被占用，请先关闭占用该端口的程序")
            logger.error(f"或者修改脚本中的 port 变量为其他端口")
        else:
            logger.error(f"启动服务器失败: {e}")
        sys.exit(1)

    logger.info("")
    logger.info(f"✅ 后台系统已启动！")
    logger.info(f"📋 浏览器即将自动打开: http://{host}:{port}")
    logger.info(f"🛑 按 Ctrl+C 停止服务器")
    logger.info("")

    # 延迟 0.5 秒后用默认浏览器自动打开
    def _open_browser():
        webbrowser.open(f"http://{host}:{port}")

    threading.Timer(0.5, _open_browser).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("")
        logger.info("后台系统已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
