# 微博超话自动签到 + AI 发帖

每天自动在指定的微博超话下签到，并使用 AI 生成帖子内容发布到微博。

部署在 **GitHub Actions** 上，免费、无需服务器、每天定时自动运行。

## 功能

- ✅ **超话签到**：支持多个超话，自动搜索 containerid 或手动指定
- 🤖 **AI 发帖**：可插拔设计，支持小米 Mimo / Claude / OpenAI 及其他兼容接口
- ⏰ **定时执行**：GitHub Actions Cron 每天自动触发
- 📋 **日志记录**：清晰的执行日志，签到/发帖结果一目了然
- 🔒 **安全**：Cookie 和 API Key 存储在 GitHub Secrets，不会泄露

## 快速开始

### 1. Fork 或创建仓库

把这个项目上传到 GitHub 仓库。

### 2. 获取微博 Cookie

1. 在浏览器中登录 [微博](https://m.weibo.cn/)
2. 按 `F12` 打开开发者工具 → Application → Cookies → `https://m.weibo.cn`
3. 复制所有 Cookie，拼接成字符串，格式如：
   ```
   SUB=xxx; SUBP=xxx; XSRF-TOKEN=xxx; ...
   ```

> **提示**：Cookie 通常有效期很长（几个月），失效后重新获取即可。

### 3. 配置 GitHub Secrets

在仓库的 **Settings → Secrets and variables → Actions** 中添加：

| Secret 名称 | 必填 | 说明 |
|-------------|------|------|
| `WEIBO_COOKIE` | ✅ | 微博登录 Cookie 字符串 |
| `AI_API_KEY` | ✅ | AI 服务的 API Key |
| `AI_PROVIDER` | ❌ | AI 服务商，默认 `mimo` |
| `AI_API_BASE` | ❌ | AI API 地址（Mimo 等自建服务需填） |
| `AI_MODEL` | ❌ | 模型名称 |

### 4. 修改超话列表

编辑仓库中的 `config.yaml`：

```yaml
checkin:
  topics:
    - name: "你的超话名称1"
    - name: "你的超话名称2"
      containerid: "100808xxxxx"  # 可选，不填自动搜索

posting:
  enabled: true
  topics:
    - "你的超话名称1"
  style: "自然随性"   # 自然随性 / 专业严谨 / 幽默风趣 / 文艺清新
```

### 5. 修改执行时间（可选）

编辑 `.github/workflows/daily.yml` 中的 cron 表达式：

```yaml
schedule:
  - cron: '0 0 * * *'  # UTC 0:00 = 北京时间 8:00
```

> **注意**：GitHub Actions 的 cron 使用 **UTC 时间**，北京时间 = UTC + 8 小时。
>
> 示例：
> - `0 22 * * *` = 北京时间早上 6:00
> - `0 0 * * *` = 北京时间早上 8:00
> - `0 2 * * *` = 北京时间早上 10:00

### 6. 推送代码

推送后 GitHub Actions 会自动开始每天执行。你也可以在 Actions 页面手动点击 **Run workflow** 立即测试。

## 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 设置环境变量
export WEIBO_COOKIE="你的Cookie"
export AI_API_KEY="你的AI_API_Key"
export AI_PROVIDER="mimo"          # 可选
export AI_API_BASE="https://..."   # 可选

# 3. 运行
python main.py
```

## 更换 AI 服务

### 切换到 Claude

1. 在 GitHub Secrets 中设置：`AI_PROVIDER=claude`
2. 安装依赖（或在 workflow 中取消注释 `pip install anthropic`）

### 切换到 OpenAI 或其他兼容接口

1. 设置：`AI_PROVIDER=openai`
2. 设置 `AI_API_BASE` 为对应地址
3. 设置 `AI_MODEL` 为对应模型名

### 添加自定义 AI Provider

编辑 `ai_provider.py`：

```python
class MyProvider(AIProvider):
    @classmethod
    def name(cls):
        return "my_provider"

    def generate_post(self, topics, style="自然随性"):
        # 你的实现
        ...

# 注册
register_provider("my_provider", MyProvider)
```

然后在 Secrets 中设置 `AI_PROVIDER=my_provider`。

## 项目结构

```
weibo-bot/
├── .github/workflows/daily.yml   # GitHub Actions 定时任务
├── main.py                       # 主入口
├── weibo_client.py               # 微博 API（签到 + 发帖）
├── ai_provider.py                # AI 接口（可插拔）
├── config.yaml                   # 超话列表等配置
├── requirements.txt              # Python 依赖
└── README.md
```

## 常见问题

**Q: Cookie 多久会过期？**
A: 通常几个月，失效后重新获取并更新 Secrets 即可。

**Q: 签到失败怎么办？**
A: 检查超话名称是否正确，或手动填入 containerid。GitHub Actions 中失败的执行会标红，你可以随时查看日志。

**Q: GitHub Actions 免费额度够用吗？**
A: 完全够用。公开仓库无限免费，私有仓库每月 2000 分钟。每天运行不到 1 分钟。

**Q: 如何确认脚本正常工作？**
A: 在仓库的 Actions 页面查看每次执行的日志，签到和发帖结果都有清晰输出。
