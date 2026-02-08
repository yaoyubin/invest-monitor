"""
投资日报 MVP 入口：拉配置 → 抓财报新闻 + Seeking Alpha → 7 天去重 → 组报 → 发 Gmail
"""
import asyncio
import datetime
import sys
import os

# 确保项目根在 path 中
_project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _project_root)

from dotenv import load_dotenv
# 显式从项目根加载 .env，避免因工作目录不同而读不到
load_dotenv(os.path.join(_project_root, ".env"))

from invest_config import get_earnings_stocks, get_holdings, get_seeking_alpha_tickers
from invest.dedup import InvestHistoryManager
from invest.news import fetch_earnings_news
from invest.report import build_html
from invest.sa_rss import fetch_seeking_alpha
from tools.email_sender import send_gmail


def main():
    earnings_stocks = get_earnings_stocks()
    sa_tickers = get_seeking_alpha_tickers()

    if not earnings_stocks and not sa_tickers:
        print("未配置财报关注或 Seeking Alpha 标的，请编辑 invest_config.py 后重试。")
        return

    history = InvestHistoryManager()

    # 财报新闻（仅当有关注股票时）
    earnings_news = []
    if earnings_stocks:
        print("抓取财报相关新闻...")
        earnings_news = fetch_earnings_news(earnings_stocks, history, max_results_per=8, delay_sec=1)

    # Seeking Alpha（仅当有美股标的时）
    sa_news, sa_analysis = [], []
    if sa_tickers:
        print("抓取 Seeking Alpha News / Analysis...")
        sa_news, sa_analysis = fetch_seeking_alpha(history, sa_tickers, max_per_feed=20, delay_sec=1)

    # 标记本次报过的 id，避免 7 天内重复
    for n in earnings_news:
        history.mark_reported(n["id"])
    for n in sa_news + sa_analysis:
        history.mark_reported(n["id"])

    # 标的顺序与展示名：财报顺序优先，再补 SA 独有；展示名来自财报列表与持仓
    earnings_symbols = [h["symbol"] for h in earnings_stocks]
    sa_only = [t for t in sa_tickers if t not in set(earnings_symbols)]
    symbol_order = earnings_symbols + sa_only
    symbol_to_name = {h["symbol"]: h["name"] for h in earnings_stocks}
    for h in get_holdings():
        symbol_to_name[h["symbol"]] = h["name"]  # 覆盖或补充 SA 标的展示名

    # 组装日报并发送（按股票分组，仅展示当日有内容的股票）
    html = build_html(
        earnings_news,
        sa_news=sa_news,
        sa_analysis=sa_analysis,
        symbol_order=symbol_order,
        symbol_to_name=symbol_to_name,
    )
    today = datetime.date.today().strftime("%Y-%m-%d")
    subject = f"投资日报 · {today}"

    success = asyncio.run(send_gmail(html, subject))
    if success:
        print(f"投资日报已发送：财报 {len(earnings_news)} 条，SA News {len(sa_news)} 条，SA Analysis {len(sa_analysis)} 条")
    else:
        print("投资日报发送失败，请检查 Gmail 配置。")

    history.save_and_clean()


if __name__ == "__main__":
    main()
