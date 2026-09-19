"""
板块涨跌速览 + 当日归因

A股和美股各取涨幅前 5 / 跌幅前 5 的板块，再让带 web search 的 LLM 逐个说明当天
涨跌的原因。只看两端是刻意的：极端涨跌通常有明确消息面（政策、财报、商品价格、
地缘），中间那一堆是随机波动，硬解释只会制造噪音。

数据口径（launchd 在美西 15:00 跑，那一刻两个市场当日都已收盘）：
  A股  —— 东方财富行业板块（申万细分口径，约 500 个），按涨跌幅排序取两端。
          东财 push2 固定 302 到 push2delay（延迟 15 分钟），但美西 15:00 时
          A股早已收盘 15 小时，取到的就是收盘数据，延迟无影响。
          市值/成分股过小的板块会被过滤：3 只票拉 6% 不是板块行情。
  美股 —— yfinance 取一篮子行业/主题 ETF 的收盘涨跌幅（11 个 GICS 板块 ETF +
          细分主题共 30 余只）。用 ETF 而非指数，是因为 ETF 才是"板块"的可交易口径，
          而且细分主题（半导体、金矿、区域银行…）比 11 个大板块更能看出资金在炒什么。

归因只走 Anthropic 的 web_search server tool。拿不到搜索能力就只出涨跌表、不出原因 ——
没有当日新闻做依据时，LLM 讲的"原因"全是编的，比留白更有害。
"""
import concurrent.futures
import datetime
import json
import os
import re
import sys

import requests

# —— A股：东方财富行业板块 ——
# push2 会 302 到 push2delay，requests 默认跟随，这里不特意处理
EM_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EM_BOARD_FS = "m:90+t:2+f:!50"  # m:90=板块, t:2=行业板块
EM_FIELDS = "f2,f3,f8,f12,f14,f20,f104,f105,f128,f136,f207,f222"
EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/center/boardlist.html",
}
# 细分行业里有大量只有三五只票的迷你板块，一只涨停就能把板块拉到榜首。
# 用总市值 + 成分股数量筛掉，留下"涨跌真能代表一类资产"的板块。
MIN_BOARD_CAP = float(os.getenv("SECTOR_CN_MIN_CAP_YI", "500")) * 1e8  # 默认 500 亿总市值
MIN_BOARD_MEMBERS = int(os.getenv("SECTOR_CN_MIN_MEMBERS", "5"))

# —— A股大盘指数（新浪行情，GBK；必须带 Referer 否则 403，同 ic_basis）——
SINA_HQ_URL = "https://hq.sinajs.cn/list={symbols}"
SINA_HEADERS = {"User-Agent": EM_HEADERS["User-Agent"], "Referer": "https://finance.sina.com.cn"}
CN_INDEXES = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指")]

# —— 美股：行业/主题 ETF ——
# (代码, 中文名, 归类)；归类只用于报告里给板块加个 tag，方便一眼区分大板块和细分主题
US_SECTOR_ETFS = [
    # GICS 11 大板块（SPDR）
    ("XLK", "信息技术", "板块"),
    ("XLC", "通信服务", "板块"),
    ("XLY", "可选消费", "板块"),
    ("XLP", "必需消费", "板块"),
    ("XLE", "能源", "板块"),
    ("XLF", "金融", "板块"),
    ("XLV", "医疗保健", "板块"),
    ("XLI", "工业", "板块"),
    ("XLB", "原材料", "板块"),
    ("XLRE", "房地产", "板块"),
    ("XLU", "公用事业", "板块"),
    # 细分主题
    ("SMH", "半导体", "主题"),
    ("IGV", "软件", "主题"),
    ("SKYY", "云计算", "主题"),
    ("HACK", "网络安全", "主题"),
    ("BOTZ", "机器人与AI", "主题"),
    ("ARKK", "颠覆式创新", "主题"),
    ("IBB", "生物科技", "主题"),
    ("XBI", "生物科技(中小盘)", "主题"),
    ("IHI", "医疗器械", "主题"),
    ("KRE", "区域银行", "主题"),
    ("KIE", "保险", "主题"),
    ("XOP", "油气开采", "主题"),
    ("OIH", "油服", "主题"),
    ("TAN", "太阳能", "主题"),
    ("ICLN", "清洁能源", "主题"),
    ("URA", "铀与核电", "主题"),
    ("LIT", "锂电与电池", "主题"),
    ("GDX", "金矿", "主题"),
    ("XME", "金属与矿业", "主题"),
    ("ITA", "国防军工", "主题"),
    ("JETS", "航空", "主题"),
    ("IYT", "运输", "主题"),
    ("XRT", "零售", "主题"),
    ("XHB", "住宅建筑", "主题"),
    ("PAVE", "基础设施建设", "主题"),
    ("MOO", "农业", "主题"),
]
US_INDEXES = [("SPY", "标普500"), ("QQQ", "纳指100"), ("DIA", "道指"), ("IWM", "罗素2000")]

