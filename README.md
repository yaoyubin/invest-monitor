# 投资雷达 / Investment Radar

按主题分组的**投资雷达日报**：聚合财报 / 新闻 / 高管买卖 / 雪球大 V / YouTube 财经 UP 主 / 机构 13F 持仓变动 → 7 天去重 → **LLM 评分（S/A/B/C + thesis delta + entry trigger）** → HTML 日报（Gmail 邮件 + 本地文件）。

> 这不是一个"AI 告诉你买什么卖什么"的系统。目标是**减少遗漏、过滤噪音、检查投资 thesis 是否被强化或削弱**，把决策权留给自己。

---

## 核心设计

watchlist 分两类标的：

| 类型 | 用途 | 每日检查 |
|------|------|---------|
| **holdings** | 已持仓 | **thesis delta**：今日信息是支持还是削弱了持有理由（-2 / -1 / 0 / +1 / +2） |
| **candidates** | 关注但未买 | **entry trigger**：是否命中事先写下的"什么发生我会重新看它"的条件 |

每条信息按硬规则评分：

| 级别 | 分数 | 含义 |
|---|---|---|
| **S** | 80+ | 可能改变长期 thesis（仅官方披露 + 影响 revenue/guidance/margin） |
| **A** | 60-79 | 值得当天看（财报、重大订单、命中 candidate trigger） |
| **B** | 40-59 | 低优先级，仅在分组列表里带 B 角标 |
| **C** | <40 | 忽略，但保留可查 |

只有 S/A 会上邮件顶部"今日重要事件"区块；候选 trigger 命中 + 持仓 thesis_delta 表分别独立成节。

---

## 架构

```
                ┌──────────────────────────────────┐
                │   watchlist.yaml                  │
                │   holdings / candidates / KOL /   │
                │   UP 主 / 机构 filer              │
                └────────────────┬─────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────┐
│                  8 个数据源（并行抓取）                          │
├────────────────────────────────────────────────────────────────┤
│  📈 财报前瞻 (yfinance)              📰 Seeking Alpha (RSS)     │
│  👥 SEC Form 4 高管买卖              💱 纳指 ETF 溢价 (haoetf)  │
│  🔴 雪球大 V (Playwright)            🎥 YouTube (字幕/Gemini)   │
│  🏦 SEC 13F-HR 机构持仓 diff         📉 IC 年化贴水 (新浪行情)  │
└──────────────────────────┬─────────────────────────────────────┘
                           ▼
              ┌────────────────────────────┐
              │  7 天 dedup                 │
              │  (invest_history.json)     │
              └────────────┬───────────────┘
                           ▼
              ┌────────────────────────────────────┐
              │  LLM scorer (Anthropic Sonnet 4.6) │
              │  一次批量调用 + streaming           │
              │  → grade / why / thesis_impact /   │
              │     trigger_hit / thesis_delta     │
              └────────────┬───────────────────────┘
                           ▼
              ┌────────────────────────────────────┐
              │  HTML 日报 (invest/report.py)       │
              │  🚨 S/A 事件区  🎯 候选 trigger    │
              │  📊 thesis delta 表                 │
              │  📰 雪球 / 🎥 YT  🏦 13F 大资金     │
              │  📉 IC 贴水  💱 ETF 溢价            │
              │  📚 按股票分组（最后）              │
              └──────┬─────────────────────┬────────┘
                     │                     │
        ┌────────────▼────────┐  ┌─────────▼──────────────┐
        │  CI (GitHub Actions) │  │  本地 launchd (macOS)   │
        │  每天 UTC 0:00 → Gmail│  │  每天 15:00 PDT → HTML  │
        │  无雪球（无登录态）   │  │  含雪球（chrome_profile）│
        └──────────────────────┘  └─────────────────────────┘
```

---

## 数据源（8 个）

| 模块 | 抓什么 | 工具 | 在哪跑 | 特殊要求 |
|---|---|---|---|---|
| `invest/earnings_forward.py` | 未来 14 天财报日历 | yfinance | CI + 本地 | — |
| `invest/sa_rss.py` | Seeking Alpha News + Analysis | RSS | CI + 本地 | — |
| `invest/form4.py` | SEC Form 4 高管买卖 | Finnhub | CI + 本地 | `FINNHUB_API_KEY`（可选） |
| `invest/haoetf.py` | 纳指 ETF 溢价 | haoetf HTTP | CI + 本地 | — |
| `invest/xueqiu.py` | 雪球大 V 最新长文 / 长动态 | Playwright (真浏览器) | **仅本地** | `chrome_profile/` 已登录 |
| `invest/youtube.py` | YouTube 财经 UP 主视频总结 | RSS / channel HTML + 字幕 + Gemini fallback | CI + 本地 | `GOOGLE_API_KEY`（字幕禁的视频用 Gemini 读视频） |
| `invest/sec_13f.py` | 机构 13F-HR 季度持仓变动 diff | SEC EDGAR | CI + 本地 | — |
| `invest/ic_basis.py` | IC 中证500股指期货当月/次月/当季/下季年化贴水 | 新浪行情 HTTP | CI + 本地 | — |

