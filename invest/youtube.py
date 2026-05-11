"""
YouTube 财经 UP 主视频抓取 + 字幕 + LLM 总结。

独立模块，类似 invest/xueqiu.py。可独立运行测试：

    # 一次性：把 @handle 解析成 channel_id 并写回 watchlist.yaml
    python -m invest.youtube --resolve

    # 抓最近 2 天的视频做总结
    python -m invest.youtube --days 2 --out /tmp/youtube.json

    # 调试单个 channel
    python -m invest.youtube --handles bellafinance --days 7 --debug

设计要点：
- RSS 取最近视频列表（无需 API key）
- youtube-transcript-api 拿字幕（中文优先，英文 fallback）
- 标题关键词 + LLM 双重过滤（不相关的不浪费 token 总结）
- 一次 LLM 调用同时输出 is_relevant + reason + summary(<500字)
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request
import urllib.error
from typing import List, Optional, Tuple

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

# 优先加载项目 .env（override=True 防止 shell 残留的空值覆盖）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_project_root, ".env"), override=True)
except ImportError:
    pass

# macOS 系统 Python 的 SSL 证书路径修复（urllib 默认证书库可能缺）
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass


# ===== 配置 =====

# 标题预过滤的关键词（命中任一即进入 LLM 判断；都不命中跳过省字幕调用）
TITLE_KEYWORDS = [
    # 财经通用
    "财经", "金融", "股票", "股市", "基金", "外汇", "经济", "通胀", "利率", "FED", "美联储",
    "财报", "业绩", "盈利", "营收", "估值", "PE", "市盈率", "分红", "回购",
    "买入", "卖出", "持仓", "做多", "做空",
    # 美股代号常见
    "美股", "A股", "港股", "中概", "纳斯达克", "标普", "道琼斯",
    "苹果", "特斯拉", "英伟达", "微软", "谷歌", "Meta", "亚马逊", "AMD", "Intel", "TSM",
    "腾讯", "阿里", "美团", "B站", "拼多多", "字节", "京东",
    # AI 关键词
    "AI", "ai", "人工智能", "大模型", "GPT", "ChatGPT", "Claude", "Gemini",
    "芯片", "半导体", "GPU", "算力", "数据中心",
    # 宏观
    "美国", "中国", "贸易", "关税", "中美", "央行",
]

# 字幕优先语言
TRANSCRIPT_LANGS = ["zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh", "en", "en-US"]

# LLM provider 复用 scorer 的配置
SUMMARIZER_PROVIDER = os.getenv("SCORER_LLM_PROVIDER", "anthropic")
SUMMARIZER_MODEL = os.getenv("SCORER_LLM_MODEL")

# 单视频字幕送 LLM 的最大字符数（中文约等于 token 数）
MAX_TRANSCRIPT_CHARS = 8000

# 视频时长上限（秒）—— 超过的算长视频/直播录像，可能要更激进截断
LONG_VIDEO_SEC = 1800  # 30 分钟


# ===== 工具函数 =====

def _hash_id(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _http_get(url: str, timeout: int = 15) -> str:
    """简单 HTTP GET，带常见 UA。"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ===== Channel ID 解析 =====

def resolve_channel_id(handle: str) -> Optional[str]:
    """从 @handle 解析出 UCxxx 形式的 channel_id。

    抓 https://www.youtube.com/@handle/about 页面，从内嵌 JSON 里找
    "channelId":"UC..."。失败返回 None。
    """
    handle = handle.lstrip("@")
    for path in (f"@{handle}/about", f"@{handle}"):
        url = f"https://www.youtube.com/{path}"
        try:
            html = _http_get(url)
        except Exception as e:
            print(f"  ⚠️  抓 {url} 失败: {e}", file=sys.stderr)
            continue
        m = re.search(r'"channelId"\s*:\s*"(UC[\w-]{20,})"', html)
        if m:
            return m.group(1)
        m = re.search(r'"externalChannelId"\s*:\s*"(UC[\w-]{20,})"', html)
        if m:
            return m.group(1)
    return None


# ===== RSS 取最近视频 =====