# —— 归因 LLM ——
# 只有 anthropic 有 web_search server tool，所以这里不跟随 SCORER_LLM_PROVIDER。
# 默认用 Sonnet 而非 scorer 的 Opus：这活儿是"搜到什么讲什么"的归纳，不需要顶配模型，
# 而且两个市场每天各跑一轮搜索（输入 10 万 token 量级），模型贵一档每天成本差好几倍。
SECTOR_LLM_MODEL = os.getenv("SECTOR_LLM_MODEL", "claude-sonnet-5")
SECTOR_LLM_TIMEOUT = int(os.getenv("SECTOR_LLM_TIMEOUT", "420"))
SECTOR_MAX_SEARCHES = int(os.getenv("SECTOR_MAX_SEARCHES", "8"))
# max_tokens 要给足：server tool 的搜索轮次和模型的中间输出都计入同一份 output 预算，
# 给 4000 时模型 10 次搜索就把预算耗光，一个字正文都没吐出来（stop_reason=max_tokens）。
SECTOR_MAX_TOKENS = int(os.getenv("SECTOR_MAX_TOKENS", "12000"))
SECTOR_DEBUG_DIR = os.getenv("SECTOR_DEBUG_DIR")  # 设为目录则把 prompt/原始回复落盘
# reason 的兜底长度上限：prompt 要求 40-70 字，但模型读英文搜索结果时容易整段粘原文，
# 一条能飙到 300 字，日报表格会被撑爆，所以这里再砍一刀
MAX_REASON_CHARS = int(os.getenv("SECTOR_MAX_REASON_CHARS", "140"))
# 带搜索时模型会把 <cite index="3-4">…</cite> 这类引用标签写进 JSON 字符串里
_CITE_TAG_RE = re.compile(r"</?cite[^>]*>", re.IGNORECASE)

REASON_SYSTEM_PROMPT = """你是帮投资者做"当日板块复盘"的分析助手。
用户给你某个市场当天涨幅前 5 / 跌幅前 5 的板块，你的任务是**先用 web search 查当天的新闻**，
再逐个说明这些板块当天为什么涨、为什么跌。

硬性要求：
1. 必须先搜索再回答。你的训练数据里没有这一天的行情，凭印象写出来的原因一律算编造。
2. 搜不到明确消息面时，reason 直接写"未查到明确消息面，可能是资金轮动/前期超跌反弹"之类的
   诚实描述，confidence 标 low。**绝对不要编一条看起来合理的理由。**
3. 每条 reason 用中文，40-70 字（超过 100 字算不合格），要具体：说清是什么政策/哪家公司的
   什么消息/什么商品价格/什么数据，而不是"受利好消息推动""市场情绪回暖"这种空话。
   reason 里禁止出现 <cite> 之类的引用标签，也禁止整句粘贴英文原文——英文新闻一律用中文转述，
   只有公司名/股票代码可以保留英文（如 NVDA、Nucor）。
4. 多个板块同源时（例如半导体设备、芯片设计、封测同涨），可以在各自 reason 里点同一个
   驱动因素，但要写清各自的落点差异。
5. 不给买卖建议，不预测后市。

confidence 口径：
- high：搜到当天直接对应该板块的新闻（政策发布、龙头公司公告、商品价格异动等）
- medium：搜到相关但间接的消息，或只搜到财经媒体的事后归因
- low：没搜到，只能给一般性解释

输出**严格的 JSON**，不要 markdown 代码块，不要任何额外说明文字：
{
  "market_summary": "一句话概括当天这个市场的主线，<=50字",
  "sectors": [
    {"name": "板块名（与输入完全一致）", "reason": "...", "confidence": "high|medium|low"}
  ]
}
sectors 必须覆盖输入里的每一个板块，name 原样抄回，不要改写。"""


