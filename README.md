# 微博超话自动签到 + AI 发帖

每天自动签到指定超话，并用 AI 生成内容发布微博。部署在 GitHub Actions，免费免服务器。

## 功能

- ✅ **超话签到** — 支持多个超话，自动搜索 containerid
- 🤖 **AI 发帖** — 可插拔设计，支持 Mimo / Claude / OpenAI
- 📬 **通知推送** — 微信/Telegram 推送每日执行报告
- 🎛️ **可视化管理** — 本地后台系统，一站式配置

## 快速开始（推荐）

```bash
# 1. 安装依赖
py -m pip install -r requirements.txt

# 2. 启动后台管理系统（二选一）
py admin_server.py          # 命令行启动
# 或双击 start_admin.bat    # 一键启动
```

浏览器会自动打开配置页面，填写以下必填项：

| 配置项 | 说明 |
|--------|------|
| 超话列表 | 要签到的超话名称 |
| 微博 Cookie | 浏览器登录 m.weibo.cn 后复制 |
| AI API Key | AI 服务商的 API Key |

其他配置（发帖风格、通知渠道等）按需调整。**修改自动保存到本地文件**。

## 部署到 GitHub Actions

1. 将项目推送到 GitHub 仓库
2. 在 Settings → Secrets → Actions 中添加 Secrets（参考后台系统导出的 GitHub Secrets 列表）
3. GitHub Actions 会每天自动执行

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `WEIBO_COOKIE` | ✅ | 微博 Cookie |
| `AI_PROVIDER` | ❌ | AI 服务商，默认 `mimo` |
| `AI_API_KEY` | ✅ | AI API Key |
| `AI_API_BASE` | ❌ | API 地址 |
| `AI_MODEL` | ❌ | 模型名称 |
| `NOTIFY_PROVIDER` | ❌ | 通知渠道：pushplus / serverchan / telegram |
| `NOTIFY_TOKEN` | ❌ | 通知 Token |
| `NOTIFY_CHAT_ID` | ❌ | Telegram chat_id |

## 手动运行

```bash
py main.py
```

## 项目结构

```
├── admin.html              # 后台系统前端
├── admin_server.py         # 后台系统服务器（一键启动）
├── main.py                 # 主入口
├── weibo_client.py         # 微博 API（签到 + 发帖）
├── ai_provider.py          # AI 接口（可插拔）
├── notifier.py             # 通知推送（可插拔）
├── config.yaml             # 超话列表等配置
├── .env                    # 敏感信息（本地）
└── .github/workflows/      # GitHub Actions 定时任务
```

## 常见问题

**Q: Cookie 多久过期？** 通常几个月，失效后重新获取即可。

**Q: 签到失败？** 检查超话名称是否正确，或在后台系统中手动填入 containerid。

**Q: GitHub Actions 额度？** 公开仓库无限免费，每天运行不到 1 分钟。
