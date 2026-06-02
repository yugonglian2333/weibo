#!/usr/bin/env python3
"""
将 .env 文件同步到 GitHub Actions Secrets

用法:
  python sync_secrets.py                  # 同步到仓库级 Secrets
  python sync_secrets.py --dry-run        # 仅预览，不实际执行
  python sync_secrets.py --env dev        # 同步到指定环境

依赖: 需要安装 GitHub CLI (gh) 并已登录
  https://cli.github.com/
"""

import subprocess
import sys
import os
import shutil
import argparse
import io

# 修复 Windows 终端 GBK 编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )


# .env 中的 key → GitHub Secret 名称 的映射
# 如果 .env key 和 Secret 名相同，可以不写，脚本会自动使用大写形式
KEY_MAP = {
    "WEIBO_COOKIE": "WEIBO_COOKIE",
    "AI_PROVIDER": "AI_PROVIDER",
    "AI_API_KEY": "AI_API_KEY",
    "AI_API_BASE": "AI_API_BASE",
    "AI_MODEL": "AI_MODEL",
    "NOTIFY_PROVIDER": "NOTIFY_PROVIDER",
    "NOTIFY_TOKEN": "NOTIFY_TOKEN",
    "NOTIFY_CHAT_ID": "NOTIFY_CHAT_ID",
}


def parse_dotenv(env_path: str) -> dict[str, str]:
    """解析 .env 文件，返回 {key: value} 字典"""
    result = {}
    if not os.path.exists(env_path):
        print(f"❌ 找不到 .env 文件: {env_path}")
        sys.exit(1)

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释
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
                if key:
                    result[key] = value
    return result


def find_gh() -> str | None:
    """查找 gh.exe 的完整路径（PATH 可能不传递到子进程）"""
    # 1. 用 shutil.which 在 PATH 中查找
    path = shutil.which("gh")
    if path:
        return path

    # 2. 尝试常见安装路径
    candidates = []
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%LocalAppData%\Programs\GitHub CLI\bin\gh.exe"),
            os.path.expandvars(r"%ProgramFiles%\GitHub CLI\bin\gh.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\GitHub CLI\bin\gh.exe"),
            r"C:\Program Files\GitHub CLI\bin\gh.exe",
        ]
    else:
        candidates = [
            "/usr/local/bin/gh",
            "/usr/bin/gh",
            "/opt/homebrew/bin/gh",
        ]

    for p in candidates:
        if os.path.isfile(p):
            return p

    return None


def check_gh_installed() -> bool:
    """检查 GitHub CLI 是否已安装"""
    return find_gh() is not None


_GH_PATH = None


def gh_path() -> str:
    """获取 gh 可执行文件路径，找不到则报错退出"""
    global _GH_PATH
    if _GH_PATH:
        return _GH_PATH
    _GH_PATH = find_gh()
    if not _GH_PATH:
        print("❌ 未检测到 GitHub CLI (gh)，请先安装并登录：")
        print("   1. 下载安装: https://cli.github.com/")
        print("   2. 登录认证: gh auth login")
        sys.exit(1)
    return _GH_PATH


def get_github_repo() -> str:
    """获取当前仓库的 owner/repo 格式"""
    result = subprocess.run(
        [gh_path(), "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        print("❌ 无法获取 GitHub 仓库信息，请确认在仓库目录中执行，且已登录 gh")
        sys.exit(1)
    return result.stdout.strip()


def set_secret(repo: str, name: str, value: str, environment: str = None):
    """通过 gh CLI 设置 GitHub Secret"""
    if environment:
        cmd = [gh_path(), "secret", "set", name, "--repo", repo, "--env", environment, "--body", value]
    else:
        cmd = [gh_path(), "secret", "set", name, "--repo", repo, "--body", value]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="同步 .env 到 GitHub Actions Secrets")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅预览将要同步的内容，不实际执行"
    )
    parser.add_argument(
        "--env", type=str, default=None,
        help="同步到指定 GitHub Environment（默认同步到仓库级 Secrets）"
    )
    args = parser.parse_args()

    # 解析 .env
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")
    env_vars = parse_dotenv(env_path)

    # 筛选出需要同步的 key
    secrets_to_sync = {}
    for env_key, env_value in env_vars.items():
        if env_key in KEY_MAP:
            secrets_to_sync[KEY_MAP[env_key]] = env_value

    if not secrets_to_sync:
        print("❌ .env 中没有找到需要同步的配置项")
        sys.exit(0)

    # 获取仓库信息
    repo = get_github_repo()
    print(f"📦 目标仓库: {repo}")
    if args.env:
        print(f"🌐 目标环境: {args.env}")
    print(f"📋 待同步 Secrets: {len(secrets_to_sync)} 个\n")

    # 预览或执行
    success_count = 0
    fail_count = 0

    for name, value in secrets_to_sync.items():
        # 隐藏敏感值，只显示前后各 6 个字符
        if len(value) > 20:
            preview = f"{value[:6]}...{value[-6:]}"
        else:
            preview = "***"

        if args.dry_run:
            print(f"  🔍 [预览] {name} = {preview}")
            success_count += 1
        else:
            print(f"  ⏳ 正在同步 {name}...", end=" ")
            if set_secret(repo, name, value, args.env):
                print(f"✅ ({preview})")
                success_count += 1
            else:
                print(f"❌ 失败")
                fail_count += 1

    print(f"\n{'🔍 预览' if args.dry_run else '✅ 同步'}完成: "
          f"{success_count} 成功, {fail_count} 失败")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