def fetch_recent_videos(channel_id: str, days_back: int = 2) -> List[dict]:
    """从 YouTube RSS 取 channel 最近的视频条目。

    返回 [{video_id, url, title, published_at(datetime), description?}, ...]
    按 published_at 倒序，过滤掉 days_back 之外的。
    """
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        xml = _http_get(rss_url)
    except Exception as e:
        print(f"  ⚠️  抓 RSS 失败 ({channel_id}): {e}", file=sys.stderr)
        return []

    # 简单正则解析（避免 feedparser 在 youtube ns 上偶发问题）
    entries = re.findall(
        r"<entry>(.*?)</entry>",
        xml,
        flags=re.DOTALL,
    )
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)
    out = []
    for e in entries:
        vid_m = re.search(r"<yt:videoId>([\w-]+)</yt:videoId>", e)
        title_m = re.search(r"<title>(.*?)</title>", e, flags=re.DOTALL)
        pub_m = re.search(r"<published>([^<]+)</published>", e)
        desc_m = re.search(r"<media:description>(.*?)</media:description>", e, flags=re.DOTALL)
        if not (vid_m and title_m and pub_m):
            continue
        video_id = vid_m.group(1)
        try:
            published = datetime.datetime.fromisoformat(pub_m.group(1).replace("Z", "+00:00"))
        except Exception:
            continue
        if published < cutoff:
            continue
        out.append({
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": title_m.group(1).strip(),
            "published_at": published,
            "description": (desc_m.group(1).strip() if desc_m else ""),
        })
    return out


# ===== 视频时长 =====

def get_duration_seconds(video_id: str) -> Optional[int]:
    """抓视频播放页 grep "lengthSeconds":"NNN"。失败返回 None。"""
    try:
        html = _http_get(f"https://www.youtube.com/watch?v={video_id}")
    except Exception:
        return None
    m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', html)
    if m:
        return int(m.group(1))
    return None


# ===== 字幕 =====

def get_transcript(video_id: str) -> Optional[str]:
    """拿字幕全文（按 TRANSCRIPT_LANGS 优先级），合并为单字符串。失败返回 None。"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            TranscriptsDisabled, NoTranscriptFound, VideoUnavailable,
        )
    except ImportError:
        print("⚠️ 未安装 youtube-transcript-api，请: pip install youtube-transcript-api", file=sys.stderr)
        return None

    try:
        # 新版 API：实例化后再 fetch
        api = YouTubeTranscriptApi()
        snippets = api.fetch(video_id, languages=TRANSCRIPT_LANGS)
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return None
    except Exception as e:
        # 兼容旧版 / 其他错误，再尝试 class method 形式
        try:
            snippets = YouTubeTranscriptApi.get_transcript(video_id, languages=TRANSCRIPT_LANGS)
        except Exception:
            print(f"  ⚠️  字幕抓取失败 ({video_id}): {e}", file=sys.stderr)
            return None

    # snippets 是 FetchedTranscriptSnippet 列表（新版）或 dict 列表（旧版）
    parts = []
    for s in snippets:
        text = getattr(s, "text", None) if not isinstance(s, dict) else s.get("text", "")
        if text:
            parts.append(text)
    return " ".join(parts) if parts else None


# ===== LLM 总结 =====

SUMMARIZE_SYSTEM_PROMPT = """你是财经视频内容分析助手。任务：判断给定视频是否与"财经/投资/AI"相关，如相关则用中文总结其核心观点（≤500 字）。

【相关性判断标准】
- 财经：股票/基金/外汇/宏观经济/通胀/利率/财报/行业分析/公司点评/投资策略
- AI：人工智能技术进展/AI 公司动态/AI 对行业/经济的影响
- 不算相关：纯娱乐/生活/旅游/带货/电子产品评测中没有股票相关分析的

【总结要求】（只在相关时输出）
- 用中文，不超过 500 字
- 抓 UP 主的核心观点和论据，不要复述时间线
- 涉及具体股票/标的时显式列出（如"看多 AMD"）
- 如有数据/引用，保留关键数字
- 不要写"UP 主认为..."这种废话引导词，直接陈述观点

【输出格式】（严格 JSON，不要 markdown code fence）
{
  "is_relevant": true | false,
  "reason": "<判断理由，30 字内>",
  "summary": "<相关时填，≤500 字；不相关填空字符串>",
  "stocks_mentioned": ["AMD", "TSLA", ...]
}

stocks_mentioned 列表用美股 ticker 或港股代码（中文公司名可对应转换，例：腾讯→TCEHY 或 0700.HK），不相关时填空数组 []。
"""


def summarize_video(title: str, channel_name: str, duration_seconds: Optional[int],
                    transcript: str, debug: bool = False) -> Optional[dict]:
    """LLM 一次调用同时判定相关性 + 生成总结。失败返回 None。"""
    if not transcript:
        return None

    truncated = transcript[:MAX_TRANSCRIPT_CHARS]
    truncated_note = ""
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        truncated_note = f"\n（字幕被截断，原始 {len(transcript)} 字，已截 {MAX_TRANSCRIPT_CHARS} 字）"

    dur_str = f"{duration_seconds // 60}分{duration_seconds % 60}秒" if duration_seconds else "未知"

    user_prompt = f"""视频标题：{title}
