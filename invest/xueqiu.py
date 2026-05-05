"""
雪球大V长文抓取器（独立模块）

设计：
- 用 Playwright 启动 Chromium，user_data_dir 持久化登录态（避免每次重新登录 + cookie 维护）
- 借鉴 yanglaiyang/Snowball-Follow-Skill 的 DOM 选择器（直接爬主页时间线）
- 借鉴 Navy-Patrick/dual-platform-crawler 的 stealth flags
- 不调 JSON API，避免 WAF JS 挑战 + cookie 鉴权问题（都由真浏览器处理）

可单独运行用于测试：
    第一次（手动登录）：
        python -m invest.xueqiu --login --limit 1
    日常（headless）：
        python -m invest.xueqiu --limit 3
        python -m invest.xueqiu --uids 1965894836,1835880756 --days 3 --min-chars 500

返回的每条 post 字典字段：
    {
        id            : sha256(post_id)，用于去重
        post_id       : 雪球内部帖子 id（来自 a.date-and-source 的 data-id）
        uid           : 大V uid
        url           : 帖子完整 URL
        title         : 帖子标题（如有）
        content       : 帖子正文预览（雪球时间线只显示前几百字，长文要点开）
        date_text     : 雪球显示的时间（"今天 12:34" / "04-26" / 绝对日期）
        published_at  : 解析后的 datetime（best effort，失败为 None）
        source        : "雪球 ({uid})"
        _kind         : "xueqiu_post"  ← scorer 区分用
    }
"""
import argparse
import asyncio
import datetime
import hashlib
import os
import random
import re
import sys
from typing import List, Optional

# 时间线 DOM 选择器
SEL_ARTICLE = "article.timeline__item"
SEL_DATE = "a.date-and-source"
# 多个 fallback，从精确到宽泛；JS 里逐个试，取最长非空 innerText
SEL_CONTENT_LIST = [
    "div.timeline__item__content div.content--description",
    "div.content--description",
    "div.timeline__item__content",
]
SEL_TITLE = "h3.timeline__item__title, a.status-title"  # 长文有 h3，普通状态无

# 文章详情页（点开"全文"后的页面）的正文选择器，多 fallback
SEL_ARTICLE_BODY_LIST = [
    "div.article__bd__detail",          # 正式文章
    "div.article__bd",
    "article.article-content",
    "div.status__content",              # 长动态
    "div.detail__content",
]

DEFAULT_PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chrome_profile"
)


