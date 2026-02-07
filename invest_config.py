"""
投资日报 MVP 配置
- 持仓列表：用于「持仓新闻」搜索（股票 + 加密货币）
- 关注股票（财报）：用于「财报」搜索；可与持仓中的股票部分一致或单独列出
"""

# 持仓列表：每项 symbol（代码/symbol）、market（cn/us/crypto）、可选 name（展示用）
HOLDINGS = [
    # A 股示例
    # {"symbol": "600519", "market": "cn", "name": "贵州茅台"},
    # 美股示例
    # {"symbol": "AAPL", "market": "us", "name": "Apple"},
    {"symbol": "TSM", "market": "us", "name": "台積電"},
    # 加密货币示例
    {"symbol": "BTC", "market": "crypto", "name": "比特币"},
]

# 关注股票（用于财报搜索）。若与持仓一致，填 None 表示自动使用持仓中的股票部分
# 若希望财报范围更大，可在此单独列出，格式同持仓（仅 cn/us）
EARNINGS_WATCH = None  # 例如: [{"symbol": "600519", "market": "cn", "name": "贵州茅台"}, ...]


def get_holdings():
    """返回持仓列表，每项至少包含 symbol, market, name（缺省用 symbol）"""
    return [
        {"symbol": item["symbol"], "market": item["market"], "name": item.get("name") or item["symbol"]}
        for item in HOLDINGS
    ]


def get_earnings_stocks():
    """返回用于财报搜索的股票列表。若 EARNINGS_WATCH 为 None，则用持仓中的股票（cn/us）"""
    if EARNINGS_WATCH is not None:
        return [
            {"symbol": item["symbol"], "market": item["market"], "name": item.get("name") or item["symbol"]}
            for item in EARNINGS_WATCH
        ]
    holdings = get_holdings()
    return [h for h in holdings if h["market"] in ("cn", "us")]