# ============================== A股 ==============================

def _sina_quotes(symbols):
    """批量取新浪行情，返回 {symbol: [字段...]}；整体失败返回 {}。（与 ic_basis 同款调用）"""
    try:
        resp = requests.get(
            SINA_HQ_URL.format(symbols=",".join(symbols)),
            headers=SINA_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        resp.encoding = "gbk"  # 新浪行情固定 GBK，requests 猜错会把中文名弄乱
        text = resp.text
    except Exception as e:
        print(f"[sector_moves] 新浪行情请求失败: {e}")
        return {}
    return {
        m.group(1): m.group(2).split(",")
        for m in re.finditer(r'hq_str_(\w+)="([^"]*)"', text)
        if m.group(2).strip()
    }


def _fetch_cn_indexes():
    """三大指数当日涨跌幅 + 行情日期。返回 ({indexes}, quote_date) ，失败返回 ([], "")。"""
    quotes = _sina_quotes([code for code, _ in CN_INDEXES])
    out, quote_date = [], ""
    for code, name in CN_INDEXES:
        f = quotes.get(code)
        # 指数字段：2 昨收, 3 最新, 30 日期
        if not f or len(f) < 31:
            continue
        try:
            last, prev = float(f[3]), float(f[2])
        except ValueError:
            continue
        if not prev:
            continue
        out.append({"name": name, "close": round(last, 2), "pct": round((last / prev - 1) * 100, 2)})
        quote_date = quote_date or f[30].strip()
    return out, quote_date


def _fetch_cn_board_page(sort_desc):
    """取行业板块排行的一页（100 条）。sort_desc=True 取涨幅榜，False 取跌幅榜。"""
    params = {
        "pn": 1, "pz": 100, "po": 1 if sort_desc else 0, "np": 1,
        "fltt": 2, "invt": 2, "fid": "f3", "fs": EM_BOARD_FS, "fields": EM_FIELDS,
    }
    resp = requests.get(EM_CLIST_URL, params=params, headers=EM_HEADERS, timeout=20)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    return data.get("diff") or []


def _cn_board_row(raw):
    """把东财的 fXX 字段转成报告/LLM 用的结构；字段缺失就返回 None。"""
    name, code, pct = raw.get("f14"), raw.get("f12"), raw.get("f3")
    if not name or not code or not isinstance(pct, (int, float)):
        return None
    up, down = raw.get("f104") or 0, raw.get("f105") or 0
    return {
        "name": name,
        "code": code,
        "pct": round(float(pct), 2),
        "cap_yi": round((raw.get("f20") or 0) / 1e8),
        "turnover_pct": raw.get("f8"),
        "up_count": up,
        "down_count": down,
        "leader": raw.get("f128") or "",          # 领涨股
        "leader_pct": raw.get("f136"),
        "laggard": raw.get("f207") or "",         # 领跌股
        "laggard_pct": raw.get("f222"),
        "url": f"https://quote.eastmoney.com/bk/90.{code}.html",
    }


def _keep_cn_board(row):
    """过滤掉迷你板块：市值太小或成分股太少，涨跌幅代表不了一类资产。"""
    return (
        row["cap_yi"] * 1e8 >= MIN_BOARD_CAP
        and (row["up_count"] + row["down_count"]) >= MIN_BOARD_MEMBERS
    )


def fetch_cn_sectors(top_n=5):
    """A股行业板块涨幅前 N / 跌幅前 N。抓取失败返回 None（日报里整个小节省略）。"""
    try:
        gain_raw = _fetch_cn_board_page(sort_desc=True)
        lose_raw = _fetch_cn_board_page(sort_desc=False)
    except Exception as e:
        print(f"[sector_moves] 东方财富板块抓取失败: {e}")
        return None
    if not gain_raw and not lose_raw:
        print("[sector_moves] 东方财富板块返回空")
        return None

    def pick(raw_list, want_up):
        rows = [r for r in (_cn_board_row(x) for x in raw_list) if r and _keep_cn_board(r)]
        # 涨幅榜里只留真涨的，跌幅榜只留真跌的：全市场普涨那天跌幅榜可能凑不满 5 个，
        # 这时宁可少列几行，也不要把 +0.3% 的板块写成"跌幅榜"。
        rows = [r for r in rows if (r["pct"] > 0 if want_up else r["pct"] < 0)]
        return rows[:top_n]

    indexes, quote_date = _fetch_cn_indexes()
    return {
        "market": "A股",
        "source": "东方财富 · 行业板块",
        "as_of": quote_date or datetime.date.today().isoformat(),
        "indexes": indexes,
        "gainers": pick(gain_raw, True),
        "losers": pick(lose_raw, False),
    }


# ============================== 美股 ==============================

def _last_two_closes(series):
    """某只 ETF 最后两个有效收盘价 + 最后一根 K 线日期；数据不足返回 None。"""
    s = series.dropna()
    if len(s) < 2:
        return None
    return float(s.iloc[-1]), float(s.iloc[-2]), s.index[-1].date()


def fetch_us_sectors(top_n=5):
    """美股行业/主题 ETF 涨幅前 N / 跌幅前 N。抓取失败返回 None。"""
    try:
        import yfinance as yf
    except ImportError:
        print("[sector_moves] 未安装 yfinance，跳过美股板块")
        return None

    tickers = [s for s, _, _ in US_SECTOR_ETFS] + [s for s, _ in US_INDEXES]
    try:
        # 7 天窗口：覆盖长假也能拿到两根有效 K 线
        df = yf.download(tickers, period="7d", interval="1d",
                         auto_adjust=False, progress=False, threads=True)
        closes = df["Close"]
    except Exception as e:
        print(f"[sector_moves] yfinance 抓美股板块失败: {e}")
        return None

    def pct_of(symbol):
        if symbol not in closes.columns:
            return None
        return _last_two_closes(closes[symbol])

    rows, as_of = [], None
    for symbol, name, category in US_SECTOR_ETFS:
        got = pct_of(symbol)
        if not got:
            continue
        last, prev, bar_date = got
        if not prev:
            continue
        as_of = max(as_of, bar_date) if as_of else bar_date
        rows.append({
            "name": name,
            "code": symbol,
            "category": category,
            "pct": round((last / prev - 1) * 100, 2),
            "close": round(last, 2),
            "url": f"https://finance.yahoo.com/quote/{symbol}",
        })
    if not rows:
        print("[sector_moves] 美股板块 ETF 全部无有效行情")
        return None

    indexes = []
    for symbol, name in US_INDEXES:
        got = pct_of(symbol)
        if got and got[1]:
            indexes.append({
                "name": name,
                "close": round(got[0], 2),
                "pct": round((got[0] / got[1] - 1) * 100, 2),
            })

    rows.sort(key=lambda r: r["pct"], reverse=True)
    return {
        "market": "美股",
        "source": "行业/主题 ETF 收盘涨跌幅",
        "as_of": as_of.isoformat() if as_of else datetime.date.today().isoformat(),
        "indexes": indexes,
        "gainers": [r for r in rows if r["pct"] > 0][:top_n],
        "losers": [r for r in reversed(rows) if r["pct"] < 0][:top_n],
    }


# ============================== 归因 ==============================

def _reason_available():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _debug_dump(label, prompt, text, parsed=None, stop_reason=None, error=None):
    """SECTOR_DEBUG_DIR 设了就把归因的 prompt/原始回复落盘，方便事后查"为什么这条原因不对"。"""
    if not SECTOR_DEBUG_DIR:
        return
    try:
        os.makedirs(SECTOR_DEBUG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SECTOR_DEBUG_DIR, f"sector_{label}_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "model": SECTOR_LLM_MODEL, "prompt": prompt, "raw_response": text,
                "parsed": parsed, "stop_reason": stop_reason, "error": error,
            }, f, ensure_ascii=False, indent=2)
        print(f"[sector_moves] debug dump → {path}")
    except Exception as e:
        print(f"⚠️ [sector_moves] debug dump 失败：{e}", file=sys.stderr)


