# 投资雷达 / Investment Radar

按股票分组的**投资雷达日报**：财报前瞻 + Seeking Alpha + 高管买卖 → 7 天去重 → **LLM 评分（S/A/B/C + thesis delta + entry trigger）** → Gmail HTML 邮件。

> 这不是一个"AI 告诉你买什么卖什么"的系统。它的目标是**减少遗漏，过滤噪音，检查投资 thesis 是否发生变化**。

## 核心设计

watchlist 分两类标的：

| 类型 | 用途 | 雷达每日检查 |
|------|------|--------------|
| **holdings** | 已持仓 | **thesis delta**：今日信息是支持还是削弱了我的持有理由（-2 / -1 / 0 / +1 / +2） |
| **candidates** | 关注但未买 | **entry trigger**：是否命中了我事先写下的"什么发生我会重新看它"的条件 |

每条信息按硬规则评分：

- **S**（80+ 分）：可能改变长期 thesis，仅来自官方披露 + 影响 revenue/guidance/margin
- **A**（60-79）：值得当天看（财报、重大订单、命中 candidate trigger 等）
- **B**（40-59）：放入低优先级，仅在分组列表中带 B 角标
- **C**（<40）：忽略

只有 S/A 级会出现在邮件顶部的"今日重要事件"区块；候选标的命中的 trigger 单独成节。

## 功能特性

- **财报前瞻**：yfinance 获取美股下次财报日期，未来两周内提示
- **Seeking Alpha**：按标的抓取 News + Analysis combined feed，7 天去重（按 `invest_history.json`）
- **高管买卖（Form 4）**：通过 Finnhub 抓取（需配置 `FINNHUB_API_KEY`）
- **LLM 雷达评分**：单次批量调用，按 watchlist 上下文判定等级与 thesis 变化（未配置 LLM key 时优雅降级）
- **纳指 ETF 溢价提醒**：`159632` / `513300` 溢价异常时提示
- **Gmail 推送**：HTML 邮件，GitHub Actions 每日 UTC 0:00（北京 8:00）自动运行

## 快速开始

### 1. 配置 Gmail

#### 获取 Gmail 应用专用密码

1. 登录 [Google 账户](https://myaccount.google.com/security)，确保已启用**两步验证**
2. 进入 **应用专用密码**（App passwords），选择「邮件」→ 其他 → 输入名称 → 生成
3. 复制 16 位密码

#### 配置 GitHub Secrets

仓库 **Settings** → **Secrets and variables** → **Actions** → 添加：

- `GMAIL_SENDER` / `GMAIL_APP_PASSWORD` / `GMAIL_RECIPIENT`
- 可选：`FINNHUB_API_KEY`（高管买卖）、`OPENAI_API_KEY`（LLM 雷达评分）

### 2. 配置 watchlist

编辑根目录 `watchlist.yaml`：

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

candidates:
  INTC:
    name: Intel
    market: us
    why_not_buying_now:
      - 代工业务持续亏损
      - 制程仍落后 TSMC 1-2 代
    revisit_triggers:        # 命中任一 → 升 A 级提醒
      - 拿到外部大客户代工订单（NVDA / QCOM / AAPL）
      - 18A 制程量产时间确认且良率公开披露
```

**纪律**：candidates 上限 10 只；每只必须写出 `why_not_buying_now` 和**可验证的** `revisit_triggers`（"基本面改善"不算，"季度毛利率连续两季改善"才算）。

> 若 `watchlist.yaml` 不存在，会回退到 `invest_config.py` 中的 `HOLDINGS` 列表（旧行为，不启用 candidates 雷达）。

### 3. 本地运行

```bash
cp .env.example .env   # 填入 Gmail / Finnhub / LLM 配置
pip install -r requirements.txt
python invest_daily.py
```

`.env` 里 `OPENAI_API_KEY` 不填也能跑——所有信息默认按 B 级直接列出（旧行为），但不会有 thesis delta / trigger 检测。

### 4. 自动运行（GitHub Actions）

工作流 `.github/workflows/invest_daily.yml` 默认每天 UTC 0:00 运行；运行后会自动 commit 更新的 `invest_history.json`。

### 5. 本地 dry-run 预览（不发邮件）

调整 `watchlist.yaml` 或开发新功能时，用 `dry_run.py` 跑完整 pipeline 但不发 Gmail，HTML 写入 `/tmp/radar_preview.html`：

```bash
python dry_run.py             # 不调 LLM（fallback：全部 B 级，thesis delta 全 0）
python dry_run.py --use-llm   # 调用 LLM 评分（需配置 SCORER_LLM_PROVIDER + key）
open /tmp/radar_preview.html  # macOS 浏览器预览
```

> macOS 系统 Python 若遇 SSL 证书报错，临时设置：`export SSL_CERT_FILE=$(python3 -c 'import certifi;print(certifi.where())')`

## 邮件输出结构

```
投资雷达日报 · 2026-04-26

🚨 今日 S/A 级事件
  S 级（1 条）
    [AMD] AMD raises Q2 AI GPU guidance to $4.5B
        📝 直接上修核心 thesis 数字
        🎯 增强 AI GPU revenue growth thesis
  A 级（2 条）
    ...

🎯 候选标的 trigger 命中
  [INTC] Intel
    ✅ 命中：拿到外部大客户代工订单
    证据：Intel 与 NVDA 签署 18A 代工协议（id: ...）

📊 持仓 thesis delta
  AMD   +1   AI GPU 需求有正面信号，但还不够改变判断
  TSLA   0   今日无重大变化
  TCEHY -1   南向资金连续三日净流出

📚 按股票分组的原始信息
  AMD / TSLA / TSM / ...   每条信息带 S/A/B/C 角标

纳斯达克ETF溢价率（如有异常）
```

## 项目结构

```
.
├── invest_daily.py        # 入口：聚合 → 去重 → 雷达评分 → 组报 → 发邮件
├── invest_config.py       # 加载 watchlist.yaml，提供 holdings/candidates/earnings 接口
├── watchlist.yaml         # 持仓 + 候选 + thesis/red_flags/triggers
├── invest/
│   ├── dedup.py              # 7 天去重
│   ├── earnings_forward.py   # 财报前瞻（yfinance）
│   ├── form4.py              # 高管买卖（Finnhub）
│   ├── sa_rss.py             # Seeking Alpha combined feed
│   ├── haoetf.py             # 纳指 ETF 溢价
│   ├── scorer.py             # LLM 雷达评分（S/A/B/C + thesis delta + trigger）
│   └── report.py             # HTML 组装（雷达三件套 + 分组列表）
├── tools/
│   ├── email_sender.py       # Gmail 发送
│   └── llm_api.py            # 多 provider LLM 客户端
├── invest_history.json    # 去重记录（GitHub Actions 自动提交）
└── .github/workflows/invest_daily.yml
```

## 设计纪律（来自实战教训）

1. **不预测股价、不给买卖建议**——LLM 只做信息整理和 thesis 检查
2. **S/A 必须满足硬规则**——避免 LLM 把所有新闻都说得很重要
3. **candidates 上限 10 只**——超出说明没认真想
4. **trigger 必须可验证**——模糊词（"基本面改善"）一律不接受
5. **季度复盘 candidates**——触发过未行动的要么删除、要么升级为持仓

## 注意事项

- `.env` 已加入 `.gitignore`
- `invest_history.json` 由 workflow 自动提交以实现跨周期去重
- LLM 评分目前用单次批量调用；信息条目过多（>50）时建议分批

## License

MIT
