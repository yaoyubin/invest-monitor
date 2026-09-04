"""
投资雷达日报入口：
  财报前瞻（yfinance）+ Finnhub 个股新闻 + 高管买卖（SA RSS 默认关闭，见 ENABLE_SA_RSS）
  → 7 天去重
  → LLM 评分（watchlist.yaml 的 holdings 看 thesis delta，candidates 看 entry trigger）
  → 组报 → 发 Gmail
"""
import asyncio
import datetime
import socket
import sys
import os
import threading

# 全局兜底超时：yfinance 等同步请求若不设超时，对端无响应会让进程永久卡死，
# launchd 看到旧实例未退出就不再触发，日报会连续静默缺失（2026-06-05 实际发生过）
socket.setdefaulttimeout(60)

# 进程级看门狗：上面那行只管 socket 模块自建的连接，管不住自带 HTTP 栈的 SDK
# （httpx/google-genai 显式传 timeout=None，直接盖掉全局默认）。2026-07-19 就是
# Gemini 直读视频卡死 14 天，launchd 因旧实例还在而静默跳过了之后每一天。
# 所以这里再兜一层：不管哪个库挂住，到点强制退出，让明天的触发能正常排上。
# 用 os._exit 而非 sys.exit —— 后者只在主线程抛异常，卡在 C 层的阻塞 read 收不到。
WATCHDOG_SEC = int(os.getenv("RADAR_WATCHDOG_SEC", "1800"))  # 30 分钟


def _watchdog_fire():
    print(
        f"⏱️ 看门狗超时：运行超过 {WATCHDOG_SEC} 秒仍未结束，强制退出以免阻塞明天的 launchd 触发。",
        file=sys.stderr,
        flush=True,
    )
    os._exit(75)  # EX_TEMPFAIL：区别于正常退出，方便日志里一眼认出


if WATCHDOG_SEC > 0:
    _wd = threading.Timer(WATCHDOG_SEC, _watchdog_fire)
    _wd.daemon = True
    _wd.start()

_project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _project_root)

# macOS 系统 Python 经常缺 root CA，feedparser/urllib 抓 SA 会 SSL_VERIFY_FAILED
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))

from invest_config import (
    get_candidates,
    get_earnings_stocks,
    get_holdings,
    get_seeking_alpha_tickers,
    get_xueqiu_kols,
    get_youtube_creators,
    get_institutional_filers,
)
from invest.dedup import InvestHistoryManager
from invest.earnings_forward import get_earnings_forward
from invest.finnhub_news import fetch_finnhub_news
from invest.form4 import fetch_form4
from invest.haoetf import get_ndq_etf_premiums
from invest.ic_basis import get_ic_basis
from invest.report import build_html
from invest.sa_rss import fetch_seeking_alpha
from invest.scorer import attach_grades, score_items
from tools.email_sender import send_gmail


# 是否启用雪球大V抓取（需 Playwright + chrome_profile/ 已登录）
# 设为 "1" / "true" 启用；建议本地跑设为 1，CI 上保持关闭直到登录态可携带
ENABLE_XUEQIU = os.getenv("ENABLE_XUEQIU", "0").lower() in ("1", "true", "yes")
XUEQIU_DAYS_BACK = int(os.getenv("XUEQIU_DAYS_BACK", "7"))
# YouTube 默认开启（不像雪球需要 Playwright，CI 也能跑；要关只需 watchlist 清空 youtube_creators）
ENABLE_YOUTUBE = os.getenv("ENABLE_YOUTUBE", "1").lower() in ("1", "true", "yes")
YOUTUBE_DAYS_BACK = int(os.getenv("YOUTUBE_DAYS_BACK", "2"))
# 13F 默认开启（每天一次轻量 EDGAR 检查；季度才有 filing，多数日子无变化）
ENABLE_13F = os.getenv("ENABLE_13F", "1").lower() in ("1", "true", "yes")
# Seeking Alpha News/Analysis 默认关闭（条数多导致日报过长；个股新闻由 Finnhub 覆盖）
ENABLE_SA_RSS = os.getenv("ENABLE_SA_RSS", "0").lower() in ("1", "true", "yes")
# 设了就把 HTML 写到该目录（按日期命名）作为本地备份，同时也发 Gmail。
# 本地 launchd 跑用此模式；CI 不设此变量，只发邮件不落盘
RADAR_LOCAL_OUTPUT_DIR = os.getenv("RADAR_LOCAL_OUTPUT_DIR")


def _earnings_forward_id(item):
    return f"earnings::{item['symbol']}::{item['earnings_date']}"


