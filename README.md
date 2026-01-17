# B站UP主和YouTube频道视频监控系统

自动监控B站UP主和YouTube频道的新视频，检测AIGC相关内容并推送通知。

## 功能特性

- 🔍 **并发监控**：同时监控多个B站UP主和YouTube频道，智能控制并发数
- 🎯 **智能过滤**：关键词硬过滤 + 预留LLM语义判断
- 💾 **持久化记忆**：使用 `history.json` 记录已处理视频，避免重复推送
- 🧹 **自动清理**：7天前的记录自动过期删除
- 📧 **推送通知**：通过Gmail邮件发送通知（合并B站和YouTube的更新到同一封邮件）
- 🤖 **自动化运行**：GitHub Actions 每天自动运行

## 快速开始

### 1. 配置 GitHub Secrets

在 GitHub 仓库中配置 Gmail 邮件通知：

#### 步骤 1.1：获取 Gmail 应用专用密码

1. 登录你的 Gmail 账户
2. 进入 [Google 账户安全设置](https://myaccount.google.com/security)
3. 确保已启用 **两步验证**（必须）
4. 在安全设置中，找到 **应用专用密码** 或 **App passwords**
5. 选择 **邮件** 和设备类型（如：其他（自定义名称））
6. 输入名称（如：`B站监控`），点击 **生成**
7. **复制生成的16位密码**（只显示一次，请妥善保存）

#### 步骤 1.2：配置 GitHub Secrets

1. 进入你的 GitHub 仓库
2. 点击 **Settings** → **Secrets and variables** → **Actions**
3. 点击 **New repository secret**，依次添加以下三个 secrets：

   - **`GMAIL_SENDER`**：你的 Gmail 邮箱地址（如：`yourname@gmail.com`）
   - **`GMAIL_APP_PASSWORD`**：刚才生成的16位应用专用密码
   - **`GMAIL_RECIPIENT`**：接收通知的邮箱地址（可以是同一邮箱或不同邮箱）
   - **`YOUTUBE_API_KEY`**（可选）：如果要监控YouTube频道，需要配置YouTube Data API v3密钥

**重要提示**：
- 必须使用**应用专用密码**，不能使用普通密码
- 如果未启用两步验证，无法生成应用专用密码
- 如需监控YouTube频道，请先获取YouTube Data API密钥（见下方说明）

#### YouTube API Key 获取方法（可选）

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建新项目或选择现有项目
3. 启用 **YouTube Data API v3**
4. 创建 **API密钥**（Credentials → Create Credentials → API Key）
5. 复制API密钥，添加到 GitHub Secrets 的 `YOUTUBE_API_KEY`

### 2. 配置监控的UP主和YouTube频道

编辑 `up_list.py` 文件：

#### 2.1 配置B站UP主

```python
# UP主列表：{UID: UP主名字}
UP_LIST = {
    4401694: "林亦LYi",
    130636947: "塑料叉FOKU",
    # 添加更多UP主的UID...
}

# 特殊UP主列表（这些UP主的视频不进行关键词过滤，直接推送）
NO_FILTER_UIDS = [
    419743655,  # BiBiPiano
    # 添加更多不需要关键词过滤的UP主UID...
]
```

#### 2.2 配置YouTube频道（可选）

```python
# YouTube频道列表：{Channel ID: 频道名字}
# Channel ID 格式：UCxxxxx（24个字符）
YOUTUBE_CHANNELS = {
    'UCxxxxx': "频道名字",
    # 添加更多频道的Channel ID...
}

# YouTube特殊频道列表（这些频道的视频不进行关键词过滤，直接推送）
YOUTUBE_NO_FILTER_CHANNELS = [
    'UCxxxxx',  # 频道名字
    # 添加更多不需要关键词过滤的频道ID...
]
```

**如何获取YouTube Channel ID**：
- 方法1：访问频道的 YouTube Studio，在"设置"→"高级设置"中查看Channel ID
- 方法2：访问频道页面，查看URL或页面源代码中的Channel ID

### 3. 配置关键词（可选）

编辑 `up_list.py` 文件，修改 `KEYWORDS` 列表：

```python
KEYWORDS = [
    "AIGC",
    "工作流",
    "模型",
    # 添加更多关键词...
]
```

### 4. 运行方式

#### 方式一：GitHub Actions 自动运行（推荐）

- 每天北京时间 9:00 自动运行
- 也可以在 **Actions** 页面手动触发

#### 方式二：本地运行

**推荐方式：使用 .env 文件（最简单）**

1. 复制 `.env.example` 为 `.env`：
   ```bash
   # Linux/Mac
   cp .env.example .env
   
   # Windows PowerShell
   copy .env.example .env
   
   # Windows CMD
   copy .env.example .env
   ```

2. 编辑 `.env` 文件，填入你的实际配置值：
   ```env
   GMAIL_SENDER=yourname@gmail.com
   GMAIL_APP_PASSWORD=your_16_digit_app_password
   GMAIL_RECIPIENT=recipient@example.com
   YOUTUBE_API_KEY=your_youtube_api_key  # 可选，仅在监控YouTube时需要
   ```

3. 运行脚本：
   ```bash
   python main.py
   ```

**备选方式：使用命令行设置环境变量**

如果不想使用 .env 文件，也可以通过命令行设置环境变量：

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量（Linux/Mac）
export GMAIL_SENDER="yourname@gmail.com"
export GMAIL_APP_PASSWORD="your_16_digit_app_password"
export GMAIL_RECIPIENT="recipient@example.com"
export YOUTUBE_API_KEY="your_youtube_api_key"  # 可选，仅在监控YouTube时需要

# 设置环境变量（Windows PowerShell）
$env:GMAIL_SENDER="yourname@gmail.com"
$env:GMAIL_APP_PASSWORD="your_16_digit_app_password"
$env:GMAIL_RECIPIENT="recipient@example.com"
$env:YOUTUBE_API_KEY="your_youtube_api_key"  # 可选，仅在监控YouTube时需要

# 设置环境变量（Windows CMD）
set GMAIL_SENDER=yourname@gmail.com
set GMAIL_APP_PASSWORD=your_16_digit_app_password
set GMAIL_RECIPIENT=recipient@example.com
set YOUTUBE_API_KEY=your_youtube_api_key  # 可选，仅在监控YouTube时需要

# 运行脚本
python main.py
```

**注意**：环境变量的优先级为：系统环境变量 > .env 文件。如果系统环境变量已设置，会优先使用系统环境变量。

#### 方式三：本地测试（推荐用于开发调试）

使用测试脚本可以预览日报/周报内容，不会发送真实邮件：

```bash
# 运行测试脚本
python test_local.py
```

测试脚本功能：
- ✅ 可以选择日报模式（26小时）或周报模式（7天）
- ✅ 不发送真实邮件，只在控制台显示邮件预览
- ✅ 使用临时文件 `history_test.json`，不会影响真实的 `history.json`
- ✅ 完整展示抓取的视频列表和报告格式

**注意事项**：
- 测试脚本会真实抓取UP主的视频数据
- 如果需要测试邮件发送，可以修改 `test_local.py` 中的 `SEND_REAL_EMAIL = True`

## 项目结构

```
.
├── main.py                    # 主程序
├── test_local.py              # 本地测试脚本
├── history.json               # 已处理视频记录（自动生成）
├── up_list.py                 # UP主列表配置
├── requirements.txt           # Python依赖
├── .github/
│   └── workflows/
│       └── daily.yml          # GitHub Actions配置
└── README.md                  # 本文件
```

## 工作原理

1. **Memory (记忆层)**：`HistoryManager` 类管理 `history.json`，记录已处理的视频（支持B站bvid和YouTube video_id）
2. **Fetcher (数据源)**：并发获取B站UP主和YouTube频道的最新视频列表
3. **Filter (过滤器)**：关键词过滤 → (预留)LLM语义判断，支持B站和YouTube两种平台
4. **Notifier (通知器)**：发送合并的推送消息（B站和YouTube更新在同一封邮件中）

## 注意事项

- `history.json` 会自动提交到仓库，实现跨运行周期的持久化
- 首次运行会创建 `history.json` 文件
- GitHub Actions 会自动提交更新后的 `history.json`
- 7天前的记录会自动清理

## 未来扩展

- [x] 支持YouTube频道监控
- [ ] 支持监控forum中的每天的新post
- [ ] 利用gemini总结youtube视频
- [ ] 接入LLM API实现语义判断
- [ ] 添加视频分类功能

## License

MIT