def _hash_id(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _normalize_text(text: str) -> str:
    """折叠多余空白：雪球用 <br> 排版，innerText 抽出后每个标点都换行，难读且费 token。

    策略：
    - 段落级换行（\\n\\n+）保留为 \\n\\n
    - 单独的 \\n（多半是 <br>）→ 空格
    - 多空格 → 单空格
    """
    if not text:
        return ""
    # 先把 2+ 换行标记为段落分隔（用占位符避免被下一步合并）
    text = re.sub(r"\n{2,}", "PARA", text)
    # 单换行 → 空格（去掉视觉用 <br>）
    text = text.replace("\n", " ")
    # 多空白 → 单空格
    text = re.sub(r"[ \t　]+", " ", text)
    # 还原段落
    text = text.replace("PARA", "\n\n")
    return text.strip()


def _parse_xueqiu_date(text: str, now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    """雪球时间格式比较杂，best-effort 解析。

    常见格式：
        "今天 12:34"
        "昨天 09:15"
        "12-30 21:00"               ← 当年
        "2024-12-30 21:00"          ← 跨年
        "12分钟前" / "3小时前"
        "修改于2022-09-17 04:01· 来自雪球"  ← 修改/发表于前缀 + 来源后缀
    解析失败返回 None。
    """
    if not text:
        return None
    text = text.strip()
    now = now or datetime.datetime.now()

    # 剥前缀（修改于 / 发表于 / 发布于）
    text = re.sub(r"^(修改于|发表于|发布于)\s*", "", text)
    # 剥来源后缀（"· 来自XXX" / "· iPhone" / "  来自雪球"）
    text = re.sub(r"\s*[·•]\s*来自.*$", "", text)
    text = re.sub(r"\s*[·•]\s*\S+$", "", text) if "·" in text else text
    text = text.strip()

    # 相对时间
    m = re.match(r"^(\d+)\s*分钟前$", text)
    if m:
        return now - datetime.timedelta(minutes=int(m.group(1)))
    m = re.match(r"^(\d+)\s*小时前$", text)
    if m:
        return now - datetime.timedelta(hours=int(m.group(1)))

    # 今天 / 昨天
    m = re.match(r"^(今天|昨天)\s+(\d{1,2}):(\d{1,2})$", text)
    if m:
        day = now.date()
        if m.group(1) == "昨天":
            day -= datetime.timedelta(days=1)
        return datetime.datetime.combine(day, datetime.time(int(m.group(2)), int(m.group(3))))

    # YYYY-MM-DD HH:MM 或 YYYY-MM-DD
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            pass

    # MM-DD HH:MM（默认当年）
    m = re.match(r"^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})$", text)
    if m:
        try:
            return datetime.datetime(now.year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            return None

    return None


# 在浏览器 page context 里执行的 JS：抽取每个 article 的元数据
# content 用多个 fallback 选择器，取最长 innerText
_EXTRACT_JS = """
(args) => {
    const sel = args.selectors;
    const articles = document.querySelectorAll(sel.article);
    return Array.from(articles).map(a => {
        const dateEl = a.querySelector(sel.date);
        const titleEl = a.querySelector(sel.title);
        // 逐个 content selector 尝试，取最长非空 innerText
        let content = '';
        for (const cs of sel.contents) {
            const el = a.querySelector(cs);
            if (el) {
                const txt = (el.innerText || '').trim();
                if (txt.length > content.length) content = txt;
            }
        }
        // 检测置顶：常见标记 class 含 'pinned'/'sticky'，或 article 内有"置顶"文字标签
        const cls = a.className || '';
        let isPinned = /pinned|sticky|top/.test(cls);
        if (!isPinned) {
            // 查找"置顶"文字标签（通常在小标签里）
            const labelEls = a.querySelectorAll('span, em, i, div');
            for (const el of labelEls) {
                const t = (el.innerText || '').trim();
                if (t === '置顶' || t === '已置顶' || t === '顶') {
                    isPinned = true;
                    break;
                }
            }
        }
        return {
            post_id: dateEl ? (dateEl.getAttribute('data-id') || '') : '',
            url: dateEl ? dateEl.href : '',
            date_text: dateEl ? (dateEl.innerText || '').trim() : '',
            title: titleEl ? (titleEl.innerText || '').trim() : '',
            content: content,
            is_pinned: isPinned,
            class_name: cls,
        };
    });
}
"""


async def _fetch_full_article(page, post_url: str, debug: bool = False) -> Optional[str]:
    """点进帖子详情页，抓完整正文。失败返回 None。"""
    try:
        await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        if debug:
            print(f"    [debug] 详情页 goto 失败 ({post_url}): {e}", file=sys.stderr)
        return None
    title = await page.title()
    if any(x in title for x in ("登录", "404", "Just a moment", "验证")):
        if debug:
            print(f"    [debug] 详情页被拦 (title={title!r})", file=sys.stderr)
        return None
    # 等其中一个正文 selector 出现
    try:
        await page.wait_for_selector(",".join(SEL_ARTICLE_BODY_LIST), timeout=8000)
    except Exception:
        if debug:
            print(f"    [debug] 详情页正文 selector 都没匹配", file=sys.stderr)
        return None
    text = await page.evaluate(
        "(selectors) => {"
        "  for (const s of selectors) {"
        "    const el = document.querySelector(s);"
        "    if (el) {"
        "      const t = (el.innerText || '').trim();"
        "      if (t.length > 0) return t;"
        "    }"
        "  }"
        "  return '';"
        "}",
        SEL_ARTICLE_BODY_LIST,
    )
    return text or None


async def _fetch_one_kol(
    page, uid: str, name: str, days_back: int, min_chars: int,
    require_title: bool = True, fetch_full: bool = True,
    detail_delay: tuple = (1.0, 1.8), debug: bool = False,
) -> List[dict]:
    """抓单个大V最近 days_back 天、长度 >= min_chars 的帖子。

    fetch_full: True 时对有 h3 标题的"正式文章"再开详情页拿完整正文；
                长动态（无 h3）保留预览版（本身就是观点片段，不需要展开）
    """
    url = f"https://xueqiu.com/u/{uid}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"  ⚠️  [{name}] goto 失败: {e}", file=sys.stderr)
        return []

    # 反爬/拦截检测
    title = await page.title()
    if debug:
        print(f"  [debug] page.title = {title!r}", file=sys.stderr)
    if any(x in title for x in ("登录", "404", "Just a moment", "验证")):
        print(f"  ⚠️  [{name}] 被拦或不存在 (title={title!r})", file=sys.stderr)
        return []

    # 等 timeline 出现，否则可能是 WAF 挑战中
    try:
        await page.wait_for_selector(SEL_ARTICLE, timeout=10000)
    except Exception:
        if debug:
            # 看看页面里到底有什么
            counts = await page.evaluate(
                "() => ({"
                "  article_timeline_item: document.querySelectorAll('article.timeline__item').length,"
                "  any_article: document.querySelectorAll('article').length,"
                "  any_timeline: document.querySelectorAll('[class*=timeline]').length,"
                "  body_text_len: (document.body.innerText || '').length,"
                "  body_text_head: (document.body.innerText || '').slice(0, 200)"
                "})"
            )
            print(f"  [debug] selector 都没匹配；DOM 统计: {counts}", file=sys.stderr)
            html = await page.content()
            dump_path = f"/tmp/xueqiu_debug_{uid}.html"
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  [debug] HTML 已保存到 {dump_path}", file=sys.stderr)
        else:
            print(f"  ⚠️  [{name}] timeline 未加载（可能登录态失效或 WAF 拦截）", file=sys.stderr)
        return []

    raw_posts = await page.evaluate(_EXTRACT_JS, {
        "selectors": {
            "article": SEL_ARTICLE,
            "date": SEL_DATE,
            "contents": SEL_CONTENT_LIST,
            "title": SEL_TITLE,
        },
    })

    if debug:
        print(f"  [debug] selector 命中 {len(raw_posts)} 个 article", file=sys.stderr)
        # dump HTML 方便对照
        html = await page.content()
        dump_path = f"/tmp/xueqiu_debug_{uid}.html"
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [debug] HTML 已保存到 {dump_path}", file=sys.stderr)

    cutoff = datetime.datetime.now() - datetime.timedelta(days=days_back)
    drop_pinned = drop_no_id = drop_no_title = drop_short = drop_old = 0
    out = []
    debug_rows = []  # 逐条诊断
    for p in raw_posts:
        title = (p.get("title") or "").strip()
        content = _normalize_text(p.get("content") or "")
        date_text = p.get("date_text") or ""
        published = _parse_xueqiu_date(date_text)
        decision = "kept"

        if p.get("is_pinned"):
            decision = "drop_pinned"
            drop_pinned += 1
        elif not p.get("post_id"):
            decision = "drop_no_id"
            drop_no_id += 1
        elif require_title and not title:
            decision = "drop_no_h3"
            drop_no_title += 1
        elif len(content) < min_chars:
            decision = f"drop_short({len(content)})"
            drop_short += 1
        elif published is not None and published < cutoff:
            decision = f"drop_old({published.date()})"
            drop_old += 1

        debug_rows.append({
            "decision": decision,
            "len": len(content),
            "date": date_text[:35],
            "preview": (title or content)[:35].replace("\n", " "),
        })

        if decision != "kept":
            continue
        # 没 h3 标题的长动态：用正文前 30 字当展示标题，方便日报/日志可读
        display_title = title or (content[:30].strip() + "...")
        out.append({
            "id": _hash_id(p["post_id"]),
            "post_id": p["post_id"],
            "uid": uid,
            "url": p.get("url") or url,
            "title": display_title,
            "has_h3_title": bool(title),  # scorer 可参考此字段判断是正式文章还是长动态
            "preview": content,           # 时间线预览版（短）
            "content": content,           # 默认 = 预览；下面有正式文章会被完整正文覆盖
            "is_full_content": False,
            "date_text": p.get("date_text") or "",
            "published_at": published.isoformat() if published else None,
            "source": f"雪球 ({name})",
            "_kind": "xueqiu_post",
        })
    if debug:
        print(
            f"  [debug] 过滤统计: 置顶 丢 {drop_pinned}, 无 post_id 丢 {drop_no_id}, "
            f"无标题(短状态) 丢 {drop_no_title}, "
            f"内容太短(<{min_chars}) 丢 {drop_short}, "
            f"太旧(>{days_back}天) 丢 {drop_old}, 保留 {len(out)}",
            file=sys.stderr,
        )
        print(f"  [debug] 逐条诊断（共 {len(debug_rows)} 条）:", file=sys.stderr)
        for i, r in enumerate(debug_rows):
            print(
                f"    {i+1:>2}. [{r['decision']:<22}] {r['len']:>4}字  {r['date']:<35}  {r['preview']}",
                file=sys.stderr,
            )

    # —— 第二步：对有 h3 标题的正式文章追加抓完整正文 ——
    if fetch_full:
        targets = [p for p in out if p.get("has_h3_title")]
        if targets:
            print(f"  → 二次抓取 {len(targets)} 篇正式文章的完整正文...")
            for i, post in enumerate(targets):
                full = await _fetch_full_article(page, post["url"], debug=debug)
                if full:
                    full = _normalize_text(full)
                    if len(full) > len(post["preview"]):
                        post["content"] = full
                        post["is_full_content"] = True
                if debug:
                    full_len = len(full) if full else 0
                    print(
                        f"    [{i+1}/{len(targets)}] {post['title'][:30]} "
                        f"预览 {len(post['preview'])} → 全文 {full_len}",
                        file=sys.stderr,
                    )
                if i < len(targets) - 1:
                    await asyncio.sleep(random.uniform(*detail_delay))
    return out


async def fetch_kol_posts(
    kols: List[dict],
    days_back: int = 7,
    min_chars: int = 100,
    profile_dir: Optional[str] = None,
    headless: bool = True,
    delay_range: tuple = (2.0, 4.0),
    detail_delay: tuple = (1.0, 1.8),
    max_per_kol: int = 5,
    require_title: bool = False,
    fetch_full: bool = True,
    debug: bool = False,
) -> List[dict]:
    """主 API。返回所有大V最近的长帖。

    kols: [{"uid": "1965894836", "name": "..."}, ...]（来自 invest_config.get_xueqiu_kols()）
    days_back: 只取这些天内发的帖子（解析时间失败的不强行过滤）
    min_chars: 最少正文字数（粗略筛掉短状态）
    profile_dir: Chromium 用户数据目录（保留登录态）；None 则用 ./chrome_profile
    headless: 第一次登录时设 False，平时 True
    delay_range: 每个大V之间随机延时（秒），降低频率限制风险
    max_per_kol: 每个大V最多保留几条
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "⚠️  未安装 playwright。请执行：\n"
            "    pip install playwright && playwright install chromium",
            file=sys.stderr,
        )
        return []

    if not kols:
        return []
    profile_dir = profile_dir or os.getenv("XUEQIU_PROFILE_DIR") or DEFAULT_PROFILE_DIR
    os.makedirs(profile_dir, exist_ok=True)

    all_posts = []
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
            viewport={"width": 1280, "height": 900},
        )
        # 抹掉 navigator.webdriver（Navy-Patrick 借鉴）
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 第一次：headless=False 时给用户机会手动登录
        if not headless:
            print("👉 当前为登录模式（headless=False）。")
            print("   如尚未登录雪球，请在打开的浏览器里完成登录，然后回到本终端按回车继续。")
            await page.goto("https://xueqiu.com/", wait_until="domcontentloaded")
            try:
                input("登录完成后按回车继续抓取...")
            except EOFError:
                pass

        for i, kol in enumerate(kols):
            uid = kol["uid"]
            name = kol.get("name") or uid
            print(f"[{i+1}/{len(kols)}] {name} (uid={uid})")
            try:
                items = await _fetch_one_kol(
                    page, uid, name, days_back, min_chars,
                    require_title=require_title,
                    fetch_full=fetch_full,
                    detail_delay=detail_delay,
                    debug=debug,
                )
            except Exception as e:
                print(f"  ⚠️  抓取异常: {e}", file=sys.stderr)
                items = []
            if max_per_kol > 0:
                items = items[:max_per_kol]
            print(f"  → 取得 {len(items)} 条")
            all_posts.extend(items)
            # 频率控制（最后一个不用睡）
            if i < len(kols) - 1:
                await asyncio.sleep(random.uniform(*delay_range))

        await ctx.close()

    return all_posts


def _cli():
    ap = argparse.ArgumentParser(description="雪球大V长文抓取器（独立测试入口）")
    ap.add_argument("--uids", help="逗号分隔 uid；留空则从 watchlist.yaml 读")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 个大V（调试用）")
    ap.add_argument("--days", type=int, default=7, help="只取这些天内的帖子（默认 7 天）")
    ap.add_argument("--min-chars", type=int, default=100, help="最少预览字数（雪球时间线预览约 100-160 字）")
    ap.add_argument("--require-title", action="store_true", help="只要有 h3 标题的正式文章；默认同时收长动态")
    ap.add_argument("--no-full", action="store_true", help="不抓完整正文，只用时间线预览（快但稀疏）")
    ap.add_argument("--max-per-kol", type=int, default=5, help="每个大V最多保留几条")
    ap.add_argument("--login", action="store_true", help="非 headless 启动，给你手动登录的机会（首次用）")
    ap.add_argument("--profile-dir", help="Chromium user_data_dir，默认 ./chrome_profile")
    ap.add_argument("--out", help="把结果以 JSON 写入此文件；留空则打印摘要到 stdout")
    ap.add_argument("--debug", action="store_true", help="打印 DOM 抽取细节 + 保存页面 HTML 到 /tmp/")
    args = ap.parse_args()

    # 加载 .env（兼容 .env 里设了 XUEQIU_PROFILE_DIR 等）
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(project_root, ".env"), override=True)
    except ImportError:
        pass

    if args.uids:
        kols = [{"uid": u.strip(), "name": u.strip()} for u in args.uids.split(",") if u.strip()]
    else:
        from invest_config import get_xueqiu_kols
        kols = get_xueqiu_kols()

    if args.limit:
        kols = kols[: args.limit]

    if not kols:
        print("没有 uid 可抓。请用 --uids 或在 watchlist.yaml 配置 xueqiu_kols。", file=sys.stderr)
        sys.exit(1)

    print(
        f"准备抓 {len(kols)} 个大V  days={args.days}  min_chars={args.min_chars}  "
        f"login={args.login}  profile={args.profile_dir or DEFAULT_PROFILE_DIR}"
    )

    posts = asyncio.run(fetch_kol_posts(
        kols,
        days_back=args.days,
        min_chars=args.min_chars,
        profile_dir=args.profile_dir,
        headless=not args.login,
        max_per_kol=args.max_per_kol,
        require_title=args.require_title,
        fetch_full=not args.no_full,
        debug=args.debug,
    ))

    print(f"\n=== 共抓到 {len(posts)} 条 ===")
    if args.out:
        import json
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"已写入 {args.out}")
    else:
        for p in posts[:10]:
            tag = "全文" if p.get("is_full_content") else "预览"
            print(f"\n[{p['source']}] {p['date_text']}  [{tag} {len(p['content'])}字]")
            print(f"  标题: {p['title'][:60]}")
            print(f"  正文: {(p['content'] or '')[:200].replace(chr(10), ' ')}")
            print(f"  URL : {p['url']}")
        if len(posts) > 10:
            print(f"\n（仅显示前 10 条，完整结果用 --out file.json 导出）")


if __name__ == "__main__":
    _cli()