频道：{channel_name}
时长：{dur_str}
字幕：{truncated_note}
\"\"\"
{truncated}
\"\"\"

请按系统提示判断相关性并输出 JSON。"""

    full_prompt = SUMMARIZE_SYSTEM_PROMPT + "\n\n" + user_prompt

    try:
        from tools.llm_api import create_llm_client, query_llm
        client = create_llm_client(SUMMARIZER_PROVIDER)
        response = query_llm(full_prompt, client=client, model=SUMMARIZER_MODEL, provider=SUMMARIZER_PROVIDER)
    except Exception as e:
        print(f"  ⚠️  LLM 总结失败 ({title[:30]}): {e}", file=sys.stderr)
        return None

    if not response:
        return None

    # 解析 JSON（容错：去掉可能的 markdown 包裹）
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?|\n?```$", "", text, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # 找 JSON 对象起止
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            if debug:
                print(f"  [debug] LLM 输出无法解析为 JSON，前 200 字: {text[:200]}", file=sys.stderr)
            return None
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return None
    return parsed


# ===== 关键词预过滤 =====

def title_or_desc_has_keyword(title: str, description: str = "") -> bool:
    blob = f"{title} {description}".lower()
    for k in TITLE_KEYWORDS:
        if k.lower() in blob:
            return True
    return False


# ===== 主流程 =====

async def fetch_youtube_summaries(
    creators: List[dict],
    days_back: int = 2,
    history=None,
    debug: bool = False,
) -> List[dict]:
    """主入口：抓所有 creator 最近 days_back 天的相关视频并总结。

    creators: [{handle, name, channel_id?}, ...]
    history:  InvestHistoryManager（去重，可选）
    返回 list of dict（已过滤"不相关"和"无字幕"的）：
    [{
        "id": sha256(video_id),
        "video_id": "...",
        "url": "https://youtube.com/watch?v=...",
        "title": "...",
        "channel": "贝乐财经",
        "duration_seconds": 600,
        "published_at": iso str,
        "summary": "...",
        "is_relevant_reason": "...",
        "stocks_mentioned": ["AMD"],
        "_kind": "youtube_video",
    }]
    """
    out = []
    for c in creators:
        handle = c.get("handle") or ""
        name = c.get("name") or handle
        channel_id = c.get("channel_id")
        if not channel_id and handle:
            channel_id = resolve_channel_id(handle)
            if not channel_id:
                print(f"⚠️  无法解析 {name} (@{handle}) 的 channel_id，跳过", file=sys.stderr)
                continue
        elif not channel_id:
            print(f"⚠️  {name} 缺 handle 和 channel_id，跳过", file=sys.stderr)
            continue

        print(f"[{name}] 拉取 RSS (channel_id={channel_id})...")
        videos = fetch_recent_videos(channel_id, days_back=days_back)
        print(f"  → 最近 {days_back} 天共 {len(videos)} 个视频")

        kept_for_channel = 0
        for v in videos:
            vid = v["video_id"]
            # dedup
            if history is not None and history.is_reported(_hash_id(vid)):
                if debug:
                    print(f"  [debug] 跳过已报过的 {vid}: {v['title'][:30]}", file=sys.stderr)
                continue

            # 1. 标题/描述关键词预过滤
            kw_hit = title_or_desc_has_keyword(v["title"], v.get("description", ""))
            if not kw_hit:
                if debug:
                    print(f"  [debug] 关键词未命中：{v['title'][:40]}", file=sys.stderr)
                continue

            # 2. 字幕
            transcript = get_transcript(vid)
            if not transcript:
                if debug:
                    print(f"  [debug] 无字幕：{v['title'][:40]}", file=sys.stderr)
                continue

            # 3. 时长
            duration = get_duration_seconds(vid)

            # 4. LLM 判定相关性 + 总结
            result = summarize_video(v["title"], name, duration, transcript, debug=debug)
            if not result:
                continue
            if not result.get("is_relevant"):
                if debug:
                    print(f"  [debug] LLM 判定不相关 ({result.get('reason','')}): {v['title'][:30]}", file=sys.stderr)
                continue

            summary = (result.get("summary") or "").strip()
            if not summary:
                continue

            item = {
                "id": _hash_id(vid),
                "video_id": vid,
                "url": v["url"],
                "title": v["title"],
                "channel": name,
                "duration_seconds": duration or 0,
                "published_at": v["published_at"].isoformat() if v.get("published_at") else None,
                "summary": summary,
                "is_relevant_reason": result.get("reason", ""),
                "stocks_mentioned": result.get("stocks_mentioned") or [],
                "source": f"YouTube ({name})",
                "_kind": "youtube_video",
            }
            out.append(item)
            kept_for_channel += 1
            print(f"  ✅ [{name}] {v['title'][:50]}  ({len(summary)}字总结)")

        print(f"  [{name}] 保留 {kept_for_channel} 个视频")

    return out


# ===== 改写 watchlist.yaml 里 channel_id（一次性辅助） =====

def _rewrite_youtube_channel_ids(watchlist_path: str, creators_with_ids: List[dict]) -> bool:
    """改写 youtube_creators 节，把抓到的 channel_id 写回。"""
    with open(watchlist_path, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^(youtube_creators:\s*\n)(.*?)(?=^\w|\Z)", text, re.MULTILINE | re.DOTALL)
    if not m:
        return False
    head = m.group(1)
    lines = []
    for c in creators_with_ids:
        handle = c.get("handle", "")
        name = c.get("name", handle)
        cid = c.get("channel_id", "")
        if cid:
            lines.append(f"  - {{handle: {handle}, name: '{name}', channel_id: {cid}}}")
        else:
            lines.append(f"  - {{handle: {handle}, name: '{name}'}}")
    new_section = "\n".join(lines) + "\n\n"
    new_text = text[: m.start()] + head + new_section + text[m.end():]
    if new_text == text:
        return False
    with open(watchlist_path + ".bak", "w", encoding="utf-8") as f:
        f.write(text)
    with open(watchlist_path, "w", encoding="utf-8") as f:
        f.write(new_text)
    return True


# ===== CLI =====

def _cli():
    ap = argparse.ArgumentParser(description="YouTube 财经 UP 主视频抓取与总结")
    ap.add_argument("--handles", help="逗号分隔的 handle 列表，覆盖 watchlist；如 'bellafinance,hackbearterry'")
    ap.add_argument("--days", type=int, default=2, help="抓最近 N 天的视频（默认 2）")
    ap.add_argument("--out", help="写 JSON 到该文件；不指定则打印摘要")
    ap.add_argument("--debug", action="store_true", help="打印过滤/解析细节")
    ap.add_argument("--resolve", action="store_true",
                    help="只把 @handle 解析为 channel_id 并写回 watchlist.yaml，不抓视频")
    args = ap.parse_args()

    if args.handles:
        creators = [{"handle": h.strip(), "name": h.strip()} for h in args.handles.split(",") if h.strip()]
    else:
        try:
            from invest_config import get_youtube_creators
            creators = get_youtube_creators()
        except ImportError:
            print("⚠️ invest_config.get_youtube_creators 不存在，请先在 watchlist.yaml 配置 youtube_creators", file=sys.stderr)
            sys.exit(1)
    if not creators:
        print("没有 creator 可处理。请配置 watchlist.yaml 的 youtube_creators 或用 --handles", file=sys.stderr)
        sys.exit(1)

    if args.resolve:
        print(f"准备解析 {len(creators)} 个 handle 的 channel_id...")
        resolved = []
        for c in creators:
            handle = c.get("handle") or c.get("name")
            cid = c.get("channel_id") or resolve_channel_id(handle)
            print(f"  {handle:<25} → {cid or '✗ 失败'}")
            resolved.append({"handle": handle, "name": c.get("name", handle), "channel_id": cid or ""})
        watchlist_path = os.path.join(_project_root, "watchlist.yaml")
        if _rewrite_youtube_channel_ids(watchlist_path, resolved):
            print(f"已改写 {watchlist_path}（备份 watchlist.yaml.bak）")
        else:
            print("未改写（可能 watchlist.yaml 里没有 youtube_creators 节，请先手动添加）")
        return

    print(f"开始抓 {len(creators)} 个 UP 主，days={args.days}...")
    items = asyncio.run(fetch_youtube_summaries(creators, days_back=args.days, debug=args.debug))
    print(f"\n=== 共保留 {len(items)} 个相关视频 ===")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"已写入 {args.out}")
    else:
        for it in items:
            print(f"\n[{it['channel']}] {it['title']}")
            print(f"  URL: {it['url']}")
            print(f"  时长: {it['duration_seconds']}秒  涉及标的: {it.get('stocks_mentioned', [])}")
            print(f"  总结 ({len(it['summary'])}字): {it['summary'][:300]}{'...' if len(it['summary']) > 300 else ''}")


if __name__ == "__main__":
    _cli()
