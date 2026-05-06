"""
投资雷达 LLM 评分器

输入：当日聚合的原始信息（SA News / Analysis / 财报前瞻 / 高管买卖）+ watchlist 上下文（holdings 的 thesis/red_flags、candidates 的 trigger）
输出：
- scored_items: 每条信息附带 grade(S/A/B/C)、why_important、thesis_impact、trigger_hit
- thesis_deltas: 每个持仓今日 delta（-2/-1/0/+1/+2）+ 简短理由
- candidate_hits: 候选标的命中的 revisit_trigger（如有）

设计要点：
- 单次 LLM 调用，避免逐条调用增加成本/延迟
- LLM 不可用或 API key 未配置时优雅降级（返回原始信息，全部标 B）
- 强制返回 JSON，解析失败时 fallback
"""
import datetime
import json
import os
import sys
from typing import Optional

# 项目根加入 path（用于运行时 import tools.llm_api，做 lazy import 避免顶层依赖所有 LLM SDK）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


GRADES = {"S", "A", "B", "C"}
SCORER_PROVIDER = os.getenv("SCORER_LLM_PROVIDER", "openai")
SCORER_MODEL = os.getenv("SCORER_LLM_MODEL")  # None → use provider default
SCORER_DEBUG_DIR = os.getenv("SCORER_DEBUG_DIR")  # 设为目录路径则把 prompt/response/parsed 落盘


SCORER_SYSTEM_PROMPT = """你是一个投资雷达助手。任务：对当日聚合的信息打分，并对照 watchlist 检查 thesis 变化与候选标的的 entry trigger。

【信号分级硬规则】（不满足条件就不能打 S/A）
S 级（80+ 分）：可能改变长期 thesis 的重大信息。仅当满足：官方披露(+30) + 影响 revenue/guidance/margin/capex/roadmap(+25) + 涉及核心 thesis(+20) 等可累加为 80+
A 级（60-79）：值得当天看。财报、重大订单、重要高管变动、大额非计划 insider 交易、命中 candidate 的 revisit_trigger
B 级（40-59）：放入低优先级。分析师调价、行业小新闻、普通产品更新
C 级（<40）：忽略。重复新闻、标题党、小幅波动、无来源传闻

【打分加减分项】（适用于新闻/财报/高管买卖类 _kind）
+30 来自公司官方披露 / SEC / HKEX / 财报电话会
+25 影响 revenue / guidance / margin / capex / product roadmap / customer adoption
+20 涉及核心 thesis 或 red_flag
+10 股价/成交量异常且有明确新闻对应
+10 多个可靠来源确认
+5  分析师观点
-20 社交媒体传闻、未经证实
-30 重复新闻

【雪球大V长文（_kind=xueqiu_post）特殊评分规则】
雪球长文是 KOL 个人观点/调研，不是官方披露，"+30 官方披露" 一律不适用。改用以下规则：
+30 给出可验证的一手数据/调研（产业链访谈、终端零售数据、产品体验细节）
+25 论点直接对应某 holding 的 thesis 或 red_flag，并且给了量化判断
+20 命中 candidate 的 revisit_trigger 描述（注意只是"作者认为命中"，要保守）
+10 观点立得住、有逻辑链条而非情绪宣泄
-10 全文是 100-200 字预览（is_full_content=false 且 content 较短），证据有限
-20 主要在反驳/回复别人，不构成独立观点
-30 标题党、玄学、纯抒情或行业大水漫灌评论

雪球内容判定 thesis_impact 时一般只能是"可能增强 X thesis"或"可能削弱 X thesis"，
而非"增强/削弱"——因为是 KOL 观点不是事实。命中 candidate trigger 也要写"作者认为命中"。

【输出要求】
1. 严格输出 JSON，不要 markdown 代码块包裹
2. 中文
3. 不给买卖建议
4. 不预测股价

输出 JSON schema：
{
  "scored_items": [
    {
      "id": "原始 id",
      "grade": "S|A|B|C",
      "why_important": "一句话说明，<=40 字",
      "thesis_impact": "增强 X thesis | 削弱 X red_flag | 无影响",
      "trigger_hit": "命中候选 X 的 trigger: ...（无则空字符串）"
    }
  ],
  "thesis_deltas": [
    {
      "symbol": "持仓代码",
      "delta": -2,
      "reason": "<=30 字"
    }
  ],
  "candidate_hits": [
    {
      "symbol": "候选代码",
      "trigger": "命中的 trigger 文本",
      "evidence": "对应信息的简要描述 + id"
    }
  ]
}"""