**为什么雪球只在本地跑**：雪球用 Aliyun WAF JS 挑战，纯 HTTP 客户端绕不过去，必须真浏览器执行 JS。CI runner 上没有持久化的浏览器登录态，所以雪球只在本地 launchd 跑。

---

## 快速开始

### 1. 配置 watchlist.yaml

唯一配置真相在 `watchlist.yaml`，包含 5 节：

```yaml
holdings:
  AMD:
    name: AMD
    market: us
    thesis:
      - AI GPU revenue growth 持续超预期
      - Data Center 占比提升带动毛利率改善
    red_flags:
      - AI GPU guidance 下修
      - 大客户转向自研 ASIC

candidates:                # 上限 10 只
  INTC:
    name: Intel
    market: us
    why_not_buying_now:
      - 代工业务持续亏损
    revisit_triggers:       # 必须可验证
      - 拿到外部大客户代工订单（NVDA / QCOM / AAPL / AMD）
      - 18A 制程量产时间确认且良率公开披露

xueqiu_kols:               # 雪球大V uid 列表
  - {uid: 1965894836, name: 'PaulWu'}

youtube_creators:          # YouTube 频道
  - {handle: hackbearterry, name: 'Terry', channel_id: UC_whOg3XES3Fihic53fvo4Q}

institutional_filers:      # SEC 13F 监控的机构
  - {cik: '0001067983', name: 'Berkshire Hathaway'}
  - {cik: '0001167483', name: 'Tiger Global'}
```

### 2. 安装 + 基础 env

```bash
pip install -r requirements.txt
cp .env.example .env       # 编辑填入下面这些 key
```

`.env` 必填（无 LLM key 整套退化为"全 B 级 + 不评 thesis"）：

```
SCORER_LLM_PROVIDER=anthropic
SCORER_LLM_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...
```

可选 key：

```
GOOGLE_API_KEY=AIza...        # YouTube：字幕禁的视频用 Gemini 直读
FINNHUB_API_KEY=...           # 高管买卖
```

发邮件场景额外加：

```
GMAIL_SENDER=you@gmail.com
GMAIL_APP_PASSWORD=16位app密码
GMAIL_RECIPIENT=you@gmail.com
```

### 3. 本地预览（不发邮件，最常用的开发循环）

```bash
python dry_run.py                                  # 不调 LLM，全 B 级
python dry_run.py --use-llm                         # 真评分
python dry_run.py --use-llm --include-xueqiu        # 加雪球（需先 6. 配置）
python dry_run.py --use-llm --include-youtube       # 加 YouTube
python dry_run.py --use-llm --include-13f           # 加机构 13F
python dry_run.py --use-llm --limit 30              # 只评分前 30 条，省 token
open /tmp/radar_preview.html
```

### 4. 部署：CI（Gmail） + 本地 launchd（HTML）

详见下面"部署方式"一节。

### 5. 雪球（可选，强烈推荐做中港股深度分析）

```bash
# 一次性安装
pip install playwright
python -m playwright install chromium     # ~150MB

# 首次登录（弹浏览器登录后回 terminal 按回车，登录态保存到 chrome_profile/）
python -m invest.xueqiu --login --limit 1

# 自动从主页 title 抓昵称回填到 watchlist.yaml
python -m invest.xueqiu --names

# 之后 headless 跑，独立测试
python -m invest.xueqiu --limit 3 --debug
```

### 6. YouTube + Gemini（已默认开启）

不需要额外步骤。`GOOGLE_API_KEY` 配了就自动启用 Gemini fallback（字幕禁的视频走 Gemini 直读视频）。