def _extract_json(text):
    """从 LLM 输出里抠出 JSON。带 web search 时模型常在 JSON 前后多说两句，所以不能直接 loads。"""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        lines = [l for l in text.splitlines() if not l.startswith("```")]
        text = "\n".join(lines)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception as e:
        print(f"[sector_moves] 归因 JSON 解析失败: {e}", file=sys.stderr)
        return None


def _build_reason_prompt(market_data):
    """把板块数据整理成 LLM 输入：带上指数背景，让它知道是普涨普跌还是结构性行情。"""
    market = market_data["market"]
    as_of = market_data["as_of"]
    idx = "、".join(f"{i['name']} {i['pct']:+.2f}%" for i in market_data.get("indexes") or [])

    def brief(rows):
        out = []
        for r in rows:
            item = {"name": r["name"], "涨跌幅": f"{r['pct']:+.2f}%"}
            if r.get("code"):
                item["代码"] = r["code"]
            if r.get("leader"):
                item["领涨股"] = f"{r['leader']} {r.get('leader_pct')}%"
            if r.get("laggard"):
                item["领跌股"] = f"{r['laggard']} {r.get('laggard_pct')}%"
            out.append(item)
        return out

    if market == "A股":
        hint = (
            "搜索时用中文，优先查财联社、证券时报、东方财富、同花顺、新浪财经等当日盘后复盘，"
            "关键词建议：「板块名 + 日期 + 大涨/大跌 原因」。A股板块名是申万细分行业口径。"
        )
    else:
        hint = (
            "搜索时用英文，优先查 Reuters / CNBC / Barron's / Yahoo Finance / Investing.com 的当日收盘综述。"
            "板块用的是 ETF 口径（代码即 ETF），归因时请说明是哪些成分股或什么宏观数据带动的。"
        )

    payload = {
        "市场": market,
        "交易日": as_of,
        "指数背景": idx or "（未取到）",
        "涨幅前列板块": brief(market_data.get("gainers") or []),
        "跌幅前列板块": brief(market_data.get("losers") or []),
    }
    return (
        f"请对 {as_of} 这个交易日的{market}板块做归因。{hint}\n"
        "优先把搜索预算花在涨跌幅最极端、以及明显不属于同一条主线的板块上。"
        "搜索过程中不要输出解说文字（那些字会挤占正文的 token 预算），搜完直接给 JSON。\n\n"
        f"数据：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _call_anthropic_with_search(prompt, max_searches):
    """单次 Anthropic 调用（开 web_search server tool），返回 (正文文本, stop_reason)。"""
    from anthropic import Anthropic

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=SECTOR_LLM_TIMEOUT)
    resp = client.messages.create(
        model=SECTOR_LLM_MODEL,
        max_tokens=SECTOR_MAX_TOKENS,
        system=REASON_SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}],
        messages=[{"role": "user", "content": prompt}],
    )
    # 带引用时正文会被切成多个 text block（每段配一组 citations），拼起来才是完整 JSON
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return text, resp.stop_reason


