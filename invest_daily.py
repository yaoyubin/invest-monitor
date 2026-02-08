"""
投资日报 MVP 入口：拉配置 → 抓持仓新闻 + 财报新闻 → 7 天去重 → 组报 → 发 Gmail
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

from invest_config import get_holdings, get_earnings_stocks
from invest.dedup import InvestHistoryManager
from invest.news import fetch_portfolio_news, fetch_earnings_news
from invest.report import build_html
from tools.email_sender import send_gmail


def main():
    holdings = get_holdings()
    earnings_stocks = get_earnings_stocks()

    if not holdings and not earnings_stocks:
        print("未配置持仓或关注股票，请编辑 invest_config.py 后重试。")
        return

    history = InvestHistoryManager()

    # 持仓新闻（仅当有持仓时）
    portfolio_news = []
    if holdings:
        print("抓取持仓相关新闻...")
        portfolio_news = fetch_portfolio_news(holdings, history, max_results_per=8, delay_sec=1)

    # 财报新闻（仅当有关注股票时）
    earnings_news = []
    if earnings_stocks:
        print("抓取财报相关新闻...")
        earnings_news = fetch_earnings_news(earnings_stocks, history, max_results_per=8, delay_sec=1)

    # 标记本次报过的 id，避免 7 天内重复
    for n in portfolio_news + earnings_news:
        history.mark_reported(n["id"])

    # 组装日报并发送
    html = build_html(portfolio_news, earnings_news)
    today = datetime.date.today().strftime("%Y-%m-%d")
    subject = f"投资日报 · {today}"

    success = asyncio.run(send_gmail(html, subject))
    if success:
        print(f"投资日报已发送：持仓新闻 {len(portfolio_news)} 条，财报 {len(earnings_news)} 条")
    else:
        print("投资日报发送失败，请检查 Gmail 配置。")

    history.save_and_clean()


if __name__ == "__main__":
    main()