申请免费 Gemini key：[aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### 7. 机构 13F 监控（已默认开启）

`watchlist.yaml` 里 `institutional_filers` 加 CIK 即可。CIK 查询：[SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=&owner=include&action=getcompany)。

首次启用某机构时，需 bootstrap 上一期 baseline，否则没法 diff：

```bash
python -m invest.sec_13f --filer 0001067983 --latest    # 看持仓 top 20
python -m invest.sec_13f --check                         # 跑 diff（首次只会建立 baseline）
```

snapshot 落到 `sec_13f_snapshots/`，由 CI 自动 commit 跨运行保留。

---

## 部署方式：CI vs 本地 launchd

|  | CI (GitHub Actions) | 本地 launchd (macOS) |
|---|---|---|
| 触发 | 每天 UTC 0:00（北京 8:00） | 每天 15:00 PDT，Mac 睡眠会唤醒后找补 |
| 输出 | Gmail 邮件 | HTML 写到 `~/Documents/radar_reports/radar_YYYY-MM-DD.html` |
| 雪球 | ❌（无 chrome_profile） | ✅ |
| YouTube | ✅ | ✅ |
| 13F | ✅ | ✅ |
| 控制变量 | GitHub Secrets | `.env` + plist 里的 `RADAR_LOCAL_OUTPUT_DIR` |

### CI 配置

仓库 **Settings → Secrets and variables → Actions** 加：

- `GMAIL_SENDER` / `GMAIL_APP_PASSWORD` / `GMAIL_RECIPIENT`
- `ANTHROPIC_API_KEY` + `SCORER_LLM_PROVIDER=anthropic` + `SCORER_LLM_MODEL=claude-sonnet-4-6`
- `GOOGLE_API_KEY`（YouTube Gemini fallback）
- `FINNHUB_API_KEY`（可选）

CI workflow 在 `.github/workflows/invest_daily.yml`，每天自动 commit 更新的 `invest_history.json` 和 `sec_13f_snapshots/`。

### 本地 launchd 配置（macOS）

详见 `~/Documents/radar_reports/README.md`（任务跑成功后自动生成的本地文档）。简要：

1. 创建 `~/Library/LaunchAgents/com.ryan.invest-radar.plist`，触发 15:00 跑 `invest_daily.py`
2. 设 `RADAR_LOCAL_OUTPUT_DIR=~/Documents/radar_reports` 让它写本地 HTML 而不是发邮件
3. 设 `ENABLE_XUEQIU=1` 开启雪球
4. `launchctl load ~/Library/LaunchAgents/com.ryan.invest-radar.plist`
5. 验证：`launchctl start com.ryan.invest-radar && tail -f /tmp/invest_radar.stdout.log`

---

## 邮件 / HTML 输出结构

```
投资雷达日报 · 2026-05-15

🚨 今日 S/A 级事件                           ← 跨所有源排序
  S 级（1 条）
    [AMD] AMD Q1 双超预期 + Q2 指引 $11.2B
        📝 直接上修核心 thesis 数字
        🎯 增强 AI GPU revenue growth thesis

  A 级（3 条）
    [Berkshire Hathaway] 加仓 GOOGL +204%：$5.59B → $15.60B  🤖 科技/AI
    [YouTube (Terry)] 美元 50 年霸權的崩解 ...
    ...

🎯 候选标的 trigger 命中
  [INTC] Intel
    ✅ 拿到外部大客户代工订单（NVDA / QCOM / AAPL / AMD）
    证据：Bloomberg 报道苹果考虑使用英特尔代工主芯片（注：报道非确定合同）

📊 持仓 thesis delta
  AMD    +2   Q1 双超预期 + 数据中心 +57% + Q2 指引 $11.2B
  TSLA   -1   FSD 欧洲监管受阻削弱放行 thesis
  ...

📅 财报日历                                  ← 独立区块（新）
  🔴 明天 · [BILI] Bilibili 2026-05-19
  🔴 后天 · [NVDA] NVIDIA 2026-05-20 [A]
  🟡 8 天后 · [MPNGY] MeiTuan 2026-05-26
  ⚪ 8 周后 · [TSM] TSMC 2026-07-16

👥 高管买卖 (Form 4)                          ← 独立区块（新）
  [A] [AMD] Lisa Su 买入 5000 股 · 2026-05-17
      📝 CEO 大额买入信号强
  [B] [TSLA] CFO 卖出 10000 股 · 2026-05-15

📰 雪球大V最新（49 条，AI 相关 12 / 其余 37）
  PaulWu / 滑雪特 / 博实 / ...

🎥 YouTube 财经 UP 主（3 条）
  贝拉聊财金 · 下周美股"超级审判周"... 📝 折叠总结
  Terry · 美元 50 年霸權的崩解 ...

🏦 大资金动向 / 13F-HR（97 项，含 9 项科技/AI）
  Berkshire / Tiger Global / Coatue ...

📉 IC 年化贴水（当月 -15.2% / 次月 -11.6% / 当季 -10.9% / 下季 -10.2%）
💱 纳指 ETF 溢价（159632 +2.88%）

📚 按股票分组的原始信息                        ← 置于报告最后
  AMD / TSLA / TSM / TCEHY / BILI / MPNGY / IBIT / INTC ...
```

---

## 项目结构

```
invest-monitor/
├── invest_daily.py              # CI 入口（发 Gmail / 写本地 HTML）
├── dry_run.py                   # 开发测试入口（不发邮件）
├── invest_config.py             # watchlist.yaml 解析
├── watchlist.yaml               # 唯一配置真相
│
├── invest/                      # 数据源模块（每个独立可单测）
│   ├── earnings_forward.py      # yfinance 财报日历
│   ├── sa_rss.py                # Seeking Alpha combined feed
│   ├── form4.py                 # SEC Form 4 高管买卖
│   ├── haoetf.py                # 纳指 ETF 溢价
│   ├── ic_basis.py              # IC 股指期货年化贴水
│   ├── xueqiu.py                # 雪球大V Playwright 抓取
│   ├── youtube.py               # YouTube channel HTML + 字幕 + Gemini fallback
│   ├── sec_13f.py               # SEC 13F-HR 机构持仓 + diff 引擎
│   ├── dedup.py                 # 7 天 history 去重
│   ├── scorer.py                # LLM 评分 + thesis_delta + trigger
│   └── report.py                # HTML 渲染（S/A 区 / 分组 / 各源专区）
│
├── tools/
│   ├── llm_api.py               # 多 provider LLM 客户端（Anthropic 主，Gemini fallback）
│   ├── email_sender.py          # Gmail 发送
│   └── send_failure_alert.py    # CI 失败告警
│
├── invest_history.json          # 7 天去重记录（CI 自动 commit）
├── sec_13f_snapshots/           # 机构 13F 持仓快照（CI 自动 commit）
├── chrome_profile/              # .gitignore（雪球登录态，仅本地）
├── scorer_debug/                # .gitignore（LLM 调用 dump）
│
├── .env.example                 # env 模板
├── requirements.txt
├── NEXT_STEPS.md                # roadmap + 观察期日志
│
└── .github/workflows/invest_daily.yml   # CI 每日定时
```

---

## watchlist.yaml 全字段参考

```yaml
# === 持仓 ===
holdings:
  <SYMBOL>:                       # 如 AMD / TSLA
    name: <显示名>
    market: us | hk | crypto
    thesis:                       # 持有的核心理由，scorer 据此评估增强/削弱
      - "..."
    red_flags:                    # 让你重新评估或卖出的信号
      - "..."

# === 候选标的（上限 10） ===
candidates:
  <SYMBOL>:
    name: <显示名>
    market: us | hk
    why_not_buying_now:           # 现在不买的具体理由，写不出来就不该放进来
      - "..."
    revisit_triggers:             # 命中任一 → 升 A 级提醒；必须可验证
      - "拿到外部大客户代工订单（NVDA / QCOM / AAPL / AMD）"
      - "季度毛利率连续两季改善"

# === 雪球大V ===
xueqiu_kols:
  - {uid: <uid>, name: <昵称>}    # 或裸 uid

# === YouTube 财经 UP 主 ===
youtube_creators:
  - {handle: <handle>, name: <中文名>, channel_id: UC...}

# === 机构 13F filer ===
institutional_filers:
  - {cik: '<10 位 CIK>', name: <机构名>}
```

---

## 设计纪律（来自实战教训）

1. **不预测股价、不给买卖建议** — LLM 只做信息整理和 thesis 检查
2. **S/A 必须满足硬规则** — 避免 LLM 把所有新闻都说得重要
3. **candidates 上限 10 只** — 超出说明没认真想
4. **trigger 必须可验证** — "基本面改善"不算，"季度毛利率连续两季改善"才算
5. **季度复盘 candidates** — 触发过未行动的要么删除、要么升级为持仓
6. **数据源宁少勿多** — 每加一个源前先想"它能告诉我 watchlist 里 8 个标的 thesis 是否变化吗"

---

## 注意事项

- `.env` 已加入 `.gitignore`，含 API key 永不上传
- `chrome_profile/` 含雪球登录态，**绝对不能上传 GitHub**（已 .gitignore）
- `invest_history.json` 和 `sec_13f_snapshots/` 由 CI 自动 commit & push（跨运行的 dedup / diff baseline）
- LLM 评分用单次批量调用 + streaming，max_tokens=32K；200+ 条数据时单次成本约 $0.10
- macOS 系统 Python 的 SSL 证书问题已在代码里自动 fallback 到 `certifi.where()`，无需手动 export

## License

MIT