def _build_user_prompt(items, holdings, candidates):
    """构造用户 prompt：包含 watchlist 上下文和当日信息列表。"""
    holdings_ctx = []
    for h in holdings:
        holdings_ctx.append({
            "symbol": h["symbol"],
            "name": h.get("name", h["symbol"]),
            "thesis": h.get("thesis", []),
            "red_flags": h.get("red_flags", []),
        })

    candidates_ctx = []
    for c in candidates:
        candidates_ctx.append({
            "symbol": c["symbol"],
            "name": c.get("name", c["symbol"]),
            "why_not_buying_now": c.get("why_not_buying_now", []),
            "revisit_triggers": c.get("revisit_triggers", []),
        })

    items_brief = []
    for it in items:
        # 雪球帖子用 content（可能是预览或全文），其他类型用 snippet
        kind = it.get("_kind")
        if kind == "xueqiu_post":
            body = (it.get("content") or "")[:1500]  # 长文截到 1500 字，控制 prompt 大小
        else:
            body = (it.get("snippet") or "")[:300]
        brief = {
            "id": it.get("id"),
            "symbol": it.get("symbol"),
            "kind": kind,  # earnings_forward / sa_news / sa_analysis / form4 / xueqiu_post
            "title": it.get("title", ""),
            "snippet": body,
            "url": it.get("url", ""),
        }
        # 雪球补充 source（含作者名）和"是否全文"，让 LLM 衡量证据强度
        if kind == "xueqiu_post":
            brief["source"] = it.get("source", "")
            brief["is_full_content"] = bool(it.get("is_full_content"))
        items_brief.append(brief)

    payload = {
        "holdings": holdings_ctx,
        "candidates": candidates_ctx,
        "items": items_brief,
    }
    return (
        "请按系统提示对以下信息打分并检查 thesis / trigger。"
        "对每条 items 必须返回一个 scored_items 条目（id 一一对应）。"
        "对每个 holdings 必须返回一个 thesis_deltas 条目（即使 delta=0）。"
        "candidate_hits 仅列出今日命中 trigger 的候选。\n\n"
        f"输入数据：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _parse_response(text):
    """从 LLM 输出中提取 JSON。容忍偶尔被 markdown fence 包裹的情况。"""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 包装
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except Exception as e:
        print(f"⚠️ scorer JSON 解析失败：{e}", file=sys.stderr)
        return None


def _fallback_passthrough(items, holdings):
    """LLM 不可用时的兜底：所有条目标 B，thesis_deltas 全部 0，candidate_hits 为空。"""
    scored = [
        {
            "id": it.get("id"),
            "grade": "B",
            "why_important": "（未启用 LLM 评分）",
            "thesis_impact": "",
            "trigger_hit": "",
        }
        for it in items
    ]
    deltas = [{"symbol": h["symbol"], "delta": 0, "reason": "（未启用 LLM 评分）"} for h in holdings]
    return {"scored_items": scored, "thesis_deltas": deltas, "candidate_hits": []}


def score_items(items, holdings, candidates):
    """对当日所有信息批量评分。

    items: 已合并的列表，每项需包含 id/symbol/title 等；建议在合并前给每项加上 `_kind`（earnings_forward/sa_news/sa_analysis/form4）以便 LLM 区分。
    holdings: 来自 invest_config.get_holdings()
    candidates: 来自 invest_config.get_candidates()

    返回: dict {scored_items, thesis_deltas, candidate_hits}
    """
    if not items and not holdings and not candidates:
        return {"scored_items": [], "thesis_deltas": [], "candidate_hits": []}

    if not _llm_available():
        print("scorer: 未检测到 LLM API key，使用兜底（不启用雷达评分）")
        return _fallback_passthrough(items, holdings)

    if not items:
        # 没有信息可评分时仍返回 thesis_deltas（全 0）以便日报渲染表格
        return {
            "scored_items": [],
            "thesis_deltas": [{"symbol": h["symbol"], "delta": 0, "reason": "今日无新信息"} for h in holdings],
            "candidate_hits": [],
        }

    prompt = _build_user_prompt(items, holdings, candidates)
    full_prompt = SCORER_SYSTEM_PROMPT + "\n\n" + prompt
    print(f"scorer: 调用 LLM 评分（provider={SCORER_PROVIDER}, items={len(items)}）...")
    response = None
    try:
        # Lazy import：避免在没有装齐所有 SDK 时模块加载失败
        from tools.llm_api import create_llm_client, query_llm
        client = create_llm_client(SCORER_PROVIDER)
        response = query_llm(full_prompt, client=client, model=SCORER_MODEL, provider=SCORER_PROVIDER)
    except Exception as e:
        print(f"⚠️ scorer 调用失败：{e}", file=sys.stderr)
        _debug_dump(full_prompt, response, None, error=str(e))
        return _fallback_passthrough(items, holdings)

    parsed = _parse_response(response)
    _debug_dump(full_prompt, response, parsed)
    if not parsed:
        return _fallback_passthrough(items, holdings)

    # 校验/补全
    parsed.setdefault("scored_items", [])
    parsed.setdefault("thesis_deltas", [])
    parsed.setdefault("candidate_hits", [])
    # 对未评分的条目兜底为 B
    scored_ids = {s.get("id") for s in parsed["scored_items"]}
    for it in items:
        if it.get("id") not in scored_ids:
            parsed["scored_items"].append({
                "id": it.get("id"),
                "grade": "B",
                "why_important": "（LLM 未评分，默认 B）",
                "thesis_impact": "",
                "trigger_hit": "",
            })
    # 持仓 thesis_delta 缺漏补 0
    delta_symbols = {d.get("symbol") for d in parsed["thesis_deltas"]}
    for h in holdings:
        if h["symbol"] not in delta_symbols:
            parsed["thesis_deltas"].append({"symbol": h["symbol"], "delta": 0, "reason": "无显著信息"})

    # 标准化 grade 字段
    for s in parsed["scored_items"]:
        g = (s.get("grade") or "B").upper()
        s["grade"] = g if g in GRADES else "B"

    return parsed


def _debug_dump(prompt: str, response, parsed, error: Optional[str] = None) -> None:
    """SCORER_DEBUG_DIR 设了就把 LLM 输入/输出/解析结果写到时间戳文件，方便事后排查。"""
    if not SCORER_DEBUG_DIR:
        return
    try:
        os.makedirs(SCORER_DEBUG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SCORER_DEBUG_DIR, f"scorer_{ts}.json")
        payload = {
            "provider": SCORER_PROVIDER,
            "model": SCORER_MODEL,
            "prompt": prompt,
            "raw_response": response,
            "parsed": parsed,
            "error": error,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"scorer: debug dump → {path}")
    except Exception as e:
        print(f"⚠️ scorer debug dump 失败：{e}", file=sys.stderr)


def _llm_available() -> bool:
    """根据 SCORER_LLM_PROVIDER 检查对应 API key 是否就绪。"""
    key_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "siliconflow": "SILICONFLOW_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
    }
    env_key = key_map.get(SCORER_PROVIDER)
    if env_key is None:
        return True  # local provider 等
    return bool(os.getenv(env_key))


def attach_grades(items, scored_items):
    """把 scored_items 的 grade/why/thesis_impact/trigger_hit 合并回原 items（按 id）。

    返回新列表（不修改原 items）。
    """
    by_id = {s.get("id"): s for s in scored_items}
    out = []
    for it in items:
        s = by_id.get(it.get("id"), {})
        merged = dict(it)
        merged["grade"] = s.get("grade", "B")
        merged["why_important"] = s.get("why_important", "")
        merged["thesis_impact"] = s.get("thesis_impact", "")
        merged["trigger_hit"] = s.get("trigger_hit", "")
        out.append(merged)
    return out
