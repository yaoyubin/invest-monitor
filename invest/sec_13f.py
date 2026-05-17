"""
SEC 13F-HR 持仓变动监控（机构投资者季报）

监控大资金（巴菲特/桥水/Tiger 等）的季度持仓变动，发现"新建仓 / 大幅加仓 / 清仓"
等高 alpha 信号，特别关注科技/AI 标的的变化。

13F-HR 由管理 >$100M 的机构提交，季度结束后 45 天内 file（每年 2/15 5/15 8/15 11/15）。

独立模块，可单独测试：

    # 抓 Berkshire 最近 5 份 filings 列表
    python -m invest.sec_13f --filer 0001067983 --list 5

    # 抓最新 filing 的 INFORMATION TABLE，输出聚合后的持仓
    python -m invest.sec_13f --filer 0001067983 --latest

    # 检查所有配置的 filers 是否有新 filing；有就 diff 后 dump
    python -m invest.sec_13f --check --out /tmp/13f.json

设计要点：
- EDGAR 要求 User-Agent 含 email（SEC 政策）
- 同一 issuer 可能在 INFORMATION TABLE 出现多次（不同子账户），需按 CUSIP 聚合
- CUSIP → ticker 用内置字典覆盖主要科技/AI 股；其他持仓只显示 issuer 名
- snapshot 缓存到 sec_13f_snapshots/<cik>_<period>.json，跟前一份 diff
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request
import urllib.error
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

# macOS 系统 Python SSL fallback
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

# EDGAR 政策要求 User-Agent
USER_AGENT = os.getenv("SEC_USER_AGENT", "invest-monitor yaoyubin@gmail.com")

# 默认快照目录
DEFAULT_SNAPSHOT_DIR = os.path.join(_project_root, "sec_13f_snapshots")


# ===== CUSIP → ticker（覆盖主要科技/AI/半导体/电信股；其他持仓仍可显示，只是没 ticker） =====
# 来源：手工查 SEC EDGAR + 公开资料；只为日报展示，不影响交易
CUSIP_TO_TICKER = {
    # 大型科技
    "02079K305": ("GOOGL", "Alphabet Class A"),
    "02079K107": ("GOOG",  "Alphabet Class C"),
    "037833100": ("AAPL",  "Apple"),
    "594918104": ("MSFT",  "Microsoft"),
    "67066G104": ("NVDA",  "NVIDIA"),
    "30303M102": ("META",  "Meta Platforms"),
    "023135106": ("AMZN",  "Amazon"),
    "88160R101": ("TSLA",  "Tesla"),
    "64110L106": ("NFLX",  "Netflix"),
    "79466L302": ("CRM",   "Salesforce"),
    "00724F101": ("ADBE",  "Adobe"),
    # 半导体
    "007903107": ("AMD",   "Advanced Micro Devices"),
    "11135F101": ("AVGO",  "Broadcom"),
    "874039100": ("TSM",   "TSMC ADR"),
    "458140100": ("INTC",  "Intel"),
    "747525103": ("QCOM",  "Qualcomm"),
    "595112103": ("MU",    "Micron"),
    "038222105": ("AMAT",  "Applied Materials"),
    "512807108": ("LRCX",  "Lam Research"),
    "461202103": ("INTU",  "Intuit"),
    # 企业软件 / 云
    "68389X105": ("ORCL",  "Oracle"),
    "459200101": ("IBM",   "IBM"),
    "92826C839": ("V",     "Visa"),
    "57636Q104": ("MA",    "Mastercard"),
    # AI / 新势力
    "69608A108": ("PLTR",  "Palantir"),
    "98138H101": ("WDAY",  "Workday"),
    # 中概互联
    "01609W102": ("BABA",  "Alibaba ADR"),
    "47215P106": ("JD",    "JD.com ADR"),
    "722304102": ("PDD",   "PDD Holdings"),
    "056752108": ("BIDU",  "Baidu"),
    "59010R105": ("MELI",  "MercadoLibre"),
    "88032Q109": ("TCEHY", "Tencent ADR"),
    "090040106": ("BILI",  "Bilibili ADR"),
    "58506Q109": ("MPNGY", "Meituan ADR"),
    # 巴菲特金融/消费持仓
    "060505104": ("BAC",   "Bank of America"),
    "025816109": ("AXP",   "American Express"),
    "191216100": ("KO",    "Coca-Cola"),
    "166764100": ("CVX",   "Chevron"),
    "30231G102": ("XOM",   "Exxon Mobil"),
    "92343V104": ("VZ",    "Verizon"),
    "26825J101": ("DAL",   "Delta Air Lines"),
    "92556H206": ("VRSN",  "VeriSign"),
    "060505104": ("BAC",   "Bank of America"),
    "806857108": ("SCHW",  "Charles Schwab"),
}

# 哪些 ticker 算"科技/AI 相关"（影响 scorer 评分加权）
TECH_AI_TICKERS = {
    "GOOGL", "GOOG", "AAPL", "MSFT", "NVDA", "META", "AMZN", "TSLA", "NFLX",
    "CRM", "ADBE", "AMD", "AVGO", "TSM", "INTC", "QCOM", "MU", "AMAT", "LRCX",
    "ORCL", "IBM", "PLTR", "WDAY", "BABA", "JD", "PDD", "BIDU", "TCEHY", "BILI",
    "INTU", "VRSN",
}


# ===== HTTP =====

def _hash_id(*parts) -> str:
    raw = "::".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Host": urllib.request.urlparse(url).netloc,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_get_json(url: str, timeout: int = 20) -> dict:
    return json.loads(_http_get(url, timeout).decode("utf-8"))


# ===== Filings list =====

def fetch_filings_list(cik: str, max_n: int = 10) -> List[dict]:
    """从 SEC submissions API 拿最近 N 份 13F-HR。

    cik: 10 位数字字符串（不足前补 0），如 '0001067983'
    返回: [{accession, accession_raw, filing_date, primary_document, form}, ...]
            按时间倒序（最新在前），且只含 13F-HR / 13F-HR/A
    """
    cik = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    data = _http_get_json(url)
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    prims = recent.get("primaryDocument", [])
    reports = recent.get("reportDate", [])
    out = []
    for i, f in enumerate(forms):
        if f not in ("13F-HR", "13F-HR/A"):
            continue
        acc_raw = accs[i].replace("-", "")
        out.append({
            "accession": accs[i],
            "accession_raw": acc_raw,
            "filing_date": dates[i],
            "report_date": reports[i] if i < len(reports) else "",
            "form": f,
            "primary_document": prims[i],
            "filing_index_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR",
            "filing_dir_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_raw}/",
        })
        if len(out) >= max_n:
            break
    return out


# ===== INFORMATION TABLE =====

def _find_info_table_url(filing_dir_url: str) -> Optional[str]:
    """在 filing 目录里找 INFORMATION TABLE 文件（不是 primary_doc.xml，是另一个 XML）。"""
    index_url = filing_dir_url.rstrip("/") + "/index.json"
    try:
        data = _http_get_json(index_url)
    except Exception as e:
        print(f"  ⚠️ 拿不到 filing index ({index_url}): {e}", file=sys.stderr)
        return None
    for item in data.get("directory", {}).get("item", []):
        name = item.get("name", "")
        # 通常 primary_doc.xml 是 cover/summary，INFORMATION TABLE 是另一个 .xml
        if name.endswith(".xml") and name != "primary_doc.xml":
            return filing_dir_url.rstrip("/") + "/" + name
    return None


def parse_information_table(xml_bytes: bytes) -> List[dict]:
    """解析 13F INFORMATION TABLE，返回原始 infoTable 条目（未聚合）。

    返回每条：{name, cusip, value_usd, shares, share_type}
    注意：自 2023 Q2 起 SEC 把 13F value 字段从"千美元"改成"整美元"，这里直接当整美元用。
    """
    # 13F XML 用命名空间
    ns = {"t": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}
    root = ET.fromstring(xml_bytes)
    rows = []
    for it in root.findall("t:infoTable", ns):
        def text(xpath):
            el = it.find(xpath, ns)
            return el.text.strip() if (el is not None and el.text) else ""
        name = text("t:nameOfIssuer")
        cusip = text("t:cusip")
        value_kusd_str = text("t:value")
        shares_str = ""
        stype = ""
        sh = it.find("t:shrsOrPrnAmt", ns)
        if sh is not None:
            sh_amt = sh.find("t:sshPrnamt", ns)
            sh_type = sh.find("t:sshPrnamtType", ns)
            shares_str = sh_amt.text.strip() if (sh_amt is not None and sh_amt.text) else ""
            stype = sh_type.text.strip() if (sh_type is not None and sh_type.text) else ""
        try:
            value_usd = int(value_kusd_str)  # 整美元（2023 Q2 起的格式）
        except ValueError:
            value_usd = 0
        try:
            shares = int(shares_str)
        except ValueError:
            shares = 0
        rows.append({
            "name": name,
            "cusip": cusip,
            "value_usd": value_usd,
            "shares": shares,
            "share_type": stype,  # "SH"=股 / "PRN"=本金
        })
    return rows


def aggregate_by_cusip(rows: List[dict]) -> dict:
    """按 CUSIP 聚合：同一 issuer 在不同子账户里的持仓加总。

    返回 {cusip: {name, cusip, value_usd, shares, share_type}}
    """
    agg = {}
    for r in rows:
        c = r["cusip"]
        if not c:
            continue
        if c not in agg:
            agg[c] = {
                "name": r["name"],
                "cusip": c,
                "value_usd": 0,
                "shares": 0,
                "share_type": r["share_type"],
            }
        agg[c]["value_usd"] += r["value_usd"]
        agg[c]["shares"] += r["shares"]
    return agg


def fetch_and_parse_filing(filing: dict) -> Tuple[Optional[dict], Optional[str]]:
    """下载 filing 的 INFORMATION TABLE 并聚合。

    返回 (holdings_dict, info_table_url) 或 (None, None) 失败
    """
    url = _find_info_table_url(filing["filing_dir_url"])
    if not url:
        return None, None
    try:
        xml = _http_get(url)
    except Exception as e:
        print(f"  ⚠️ 抓 INFORMATION TABLE 失败 ({url}): {e}", file=sys.stderr)
        return None, None
    rows = parse_information_table(xml)
    agg = aggregate_by_cusip(rows)
    return agg, url


# ===== Diff =====

def diff_snapshots(old: Optional[dict], new: dict) -> List[dict]:
    """比对两期持仓快照，输出变动事件。

    old/new 是 {cusip: {name, value_usd, shares, ...}}（aggregate_by_cusip 输出）
    old=None 表示首次抓取，没有比对基准 → 返回空（不报"新建仓所有股"，会刷屏）

    返回 [{cusip, name, ticker?, change_type, shares_old, shares_new, shares_change_pct,
            value_old, value_new}, ...]
    change_type ∈ {'new', 'increase', 'decrease', 'exit'}
    """
    if old is None:
        return []
    changes = []
    all_cusips = set(old.keys()) | set(new.keys())
    for c in all_cusips:
        old_pos = old.get(c)
        new_pos = new.get(c)
        if not old_pos and new_pos:
            change_type = "new"
            shares_old, shares_new = 0, new_pos["shares"]
            value_old, value_new = 0, new_pos["value_usd"]
            pct = None
            name = new_pos["name"]
        elif old_pos and not new_pos:
            change_type = "exit"
            shares_old, shares_new = old_pos["shares"], 0
            value_old, value_new = old_pos["value_usd"], 0
            pct = -100.0
            name = old_pos["name"]
        else:
            shares_old, shares_new = old_pos["shares"], new_pos["shares"]
            value_old, value_new = old_pos["value_usd"], new_pos["value_usd"]
            name = new_pos["name"]
            if shares_new == shares_old:
                continue  # 无变化
            if shares_old == 0:
                pct = None
                change_type = "new"
            else:
                pct = (shares_new - shares_old) / shares_old * 100
                change_type = "increase" if pct > 0 else "decrease"
        tk_info = CUSIP_TO_TICKER.get(c)
        changes.append({
            "cusip": c,
            "name": name,
            "ticker": tk_info[0] if tk_info else None,
            "display_name": tk_info[1] if tk_info else name,
            "change_type": change_type,
            "shares_old": shares_old,
            "shares_new": shares_new,
            "shares_change_pct": pct,
            "value_old": value_old,
            "value_new": value_new,
        })
    # 排序：科技 AI > 大幅变动 > 大金额
    def sort_key(c):
        is_tech = (c.get("ticker") in TECH_AI_TICKERS)
        abs_pct = abs(c.get("shares_change_pct") or 999)
        return (
            0 if is_tech else 1,
            -abs_pct,
            -(c["value_new"] + c["value_old"]) / 2,
        )
    changes.sort(key=sort_key)
    return changes


# ===== Snapshot 缓存 =====

def snapshot_path(snapshot_dir: str, cik: str, period: str) -> str:
    os.makedirs(snapshot_dir, exist_ok=True)
    safe_period = period.replace("-", "")
    return os.path.join(snapshot_dir, f"{cik.zfill(10)}_{safe_period}.json")


def load_snapshot(snapshot_dir: str, cik: str, period: str) -> Optional[dict]:
    p = snapshot_path(snapshot_dir, cik, period)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("holdings")
    except Exception:
        return None


def save_snapshot(snapshot_dir: str, cik: str, period: str, holdings: dict,
                  filing: dict, info_table_url: str) -> None:
    p = snapshot_path(snapshot_dir, cik, period)
    payload = {
        "cik": cik,
        "period": period,
        "filing_date": filing.get("filing_date"),
        "accession": filing.get("accession"),
        "info_table_url": info_table_url,
        "saved_at": datetime.datetime.utcnow().isoformat() + "Z",
        "holdings": holdings,
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def find_previous_period(snapshot_dir: str, cik: str, current_period: str) -> Optional[str]:
    """在 snapshot_dir 里找该 CIK 早于 current_period 的最近一个快照 period。"""
    os.makedirs(snapshot_dir, exist_ok=True)
    cik_z = cik.zfill(10)
    safe_cur = current_period.replace("-", "")
    candidates = []
    for fn in os.listdir(snapshot_dir):
        if not fn.startswith(cik_z + "_") or not fn.endswith(".json"):
            continue
        p = fn[len(cik_z) + 1: -5]  # 去掉 .json
        if p < safe_cur:
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(reverse=True)
    p = candidates[0]
    # 转回 YYYY-MM-DD
    return f"{p[:4]}-{p[4:6]}-{p[6:]}"


# ===== 主入口：检查 + 输出 scorer items =====

def fetch_13f_changes(filers: List[dict], snapshot_dir: Optional[str] = None,
                      max_filings_per_filer: int = 5,
                      only_tech_ai: bool = False,
                      min_change_pct: float = 10.0) -> List[dict]:
    """主入口：对每个 filer，检查最新 13F 是否未见过；如果是新 filing，跟上一期 diff
    并产出 scorer 可消化的 items（_kind=institutional_filing）。

    filers: [{cik, name, ...}, ...]
    snapshot_dir: 快照存放目录（默认 ./sec_13f_snapshots/）
    only_tech_ai: True 时只输出 ticker 在 TECH_AI_TICKERS 里的变动
    min_change_pct: 加减仓百分比的最低门槛（小于此值不输出，noise 控制）；
                    new 和 exit 不受此约束（始终输出）

    返回 items list（按重要性排序）。
    """
    snapshot_dir = snapshot_dir or DEFAULT_SNAPSHOT_DIR
    items = []
    for filer in filers:
        cik = str(filer.get("cik") or "").zfill(10)
        name = filer.get("name") or f"CIK {cik}"
        if not cik or cik == "0000000000":
            print(f"⚠️  filer 缺 cik，跳过: {filer}", file=sys.stderr)
            continue

        try:
            filings = fetch_filings_list(cik, max_n=max_filings_per_filer)
        except Exception as e:
            print(f"⚠️  抓 {name} (CIK {cik}) filings 失败: {e}", file=sys.stderr)
            continue
        if not filings:
            print(f"  [{name}] 无 13F-HR filings", file=sys.stderr)
            continue

        # 最新一份
        latest = filings[0]
        period = latest["report_date"] or latest["filing_date"]
        print(f"[{name}] 最新 13F-HR: 报告期 {period}, file 于 {latest['filing_date']} ({latest['accession']})")

        # 已有这份快照？跳过
        existing = load_snapshot(snapshot_dir, cik, period)
        if existing is not None:
            print(f"  → 已有快照，跳过")
            continue

        # 拉 + 解析
        holdings, info_url = fetch_and_parse_filing(latest)
        if holdings is None:
            print(f"  ⚠️ 解析失败")
            continue
        print(f"  → 持仓 {len(holdings)} 个 issuer，总市值 ${sum(h['value_usd'] for h in holdings.values())/1e9:.1f}B")

        # 找上一期做 diff
        prev_period = find_previous_period(snapshot_dir, cik, period)
        prev_holdings = load_snapshot(snapshot_dir, cik, prev_period) if prev_period else None

        # 保存当前快照
        save_snapshot(snapshot_dir, cik, period, holdings, latest, info_url or "")

        if prev_holdings is None:
            print(f"  → 无前期快照，仅记录本期（diff 留待下次）")
            continue
        print(f"  → 跟前期 {prev_period} 比对...")

        changes = diff_snapshots(prev_holdings, holdings)

        # 过滤
        filtered = []
        for c in changes:
            if only_tech_ai and c.get("ticker") not in TECH_AI_TICKERS:
                continue
            pct = c.get("shares_change_pct")
            if c["change_type"] in ("increase", "decrease") and pct is not None and abs(pct) < min_change_pct:
                continue
            filtered.append(c)
        print(f"  → {len(changes)} 个变动，过滤后 {len(filtered)} 个上报")

        # 转 scorer item
        for c in filtered:
            items.append(_change_to_item(c, name, cik, period, latest))
    return items


def _change_to_item(c: dict, filer_name: str, cik: str, period: str, filing: dict) -> dict:
    """把一个 diff 变动转成 scorer 可消化的 item。"""
    ticker = c.get("ticker")
    display_name = c.get("display_name") or c.get("name")
    ct = c["change_type"]
    pct = c.get("shares_change_pct")

    # 生成可读 title
    sym_str = f"{ticker} ({display_name})" if ticker else display_name
    if ct == "new":
        title = f"{filer_name} 新建仓 {sym_str}：${c['value_new']/1e9:.2f}B"
    elif ct == "exit":
        title = f"{filer_name} 清仓 {sym_str}（前期 ${c['value_old']/1e9:.2f}B）"
    elif ct == "increase":
        title = (f"{filer_name} 加仓 {sym_str} +{pct:.0f}%："
                 f"${c['value_old']/1e9:.2f}B → ${c['value_new']/1e9:.2f}B")
    else:  # decrease
        title = (f"{filer_name} 减仓 {sym_str} {pct:.0f}%："
                 f"${c['value_old']/1e9:.2f}B → ${c['value_new']/1e9:.2f}B")

    snippet_parts = [f"机构：{filer_name}（CIK {cik}）", f"报告期：{period}", f"动作：{ct}"]
    if pct is not None:
        snippet_parts.append(f"百分比变化：{pct:+.1f}%")
    snippet_parts.append(f"持仓价值：${c['value_old']/1e6:.0f}M → ${c['value_new']/1e6:.0f}M")
    snippet_parts.append(f"股数：{c['shares_old']:,} → {c['shares_new']:,}")
    if ticker in TECH_AI_TICKERS:
        snippet_parts.append("🤖 科技/AI 标的")
    snippet = " | ".join(snippet_parts)

    is_tech_ai = ticker in TECH_AI_TICKERS

    return {
        "id": _hash_id(cik, period, c["cusip"], ct),
        "symbol": ticker,
        "title": title,
        "snippet": snippet,
        "url": filing.get("filing_dir_url", ""),
        "filer": filer_name,
        "filer_cik": cik,
        "period": period,
        "filing_date": filing.get("filing_date"),
        "accession": filing.get("accession"),
        "cusip": c["cusip"],
        "issuer_name": c.get("name"),
        "change_type": ct,
        "shares_old": c["shares_old"],
        "shares_new": c["shares_new"],
        "shares_change_pct": pct,
        "value_old_usd": c["value_old"],
        "value_new_usd": c["value_new"],
        "is_tech_ai": is_tech_ai,
        "source": f"SEC 13F ({filer_name})",
        "_kind": "institutional_filing",
    }


# ===== CLI =====

def _cli():
    ap = argparse.ArgumentParser(description="SEC 13F-HR 监控（机构投资者季报持仓变动）")
    ap.add_argument("--filer", help="单个 CIK（如 0001067983 Berkshire），用于 --list/--latest")
    ap.add_argument("--list", type=int, help="列出该 CIK 最近 N 份 13F-HR filings")
    ap.add_argument("--latest", action="store_true", help="抓该 CIK 最新 filing 的 INFORMATION TABLE 聚合后输出")
    ap.add_argument("--check", action="store_true", help="按 watchlist 配置检查所有 filers，diff 后输出 items")
    ap.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR, help="快照目录")
    ap.add_argument("--only-tech-ai", action="store_true", help="只输出 ticker 在 TECH_AI_TICKERS 里的变动")
    ap.add_argument("--min-change-pct", type=float, default=10.0, help="加减仓最低百分比门槛（默认 10%）")
    ap.add_argument("--out", help="把 --check 的结果以 JSON 写入此文件")
    args = ap.parse_args()

    if args.list and args.filer:
        filings = fetch_filings_list(args.filer, max_n=args.list)
        for f in filings:
            print(f"  {f['filing_date']} | {f['form']:<10} | 报告期 {f['report_date']} | {f['accession']}")
        return

    if args.latest and args.filer:
        filings = fetch_filings_list(args.filer, max_n=1)
        if not filings:
            print("没找到 13F-HR")
            return
        holdings, info_url = fetch_and_parse_filing(filings[0])
        if holdings is None:
            print("解析失败")
            return
        print(f"INFORMATION TABLE URL: {info_url}")
        print(f"持仓数：{len(holdings)}，总市值 ${sum(h['value_usd'] for h in holdings.values())/1e9:.2f}B\n")
        # 按市值降序输出前 20
        sorted_h = sorted(holdings.values(), key=lambda x: -x["value_usd"])
        print(f"{'排名':<4} {'CUSIP':<11} {'Ticker':<7} {'Issuer':<35} {'市值 ($M)':>12}  股数")
        for i, h in enumerate(sorted_h[:20], 1):
            tk = CUSIP_TO_TICKER.get(h["cusip"])
            t = tk[0] if tk else ""
            print(f"  {i:<3} {h['cusip']:<11} {t:<7} {h['name'][:33]:<35} {h['value_usd']/1e6:>10.0f}M  {h['shares']:>12,}")
        return

    if args.check:
        try:
            from invest_config import get_institutional_filers
            filers = get_institutional_filers()
        except ImportError:
            print("⚠️  invest_config.get_institutional_filers 不存在", file=sys.stderr)
            sys.exit(1)
        if not filers:
            print("watchlist.yaml 没配 institutional_filers", file=sys.stderr)
            sys.exit(1)
        items = fetch_13f_changes(filers, snapshot_dir=args.snapshot_dir,
                                  only_tech_ai=args.only_tech_ai,
                                  min_change_pct=args.min_change_pct)
        print(f"\n=== 共 {len(items)} 个变动事件 ===")
        for it in items[:20]:
            tag = "🤖" if it.get("is_tech_ai") else "  "
            print(f"{tag} {it['title']}")
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            print(f"\n已写入 {args.out}")
        return

    ap.print_help()


if __name__ == "__main__":
    _cli()