def main():
    earnings_stocks = get_earnings_stocks()
    sa_tickers = get_seeking_alpha_tickers()
    holdings = get_holdings()
    candidates = get_candidates()

    if not earnings_stocks and not sa_tickers:
        print("未配置财报关注或 Seeking Alpha 标的，请编辑 watchlist.yaml 或 invest_config.py 后重试。")
        return

    history = InvestHistoryManager()

    # 美股集合：财报关注 + SA 关注 + 候选标的（让候选股的财报/SA 信息也被收集）
    candidate_us = [c["symbol"] for c in candidates if c["market"] == "us"]
    us_symbols = list(dict.fromkeys(
        [h["symbol"] for h in earnings_stocks if h["market"] == "us"]
        + list(sa_tickers)
        + candidate_us
    ))

    earnings_forward = []
    if us_symbols:
        print("获取财报前瞻...")
        earnings_forward = get_earnings_forward(us_symbols, within_days=14)
        for item in earnings_forward:
            item["id"] = _earnings_forward_id(item)
            item["title"] = f"{item['symbol']} 下次财报：{item['earnings_date']}"
            item["url"] = ""

    # SA combined feed 抓取（包含候选股；默认关闭，见 ENABLE_SA_RSS）
    sa_news, sa_analysis = [], []
    sa_pull_tickers = list(dict.fromkeys(list(sa_tickers) + candidate_us))
    if ENABLE_SA_RSS and sa_pull_tickers:
        print("抓取 Seeking Alpha News / Analysis...")
        sa_news, sa_analysis = fetch_seeking_alpha(history, sa_pull_tickers, max_per_feed=20, delay_sec=1)
        for n in sa_news + sa_analysis:
            history.mark_reported(n["id"])

    # Finnhub 个股新闻（与 SA 并行的信源，复用 FINNHUB_API_KEY；未配置 key 则返回空）
    finnhub_news = []
    if sa_pull_tickers:
        print("抓取 Finnhub 个股新闻...")
        finnhub_news = fetch_finnhub_news(history, sa_pull_tickers, within_days=3, max_per_symbol=8)
        for n in finnhub_news:
            history.mark_reported(n["id"])

    form4_list = []
    if us_symbols:
        print("获取高管买卖...")
        form4_list = fetch_form4(us_symbols, history, within_days=15, max_per_symbol=10)
        for item in form4_list:
            history.mark_reported(item["id"])

    # 雪球大V长文（可选；需 ENABLE_XUEQIU=1 + Playwright + chrome_profile/ 已登录）
    xueqiu_posts = []
    if ENABLE_XUEQIU:
        kols = get_xueqiu_kols()
        if kols:
            print(f"抓雪球大V（{len(kols)} 人，days={XUEQIU_DAYS_BACK}）...")
            try:
                from invest.xueqiu import fetch_kol_posts
                raw = asyncio.run(fetch_kol_posts(kols, days_back=XUEQIU_DAYS_BACK))
            except Exception as e:
                print(f"⚠️ 雪球抓取失败（不影响其他数据源）: {e}")
                raw = []
            xueqiu_posts = [p for p in raw if not history.is_reported(p["id"])]
            for p in xueqiu_posts:
                history.mark_reported(p["id"])
            print(f"  → 抓到 {len(raw)} 条，去重后 {len(xueqiu_posts)} 条入库")

    # YouTube 财经 UP 主视频总结（默认开启）
    youtube_videos = []
    if ENABLE_YOUTUBE:
        creators = get_youtube_creators()
        if creators:
            print(f"抓 YouTube UP 主（{len(creators)} 人，days={YOUTUBE_DAYS_BACK}）...")
            try:
                from invest.youtube import fetch_youtube_summaries
                youtube_videos = asyncio.run(
                    fetch_youtube_summaries(creators, days_back=YOUTUBE_DAYS_BACK, history=history)
                )
                for v in youtube_videos:
                    history.mark_reported(v["id"])
            except Exception as e:
                print(f"⚠️ YouTube 抓取失败（不影响其他数据源）: {e}")
                youtube_videos = []
            print(f"  → 保留 {len(youtube_videos)} 条相关视频入库")

    # 机构 13F-HR 持仓变动（默认开启，季度才有 filing）
    institutional_changes = []
    if ENABLE_13F:
        filers = get_institutional_filers()
        if filers:
            print(f"检查机构 13F-HR ({len(filers)} 个 filer)...")
            try:
                from invest.sec_13f import fetch_13f_changes
                institutional_changes = fetch_13f_changes(filers)
            except Exception as e:
                print(f"⚠️ 13F 抓取失败（不影响其他数据源）: {e}")
                institutional_changes = []
            print(f"  → {len(institutional_changes)} 个变动事件入库")

    # 纳指 ETF 溢价
    print("获取纳斯达克ETF溢价...")
    ndq_etf_premiums = get_ndq_etf_premiums()
    if ndq_etf_premiums:
        print("纳斯达克ETF溢价提醒：" + "；".join(p["code"] + " " + p["premium_str"] for p in ndq_etf_premiums))

    # IC（中证500股指期货）年化贴水
    print("获取 IC 股指期货基差...")
    try:
        ic_basis = get_ic_basis()
    except Exception as e:
        print(f"⚠️ IC 基差抓取失败（不影响其他数据源）: {e}")
        ic_basis = None
    if ic_basis:
        print("IC 年化贴水：" + "；".join(
            f"{c['label']} {c['code']} {c['annual_pct']:+.2f}%" for c in ic_basis["contracts"]
        ))

    # —— 投资雷达评分 ——
    # 顺序：13F 机构 → 财报前瞻 → 高管 → YouTube → 雪球 → SA Analysis → SA News
    all_items_for_scoring = []
    for it in institutional_changes:
        all_items_for_scoring.append({**it, "_kind": "institutional_filing"})
    for it in earnings_forward:
        all_items_for_scoring.append({**it, "_kind": "earnings_forward"})
    for it in form4_list:
        all_items_for_scoring.append({**it, "_kind": "form4"})
    for it in youtube_videos:
        all_items_for_scoring.append({**it, "_kind": "youtube_video"})
    for it in xueqiu_posts:
        all_items_for_scoring.append({**it, "_kind": "xueqiu_post"})
    for it in sa_analysis:
        all_items_for_scoring.append({**it, "_kind": "sa_analysis"})
    for it in sa_news:
        all_items_for_scoring.append({**it, "_kind": "sa_news"})
    for it in finnhub_news:
        all_items_for_scoring.append({**it, "_kind": "finnhub_news"})

    scorer_result = score_items(all_items_for_scoring, holdings, candidates)
    scored_items = scorer_result.get("scored_items", [])

    # 把 grade/why/impact/trigger 合并回各列表
    earnings_forward = attach_grades(earnings_forward, scored_items)
    sa_news = attach_grades(sa_news, scored_items)
    sa_analysis = attach_grades(sa_analysis, scored_items)
    finnhub_news = attach_grades(finnhub_news, scored_items)
    form4_list = attach_grades(form4_list, scored_items)
    xueqiu_posts = attach_grades(xueqiu_posts, scored_items)
    youtube_videos = attach_grades(youtube_videos, scored_items)
    institutional_changes = attach_grades(institutional_changes, scored_items)

    # 展示顺序与名称：先 holdings，再 candidates
    earnings_symbols = [h["symbol"] for h in earnings_stocks]
    sa_only = [t for t in sa_tickers if t not in set(earnings_symbols)]
    cand_only = [s for s in candidate_us if s not in set(earnings_symbols) and s not in set(sa_only)]
    symbol_order = earnings_symbols + sa_only + cand_only

    symbol_to_name = {h["symbol"]: h["name"] for h in earnings_stocks}
    for h in holdings:
        symbol_to_name[h["symbol"]] = h["name"]
    for c in candidates:
        symbol_to_name.setdefault(c["symbol"], c["name"])

    html = build_html(
        earnings_forward,
        sa_news=sa_news,
        sa_analysis=sa_analysis,
        finnhub_news=finnhub_news,
        form4_list=form4_list,
        xueqiu_posts=xueqiu_posts,
        youtube_videos=youtube_videos,
        institutional_changes=institutional_changes,
        ndq_etf_premiums=ndq_etf_premiums,
        ic_basis=ic_basis,
        symbol_order=symbol_order,
        symbol_to_name=symbol_to_name,
        scorer_result=scorer_result,
        candidates=candidates,
    )
    today = datetime.date.today().strftime("%Y-%m-%d")
    subject = f"投资雷达日报 · {today}"

    s_count = sum(1 for s in scored_items if s.get("grade") == "S")
    a_count = sum(1 for s in scored_items if s.get("grade") == "A")
    hits = scorer_result.get("candidate_hits") or []
    summary = (
        f"S {s_count} 条 / A {a_count} 条 / 候选触发 {len(hits)} 条 / "
        f"财报前瞻 {len(earnings_forward)} / SA News {len(sa_news)} / "
        f"SA Analysis {len(sa_analysis)} / Finnhub News {len(finnhub_news)} / 高管 {len(form4_list)} / "
        f"雪球 {len(xueqiu_posts)} / YouTube {len(youtube_videos)} / "
        f"13F {len(institutional_changes)}"
    )
    if ic_basis and ic_basis["contracts"]:
        summary += f" / IC当月年化 {ic_basis['contracts'][0]['annual_pct']:+.2f}%"

    if RADAR_LOCAL_OUTPUT_DIR:
        # 本地模式：写 HTML 到磁盘作为备份，再发一份到 Gmail
        os.makedirs(RADAR_LOCAL_OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(RADAR_LOCAL_OUTPUT_DIR, f"radar_{today}.html")
        full_html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;"
            "max-width:780px;margin:24px auto;padding:0 16px;color:#222;line-height:1.5}</style>"
            f"</head><body>{html}</body></html>"
        )
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full_html)
        print(f"投资雷达日报已写入本地：{out_path} | {summary}")
        # 完整报告作为附件随邮件发送：正文超过 ~102KB 会被 Gmail 截断，附件不会
        success = asyncio.run(send_gmail(html, subject, attachment_path=out_path))
        if success:
            print(f"投资雷达日报已发送：{summary}")
        else:
            print("投资雷达日报发送失败（本地备份已保留），请检查 Gmail 配置。")
    else:
        # 默认模式（含 CI）：发 Gmail
        success = asyncio.run(send_gmail(html, subject))
        if success:
            print(f"投资雷达日报已发送：{summary}")
        else:
            print("投资雷达日报发送失败，请检查 Gmail 配置。")

    history.save_and_clean()


if __name__ == "__main__":
    main()