def _clean_reason(text):
    """剥掉引用标签、压掉多余空白，超长的截断（见 MAX_REASON_CHARS）。"""
    text = _CITE_TAG_RE.sub("", text or "").strip()
    text = re.sub(r"\s{2,}", " ", text)
    if len(text) > MAX_REASON_CHARS:
        text = text[:MAX_REASON_CHARS].rstrip("，,、。.；; ") + "…"
    return text


def _attach_reasons(market_data, parsed):
    """把 LLM 给的 reason/confidence 按板块名合并回行数据。"""
    by_name = {}
    for s in (parsed.get("sectors") or []):
        if s.get("name"):
            by_name[str(s["name"]).strip()] = s
    hit = 0
    for row in (market_data.get("gainers") or []) + (market_data.get("losers") or []):
        s = by_name.get(row["name"])
        if not s:
            continue
        row["reason"] = _clean_reason(s.get("reason"))
        row["confidence"] = (s.get("confidence") or "").strip().lower()
        hit += 1
    market_data["summary"] = _clean_reason(parsed.get("market_summary"))
    return hit


def explain_market(market_data):
    """给一个市场的 10 个板块做归因（就地写入 reason/confidence/summary）。"""
    if not market_data:
        return
    rows = (market_data.get("gainers") or []) + (market_data.get("losers") or [])
    if not rows:
        return
    label = market_data["market"]
    prompt = _build_reason_prompt(market_data)
    text, parsed = "", None
    # 重试一次时把搜索预算砍半：解析失败的典型原因就是搜索把 output 预算吃光、正文没写完
    for attempt, budget in enumerate((SECTOR_MAX_SEARCHES, max(SECTOR_MAX_SEARCHES // 2, 3)), 1):
        try:
            text, stop_reason = _call_anthropic_with_search(prompt, budget)
        except Exception as e:
            print(f"⚠️ [sector_moves] {label}板块归因调用失败（第 {attempt} 次）: {e}", file=sys.stderr)
            _debug_dump(label, prompt, None, error=str(e))
            continue
        parsed = _extract_json(text)
        _debug_dump(label, prompt, text, parsed=parsed, stop_reason=stop_reason)
        if parsed:
            break
        print(
            f"⚠️ [sector_moves] {label}板块归因无法解析（第 {attempt} 次，"
            f"stop_reason={stop_reason}, 正文 {len(text)} 字）",
            file=sys.stderr,
        )
    if not parsed:
        print(f"⚠️ [sector_moves] {label}板块归因放弃，只出涨跌表", file=sys.stderr)
        return
    hit = _attach_reasons(market_data, parsed)
    print(f"[sector_moves] {label}板块归因完成：{hit}/{len(rows)} 个板块拿到原因")


def get_sector_moves(top_n=5, with_reasons=True):
    """
    日报用的板块速览。返回：
      {
        "cn": {market, source, as_of, indexes[], gainers[], losers[], summary},
        "us": {...},
      }
    某个市场抓取失败时对应 key 为 None；两个都失败返回 None（日报省略整节）。
    每个板块行在归因成功时带 reason / confidence，失败时没有这两个 key（报告只显示涨跌幅）。
    """
    print("获取 A股/美股板块涨跌榜...")
    cn = fetch_cn_sectors(top_n)
    us = fetch_us_sectors(top_n)
    if not cn and not us:
        return None

    if with_reasons:
        if not _reason_available():
            print("[sector_moves] 未配置 ANTHROPIC_API_KEY，跳过归因（只出涨跌表）")
        else:
            # 两个市场的搜索互不相关，并行跑省一半时间（整轮日报有 30 分钟看门狗）
            targets = [m for m in (cn, us) if m]
            print(f"[sector_moves] 调用 LLM 归因（model={SECTOR_LLM_MODEL}, 市场数={len(targets)}）...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(explain_market, m) for m in targets]
                for f in futures:
                    try:
                        f.result(timeout=SECTOR_LLM_TIMEOUT + 30)
                    except Exception as e:
                        print(f"⚠️ [sector_moves] 归因线程异常: {e}", file=sys.stderr)

    return {"cn": cn, "us": us}
