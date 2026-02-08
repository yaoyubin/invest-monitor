# 投资日报

每日抓取**持仓相关新闻**与**上市公司财报**新闻，7 天去重后通过 Gmail 发送一封 HTML 日报。

## 功能特性

- **持仓新闻**：按 `invest_config.py` 中的持仓列表（A 股/美股/加密货币）抓取相关新闻（RSS/搜索）
- **财报新闻**：按关注股票抓取财报相关新闻
- **7 天去重**：同一链接 7 天内不重复推送，记录保存在 `invest_history.json`
- **Gmail 推送**：HTML 邮件，支持 GitHub Actions 自动运行并提交去重记录

## 快速开始

### 1. 配置 Gmail

#### 获取 Gmail 应用专用密码

1. 登录 [Google 账户](https://myaccount.google.com/security)，确保已启用**两步验证**
2. 进入 **应用专用密码**（App passwords），选择「邮件」→ 其他（自定义名称）→ 输入名称（如：`投资日报`）→ **生成**
3. 复制生成的 16 位密码（只显示一次）

#### 配置 GitHub Secrets（用于 Actions）

1. 仓库 **Settings** → **Secrets and variables** → **Actions**
2. 添加：
   - **`GMAIL_SENDER`**：发件 Gmail 地址
   - **`GMAIL_APP_PASSWORD`**：上述 16 位应用专用密码
   - **`GMAIL_RECIPIENT`**：收件邮箱

### 2. 配置持仓与关注股票

编辑 `invest_config.py`：

```python
# 持仓列表：用于「持仓新闻」搜索
HOLDINGS = [
    {"symbol": "TSM", "market": "us", "name": "台積電"},
    {"symbol": "BTC", "market": "crypto", "name": "比特币"},
    # market: cn / us / crypto
]

# 财报关注：None 表示用持仓中的股票（cn/us）做财报搜索
EARNINGS_WATCH = None
```

### 3. 本地运行

1. 复制 `.env.example` 为 `.env`，填入 Gmail 配置：
   ```env
   GMAIL_SENDER=yourname@gmail.com
   GMAIL_APP_PASSWORD=your_16_digit_app_password
   GMAIL_RECIPIENT=recipient@example.com
   ```
2. 安装依赖后运行：
   ```bash
   pip install -r requirements.txt
   python invest_daily.py
   ```

### 4. 自动运行（GitHub Actions）

- 工作流：`.github/workflows/invest_daily.yml`
- 默认每天 **UTC 0:00**（北京时间 8:00）运行，也可在 Actions 页手动触发
- 运行后会提交更新后的 `invest_history.json`，实现跨周期去重

## 项目结构

```
.
├── invest_daily.py       # 入口：拉配置 → 抓新闻 → 去重 → 组报 → 发邮件
├── invest_config.py     # 持仓、财报关注配置
├── invest/
│   ├── dedup.py         # 7 天去重（invest_history.json）
│   ├── news.py          # 持仓新闻 + 财报新闻抓取
│   └── report.py        # 日报 HTML 组装
├── tools/
│   ├── email_sender.py  # Gmail 发送
│   └── search_engine.py # 搜索（DuckDuckGo 等）
├── invest_history.json  # 7 天去重记录（自动生成/提交）
├── .github/workflows/
│   └── invest_daily.yml # 投资日报定时任务
└── README.md
```

## 注意事项

- `.env` 已加入 `.gitignore`，不会被提交
- 去重记录 `invest_history.json` 由 workflow 自动提交，便于多环境共享
- 后续可扩展：财报日历、Form 4、量化指标阈值、IPO 雷达等

## License

MIT
