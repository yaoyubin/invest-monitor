# 投资雷达 · 下一步计划

> 起点：2026-05-06，v2 上线（CI 邮件 + 本地 launchd HTML + 雪球 KOL）
> 观察期目标：连续跑 ≥ 2 周，攒到真实使用反馈，再决定哪些 next step 值得做。

---

## 📓 观察期日志（你边用边填）

打开每天的报告（早上 Gmail，晚上 `~/Documents/radar_reports/`），
记下两类东西：

**👀 让你眼睛一亮的 / 真有用的**
- 哪类信号你看到第一时间会停下来读？
- 哪条 thesis_delta 让你想立即查行情？
- 哪个 candidate trigger 你认真考虑过要不要建仓？

例子格式：
```
2026-05-08：AMD A 级 Q2 guidance 上修，验证 thesis +1，第二天就开盘看了
2026-05-12：INTC 苹果代工传闻 A 级，但翻原文发现是不确定报道，标的太宽了
```

**🙄 让你想跳过的 / 噪音**
- 哪类内容你每天都直接跳过？
- 哪个 KOL 多数时候写的和持仓没关系？
- 报告里哪个区块你几乎不看？

例子格式：
```
2026-05-10：雪球大V写东方甄选的内容连续三周都跟 watchlist 无关
2026-05-15：SA News 里 50% 是行业综述，跟单股 thesis 无关
```

记一两句话即可，攒 10-15 条就够指导决策。

---

## 🎯 候选 next step（按价值/工作量排）

### 🔥 立刻能做（半天，价值确定）

#### 1. Anthropic prompt caching → 省 ~50% LLM 费用
- system prompt + watchlist context（每次都重复 ~15K tokens）加 `cache_control`
- 改 `tools/llm_api.py` 一处 + system prompt 一处
- Anthropic 5 分钟 cache TTL，每天的 LLM 账单减半
- 难度：低

#### 2. HTML 雪球区块折叠
- 49 条雪球长文堆在一起视觉很重
- `<details>/<summary>` 默认折叠，只展示标题 + grade，点击展开
- 改 `invest/report.py` 中 `📰 雪球大V最新` 区块
- 难度：低

#### 3. 各区块内按 grade 排序
- 现在按 symbol 顺序，S/A 散在中间不显眼
- 改成 S → A → B → C，眼睛先抓到重点
- 改 `invest/report.py` 几处 sort
- 难度：低

---

### 📊 中等工作量（半天到一天，加深度）

#### 4. 巨潮 / 东方财富抓 A 股+港股公司公告
- 中港股持仓（TCEHY / BILI / MPNGY / 0981.HK）目前只靠 SA News 间接覆盖
- 巨潮资讯网 API 开放、无 WAF，提供官方公告（盈利预警 / 股东减持 / 业绩快报）
- 新增 `invest/cninfo.py` 或 `invest/eastmoney.py`，参考 `sa_rss.py` 模式
- 难度：中（爬 + 解析 + 集成 dedup）
- 触发条件：观察期发现中港股信号缺口大

#### 5. 周报聚合
- 周日跑一次"过去 7 天"汇总：哪些天 S/A 多、thesis 整体走向、候选有没有逼近触发
- 帮你识别"这周市场在 say 什么"，而不是天天看片段
- 复用现有数据，新增 `invest_weekly.py`
- 难度：中
- 触发条件：观察期发现"看每天太碎，看不到全局"

---

### 🧪 实验性（多于 1 天，不一定值）

#### 6. Scorer 评分回测
- 跑 4-8 周后回看：标 S/A 的事件后来股价/财报怎么样？
- 评估 prompt 是否过于激进/保守
- 需要数据沉淀 + 一些手工 label
- 难度：高
- 触发条件：观察期觉得 scorer 频繁误报或漏报

#### 7. 多 KOL 圈层
- "一日必读" 是核心，再加一组次要圈（每周看一次）
- watchlist.yaml 加 `tier` 字段，不同周期跑
- 难度：中
- 触发条件：发现想关注但不够格进"一日必读"的 KOL

#### 8. Slack/Discord/Lark 通知
- 邮件作为日报已经够了，但 S/S+ 级事件可以走即时通讯推送
- 难度：低-中
- 触发条件：发现"急事不能等下次开 Mac"

---

## ✅ 决策守则（2 周后回来时）

不要看到列表就一口气都做，按以下顺序：

1. **先看观察期日志** — 你的真实痛点是什么？
2. **拿痛点去 match** 上面的候选项 → 哪个最对应？
3. **只做 1-2 个**，不要批量
4. 做完再观察 1-2 周，重复

如果观察期日志几乎空白（说明产品对你够用了，没明显痛点），
**直接做 1 + 2 + 3 三个小改进**就停手 — 它们都是确定有价值的清扫工作。

---

## 🗂 当前已完成（v2，2026-05-06）

- ✅ Watchlist + thesis/red_flags/triggers
- ✅ Seeking Alpha News + Analysis 抓取
- ✅ yfinance 财报前瞻
- ✅ SEC Form 4 高管买卖
- ✅ 纳指 ETF 溢价
- ✅ LLM 评分（S/A/B/C + thesis_delta + candidate_hits）
- ✅ 雪球 16 KOL 长文（Playwright + 详情页全文）
- ✅ HTML 报告（雷达三件套 + 分组列表 + 雪球区块）
- ✅ CI（GitHub Actions）每天发邮件
- ✅ 本地 launchd 每天 15:00 PDT 写 HTML
- ✅ Anthropic streaming + 32K max_tokens
- ✅ macOS SSL_CERT_FILE 自动 fallback
- ✅ scorer debug dump 落盘（`./scorer_debug/`）
- ✅ scorer prompt 区分官方披露 vs KOL 观点
