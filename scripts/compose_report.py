#!/usr/bin/env python3
"""Compose a trademark big-data report by orchestrating the trademark MCP.

Calls the upstream trademark-mcp-server tools and assembles a structured JSON
payload rendered into a professional HTML / Markdown report. Supports
``--dry-run`` which returns a well-formed skeleton from the bundled sample data
WITHOUT contacting the MCP.

Workflow (real run):
  1. Resolve the canonical enterprise name (fuzzy search if only a keyword).
  2. Query trademark_search (records), trademark_profile (概况), trademark_stats (趋势).
  3. Build unified report JSON with domain sections (概况指标 / 申请注册趋势 / 状态分布 / 类别分布 / 商标明细).
  4. Optionally render HTML + Markdown.

This file never prints secrets; MCP credentials live in the server's own .env.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import pathlib
from typing import Any, Dict, List, Mapping, Optional, Sequence

from common import REPORT_BANNER, REPORT_TYPE, attach_pagination, call_tool_once, collect_detail_pages, paid_call_scope, json_dumps, load_json_file, print_json
import mcp_client
from semantic_review import prepare_draft
from render_report import render_html, render_markdown, html_to_pdf

SAMPLE_PATH = pathlib.Path(__file__).resolve().parent.parent / "assets" / "report.example.json"

# Trademark MCP tools.
T_FUZZY = "trademark_bigdata_fuzzy_search"
T_SEARCH = "trademark_bigdata_trademark_search"
T_PROFILE = "trademark_bigdata_trademark_profile"
T_STATS = "trademark_bigdata_trademark_stats"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #




def _is_empty_signal(value: Any) -> bool:
    """Recognize documented no-data text without classifying it as an API failure."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(text) and any(token in text for token in (
        "查询数据为空", "暂无数据", "未查询到", "未检索到", "查询结果为空",
    ))

def _is_api_error(value: Any) -> bool:
    """Recognize failures without treating a documented empty result as an error."""
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        if any(s in text for s in ("查询数据为空", "暂无数据", "未查询到", "未检索到")):
            return False
        return any(s in text for s in (
            "接口调用失败", "查询失败", "Error executing tool", "validation error",
            "状态码：4", "状态码：5", "密钥错误", "签名错误", "未配置",
        ))
    if not isinstance(value, dict):
        return False
    if value.get("isError") is True or value.get("success") is False:
        return True
    if value.get("_error") or value.get("error") or value.get("error_message"):
        return True
    if "code" in value and value.get("code") not in (None, 0, 200, "0", "200", "SUCCESS", "success"):
        return True
    if set(value) == {"text"} and value.get("text"):
        return not any(s in str(value["text"]) for s in ("查询数据为空", "暂无数据", "未查询到", "未检索到"))
    return any(_is_api_error(v) for v in value.values() if isinstance(v, (dict, str)))


def _first_list(value: Any) -> List[Any]:
    if _is_api_error(value):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("resultList", "list", "items", "records", "data"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def _first_record(value: Any) -> Dict[str, Any]:
    for record in _first_list(value):
        if isinstance(record, dict) and _has_evidence(record):
            return record
    if isinstance(value, dict) and not _is_api_error(value) and _has_evidence(value):
        return value
    return {}


def _has_evidence(value: Any) -> bool:
    if value is None or value in ("", "-") or _is_empty_signal(value):
        return False
    if _is_api_error(value):
        return False
    if isinstance(value, dict):
        return any(_has_evidence(v) for k, v in value.items() if k not in {"sections", "_raw"})
    if isinstance(value, list):
        return any(_has_evidence(v) for v in value)
    return True


def _text(value: Any, limit: int = 0) -> str:
    if value is None or _is_empty_signal(value):
        return ""
    if isinstance(value, (dict, list)):
        t = json.dumps(value, ensure_ascii=False)
    else:
        t = str(value)
    t = " ".join(t.split())
    if limit and len(t) > limit:
        return t[: limit - 1].rstrip() + "…"
    return t


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_call(tool: str, arguments: Dict[str, Any]) -> Any:
    try:
        result = call_tool_once(mcp_client.call_tool, tool, arguments)
        # Detect API error responses (405, etc.) and return error marker
        if _is_api_error(result):
            return {"_error": "API错误", "_raw": result}
        return result
    except Exception as exc:
        return {"_error": str(exc)}


def _safe_total(payload: Any) -> Any:
    if isinstance(payload, dict):
        if _is_api_error(payload):
            return None
        return payload.get("total")
    return None


def _collect_detail_pages(tool: str, arguments: Dict[str, Any], max_pages: int) -> Dict[str, Any]:
    """Total-driven detail collection within an explicit paid-page budget (default 1 page).

    The first paid page serves both as report data and as total discovery; it is
    never refetched. Additional pages are only requested inside the budget.
    """
    return collect_detail_pages(
        _safe_call, tool, dict(arguments), get_rows=_first_list, get_total=_safe_total,
        is_error=_is_api_error, max_pages=max_pages,
    )


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def resolve_enterprise_name(raw: str) -> Dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {"keyword": "", "enterprise": "", "resolved": False, "reason": "关键词为空"}
    if any(suffix in raw for suffix in ("公司", "集团", "有限", "院", "厂", "中心", "事务所", "合作社", "合伙")):
        return {"keyword": raw, "enterprise": raw, "resolved": True, "reason": "视为企业全称"}
    fuzzy = _safe_call(T_FUZZY, {"matchKeyword": raw, "pageSize": 1})
    record = _first_record(fuzzy)
    total = _int(_safe_total(fuzzy))
    name = str(record.get("name") or "").strip()
    if name and total == 1:
        return {"keyword": raw, "enterprise": name, "resolved": True, "reason": "模糊查询唯一候选补全", "fuzzy_total": total, "record": record}
    if name and total and total > 1:
        return {"keyword": raw, "enterprise": raw, "resolved": False, "reason": f"模糊查询返回 {total} 个候选，未静默采用首条", "fuzzy_total": total}
    return {"keyword": raw, "enterprise": raw, "resolved": False, "reason": "模糊查询未命中唯一企业全称，按原始关键词查询"}


# --------------------------------------------------------------------------- #
# Enterprise profile helpers (from fuzzy_search record)
# --------------------------------------------------------------------------- #

def _extract_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    """Extract enterprise profile fields from a fuzzy_search record."""
    return {
        "name": _text(record.get("name")),
        "reg_capital": record.get("regCapitalValue"),
        "reg_capital_coin": _text(record.get("regCapitalCoinType")),
        "annual_turnover": _text(record.get("annualTurnover")),
        "oper_status": _text(record.get("operStatus")),
        "enterprise_type": _text(record.get("enterpriseType")),
        "found_time": _text(record.get("foundTime")),
        "legal_rep": _text(record.get("legalRepresentative")),
        "address": _text(record.get("address")),
        "homepage": _text(record.get("homepage")),
    }


def _format_capital(val: Any, coin: str = "") -> str:
    """Format capital value: 10995210218.0 -> '109.95 亿'."""
    try:
        v = float(val)
        if v >= 1e8:
            s = f"{v / 1e8:.2f} 亿"
        elif v >= 1e4:
            s = f"{v / 1e4:.2f} 万"
        else:
            s = f"{v:.0f}"
        if coin:
            s += f" {coin}"
        return s
    except (TypeError, ValueError):
        return _text(val) if val else "-"


def _enrich_metrics_with_profile(metrics: List[Dict[str, Any]], record: Any) -> List[Dict[str, Any]]:
    """Append enterprise profile metrics from a fuzzy_search record."""
    if not isinstance(record, dict):
        return metrics
    _prof = _extract_profile(record)
    if _prof.get("reg_capital") and _prof["reg_capital"] not in ("-", "", None):
        metrics.append({"label": "注册资本", "value": _format_capital(_prof["reg_capital"], _prof.get("reg_capital_coin", "")), "hint": "工商登记注册资本"})
    if _prof.get("found_time") and _prof["found_time"] != "-":
        metrics.append({"label": "成立时间", "value": _prof["found_time"], "hint": "工商登记成立日期"})
    if _prof.get("oper_status") and _prof["oper_status"] != "-":
        metrics.append({"label": "经营状态", "value": _prof["oper_status"], "hint": "工商登记经营状态"})
    if _prof.get("enterprise_type") and _prof["enterprise_type"] != "-":
        metrics.append({"label": "企业类型", "value": _prof["enterprise_type"], "hint": "工商登记企业类型"})
    if _prof.get("legal_rep") and _prof["legal_rep"] != "-":
        metrics.append({"label": "法定代表人", "value": _prof["legal_rep"], "hint": "工商登记法定代表人"})
    return metrics


# Legacy sample-scoped metric labels removed from composer output; used only by
# apply_content_review() to clean older payloads.
SAMPLE_METRIC_LABELS = {"商标分类数", "商标状态数"}


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #

def build_subject(raw: str, resolved: Mapping[str, Any], keyword_type: str) -> Dict[str, Any]:
    return {
        "enterprise": resolved.get("enterprise") or raw,
        "matchKeyword": resolved.get("enterprise") or raw,
        "keywordType": keyword_type,
        "match_raw": raw,
        "resolved": bool(resolved.get("resolved")),
        "resolve_reason": resolved.get("reason", ""),
        "searchKeywordType": "申请人",
        "fuzzy_total": resolved.get("fuzzy_total"),
        "requires_confirmation": bool((resolved.get("fuzzy_total") or 0) > 1),
    }


def _sum_stat_counts(stats: Mapping[str, Any], key: str) -> int:
    """Sum the `count` field across stat rows for a given key (missing count = 0)."""
    total = 0
    for item in _first_list(stats.get(key)):
        if isinstance(item, dict):
            c = item.get("count")
            if c is None:
                continue
            try:
                total += int(float(str(c)))
            except (TypeError, ValueError):
                pass
    return total


def build_metrics(profile: Mapping[str, Any], search_total: Any, stats: Mapping[str, Any]) -> List[Dict[str, Any]]:
    metrics: List[Dict[str, Any]] = []
    p = profile if isinstance(profile, dict) else {}
    total_n = _int(p.get("tmCount"))
    valid_n = _int(p.get("tmValidNumber"))
    metrics.append({"label": "商标总数", "value": _text(p.get("tmCount")) or "-", "hint": "企业商标总量"})
    if valid_n is not None and total_n:
        share = valid_n / total_n * 100
        metrics.append({"label": "有效商标", "value": _text(p.get("tmValidNumber")) or "-", "hint": "当前有效商标数", "delta": f"有效率 {share:.0f}%"})
    else:
        metrics.append({"label": "有效商标", "value": _text(p.get("tmValidNumber")) or "-", "hint": "当前有效商标数"})
    metrics.append({"label": "无效商标", "value": _text(p.get("tmInvalidNumber")) or "-", "hint": "无效商标数"})
    metrics.append({"label": "本年度申请", "value": _text(p.get("tmNumberThisYear")) or "-", "hint": "生成年度内申请数（年初至生成时点）"})
    metrics.append({"label": "类别覆盖", "value": (str(len(p.get("tmTypeList") or [])) + " 类" if isinstance(p.get("tmTypeList"), list) else "-"), "hint": "涵盖商标类别数"})
    if search_total is not None:
        metrics.append({"label": "检索结果", "value": str(search_total) + " 条", "hint": "本次检索命中商标条数"})
    # 两组按年份独立统计，不是同一申请队列的成功率。
    app_total = _sum_stat_counts(stats, "tmAppTimeStat")
    reg_total = _sum_stat_counts(stats, "tmRegTimeStat")
    if app_total > 0:
        success_rate = reg_total / app_total * 100
        metrics.append({"label": "报告期注册/申请量比", "value": f"{success_rate:.0f}%", "hint": f"各报告期注册总量/申请总量（{reg_total}/{app_total}），年份不同、非同一申请队列，不是成功率", "delta": f"申请 {app_total}、注册 {reg_total}"})
    return [m for m in metrics if m.get("value") not in ("", None, "-")]


def build_caliber(subject: Mapping[str, Any]) -> Dict[str, Any]:
    detail_filter = "申请人=目标企业"
    if subject.get("tmStatus"):
        detail_filter += f"，商标状态={subject['tmStatus']}"
    return {
        "match_target": subject.get("enterprise") or subject.get("match_raw"),
        "match_type": f"商标概况按企业主体匹配（keywordType={subject.get('keywordType', 'name')}）；检索支持商标名称/申请号/申请人/代理机构",
        "data_scope": f"商标概况和趋势统计（企业整体口径）；商标检索明细（明细筛选：{detail_filter}）；状态分布、类别分布",
        "products": ["商标检索", "商标概况", "商标趋势统计"],
        "limit": "数据来自商标公开数据库；少量字段可能存在更新延迟。",
    }


def _stat_rows(stats: Mapping[str, Any], key: str, label_key: str, count_key: str = "count", year_key: str = "year") -> List[Dict[str, Any]]:
    rows = []
    for item in _first_list(stats.get(key)):
        if not isinstance(item, dict) or not _has_evidence(item):
            continue
        label = item.get(label_key) or item.get(year_key) or item.get("tmStatus") or item.get("tmName") or "-"
        rows.append({
            "名称/类别/年份": _text(label),
            "数量": _text(item.get(count_key) or item.get("value") or "-"),
        })
    return rows


def _same_subject(target: str, applicant: Any) -> bool:
    def norm(value: Any) -> str:
        return "".join(ch for ch in _text(value).lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    left, right = norm(target), norm(applicant)
    return bool(left and right and (left in right or right in left))


def build_core_analysis(profile: Mapping[str, Any], search: Any, stats: Mapping[str, Any], target: str = "", tm_status: Optional[str] = None) -> Dict[str, Any]:
    p = profile if isinstance(profile, dict) else {}
    s = stats if isinstance(stats, dict) else {}

    # 概况 KV
    profile_kv: Dict[str, Any] = {}
    for k, label in (("tmCount", "商标总数"), ("tmValidNumber", "有效商标数"), ("tmInvalidNumber", "无效商标数"), ("tmNumberThisYear", "近一年申请数")):
        if p.get(k) is not None:
            profile_kv[label] = _text(p.get(k))
    if isinstance(p.get("tmTypeList"), list) and p["tmTypeList"]:
        profile_kv["涵盖类别"] = "、".join(_text(t) for t in p["tmTypeList"] if t)
    if isinstance(p.get("tmStatusList"), list) and p["tmStatusList"]:
        profile_kv["商标状态"] = "、".join(_text(t) for t in p["tmStatusList"] if t)

    # 检索明细表
    search_rows = []
    total = None
    if isinstance(search, dict):
        total = search.get("total")
    for item in _first_list(search):
        if not isinstance(item, dict) or not _has_evidence(item):
            continue
        if target and not _same_subject(target, item.get("tmCompanyName")):
            continue
        search_rows.append({
            "商标名称": _text(item.get("tmName")) or "-",
            "申请号": _text(item.get("tmRegNum")) or "-",
            "申请人": _text(item.get("tmCompanyName")) or "-",
            "国际分类": _text(item.get("internationalClass") or item.get("tmSingleInternationalClass")) or "-",
            "申请日期": _text(item.get("tmApplicationTime")) or "-",
            "注册日期": _text(item.get("tmRegTime")) or "-",
            "商标状态": _text(item.get("tmStatus")) or "-",
            "代理机构": _text(item.get("tmAgentName")) or "-",
        })

    # 趋势/状态/类别统计
    apply_trend = _stat_rows(s, "tmAppTimeStat", "year")
    reg_trend = _stat_rows(s, "tmRegTimeStat", "year")
    status_stat = _stat_rows(s, "tmStatusStat", "tmStatus")
    type_stat = _stat_rows(s, "tmTypeStats", "tmName")

    sections = [
        {"key": "profile_overview", "title": "商标概况", "kind": "kv"},
        {"key": "apply_trend", "title": "商标申请趋势", "kind": "line", "note": "按年度统计商标申请数量", "chart": {"x": "名称/类别/年份", "y": "数量", "area": True}, "columns": [("年份/周期", "名称/类别/年份"), ("数量", "数量")]},
        {"key": "reg_trend", "title": "商标注册趋势", "kind": "line", "note": "按年度统计商标注册数量", "chart": {"x": "名称/类别/年份", "y": "数量", "area": True}, "columns": [("年份/周期", "名称/类别/年份"), ("数量", "数量")]},
        {"key": "status_stat", "title": "商标状态分布", "kind": "pie", "note": "按商标状态统计数量", "chart": {"name": "名称/类别/年份", "value": "数量", "donut": True}, "columns": [("状态", "名称/类别/年份"), ("数量", "数量")]},
        {"key": "type_stat", "title": "商标类别分布", "kind": "bar", "note": "按国际分类统计数量", "chart": {"name": "名称/类别/年份", "value": "数量", "orient": "v"}, "columns": [("类别", "名称/类别/年份"), ("数量", "数量")]},
        {"key": "search_records", "title": "商标检索明细", "kind": "table", "note": (
            f"明细筛选：申请人={target or '目标企业'}" + (f"、商标状态={tm_status}" if tm_status else "")
            + (f"；上游总量 {total} 条，本次采集 {len(search_rows)} 条" if total is not None
               else f"；上游未返回总量，本次采集 {len(search_rows)} 条")
        ),
         "columns": [("商标名称", "商标名称"), ("申请号", "申请号"), ("申请人", "申请人"), ("国际分类", "国际分类"), ("申请日期", "申请日期"), ("注册日期", "注册日期"), ("商标状态", "商标状态"), ("代理机构", "代理机构")]},
    ]

    return {
        "sections": sections,
        "profile_overview": profile_kv,
        "apply_trend": apply_trend,
        "reg_trend": reg_trend,
        "status_stat": status_stat,
        "type_stat": type_stat,
        "search_records": search_rows,
    }


def build_records(core: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for item in core.get("search_records") or []:
        out.append({
            "商标名称": item.get("商标名称") or "-",
            "国际分类": item.get("国际分类") or "-",
            "申请日期": item.get("申请日期") or "-",
            "商标状态": item.get("商标状态") or "-",
        })
    return out[:20]


def _period_year(value: Any) -> Optional[int]:
    text = _text(value)
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _trend_analysis(rows: List[Mapping[str, Any]], as_of_year: Optional[int] = None) -> Dict[str, Any]:
    """Compute a reliable yearly trend from a {period,count} series.

    The current calendar year is partial (tmNumberThisYear is year-to-date) and
    is excluded from peak/YoY. Fewer than 3 complete years, or either of the
    last two complete years below 10 filings, is reported as insufficient.
    """
    points: List[tuple[Any, float]] = []
    for r in rows:
        try:
            n = float(str(r.get("数量", 0)).replace(",", ""))
        except (TypeError, ValueError):
            n = 0.0
        points.append((r.get("名称/类别/年份", "-"), n))
    if not points:
        return {}

    partial_period = None
    if as_of_year is not None:
        complete = [(p, n) for p, n in points if _period_year(p) != as_of_year]
        if len(complete) >= 2 and len(complete) < len(points):
            partial_period = _text(points[-1][0])
            points = complete
    if len(points) < 3:
        return {"insufficient": "完整年度不足 3 期，未作趋势研判", "partial_period": partial_period}

    last_n = points[-1][1]
    prev_n = points[-2][1]
    if min(last_n, prev_n) < 10:
        return {
            "insufficient": "近两个完整年度数量过少（<10 件/年），未作同比研判",
            "partial_period": partial_period,
            "last_period": _text(points[-1][0]),
            "last": last_n,
        }

    peak_idx = max(range(len(points)), key=lambda i: points[i][1])
    pct = (last_n - prev_n) / prev_n * 100 if prev_n > 0 else 0.0
    if abs(pct) <= 5:
        direction = "基本持平"
        yoy = ""
    else:
        direction = f"{'上升' if pct > 0 else '下降'} {abs(pct):.0f}%"
        verb = "上升" if pct > 0 else "下降"
        yoy = f"{_text(points[-1][0])} 年较 {_text(points[-2][0])} 年{verb} {abs(pct):.0f}%"
    return {
        "peak_period": _text(points[peak_idx][0]),
        "peak_value": points[peak_idx][1],
        "direction": direction,
        "yoy": yoy,
        "last": last_n,
        "compare_period": _text(points[-1][0]),
        "base_period": _text(points[-2][0]),
        "partial_period": partial_period,
    }


def _concentration(rows: List[Mapping[str, Any]], top_n: int = 3) -> Dict[str, Any]:
    """Compute top-N concentration, excluding an upstream "其他" bucket.

    "其他" is an aggregation of unlisted classes, not a class. Treating it as
    the top class produced misleading evidence such as “其他占比 40%”. The
    bucket total is returned separately so the report can disclose it.
    """
    items = []
    other_total = 0.0
    for r in rows:
        name = r.get("名称/类别/年份", "-")
        try:
            value = float(str(r.get("数量", 0)).replace(",", ""))
        except (TypeError, ValueError):
            value = 0.0
        if str(name).strip() == "其他":
            other_total += value
            continue
        items.append((name, value))
    total = sum(v for _, v in items)
    if not total:
        return {}
    items.sort(key=lambda x: x[1], reverse=True)
    cr = sum(v for _, v in items[:top_n]) / total * 100
    return {
        "top": items[0][0],
        "top_share": items[0][1] / total * 100,
        "cr": cr,
        "total": total,
        "other_total": other_total,
    }


def _distribution_evenness(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Assess whether category distribution is even (uniform/defensive) vs concentrated.
    Uses coefficient of variation (CV = std/mean); CV low => evenly spread."""
    nums = []
    for r in rows:
        if str(r.get("名称/类别/年份", "")).strip() == "其他":
            continue
        try:
            nums.append(float(str(r.get("数量", 0)).replace(",", "")))
        except (TypeError, ValueError):
            pass
    nums = [n for n in nums if n > 0]
    if len(nums) < 3:
        return {}
    mean = sum(nums) / len(nums)
    if mean <= 0:
        return {}
    var = sum((n - mean) ** 2 for n in nums) / len(nums)
    std = var ** 0.5
    cv = std / mean  # coefficient of variation
    return {"count": len(nums), "mean": mean, "cv": cv, "even": cv < 0.25}


def build_insights(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]], as_of_year: Optional[int] = None) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []
    metric_map = {m["label"]: str(m["value"]) for m in metrics}
    tm_count = metric_map.get("商标总数")
    valid = metric_map.get("有效商标")
    invalid = metric_map.get("无效商标")
    recent = metric_map.get("本年度申请")

    if tm_count:
        insights.append({
            "feature": "商标资产规模",
            "evidence": f"当前返回口径下商标记录总数为 {tm_count} 件。",
            "interpretation": "该计数只表示当前数据口径内的商标记录规模；不能说明品牌布局广度、市场覆盖或资产健康度，需结合类别、有效状态和实际使用证据。",
        })
    if valid or invalid:
        try:
            v_n = float(valid) if valid else 0.0
            i_n = float(invalid) if invalid else 0.0
            total_n = float(tm_count) if tm_count else 0.0
            # Match the KPI delta denominator (valid / total) so the same
            # indicator is not shown with two different effective rates.
            denom = total_n if total_n > 0 else v_n + i_n
            ratio = f"有效商标占商标总数 {v_n / denom * 100:.0f}%" if denom else ""
        except (TypeError, ValueError):
            ratio = ""
        evidence = "、".join(p for p in (f"有效商标 {valid}" if valid else None, f"无效商标 {invalid}" if invalid else None) if p) + "。"
        insights.append({
            "feature": "商标有效性",
            "evidence": evidence + (f"（{ratio}）" if ratio else ""),
            "interpretation": "该比例只描述当前返回的有效/无效数量结构；形成原因需结合续展、驳回、撤销和无效宣告记录核查。",
        })
    type_stat = core.get("type_stat") or []
    if type_stat:
        evenness = _distribution_evenness(type_stat)
        conc = _concentration(type_stat, 3)
        other_clause = ""
        if conc and conc.get("other_total"):
            other_clause = f"；另有“其他”未细分 {int(conc['other_total'])} 件（不计入类别份额）"
        if evenness and evenness.get("even"):
            insights.append({
                "feature": "类别均匀布局",
                "evidence": f"已列明类别 {evenness['count']} 个，各类别数量接近（均值 {evenness['mean']:.1f}，变异系数 {evenness['cv']:.2f}{other_clause}）。",
                "interpretation": "低方差只说明返回记录在已列明类别间较均匀；是否属于防御性注册、多品牌或多业务布局需结合商标用途核验。",
            })
        elif conc:
            insights.append({
                "feature": "类别集中度",
                "evidence": f"“{conc['top']}”占已列明类别约 {conc['top_share']:.0f}%，前 3 类合计 {conc['cr']:.0f}%（CR3）{other_clause}。",
                "interpretation": "CR3 只描述已列明类别的数量集中度；“其他”为上游未细分聚合，不能当作单一类别或核心业务判断依据。",
            })
    status_stat = core.get("status_stat") or []
    if status_stat:
        conc = _concentration(status_stat, 2)
        if conc:
            insights.append({
                "feature": "商标状态结构",
                "evidence": f"“{conc['top']}”为主流状态，占比约 {conc['top_share']:.0f}%。",
                "interpretation": "该结果只描述当前状态数量结构；稳定性、活跃度和维护风险需结合申请时间、续展及案件记录核验。",
            })
    apply_trend = core.get("apply_trend") or []
    if apply_trend:
        ta = _trend_analysis(apply_trend, as_of_year)
        if ta and not ta.get("insufficient"):
            extra = f"；{ta['partial_period']} 年数据截至生成时点，未纳入趋势比较" if ta.get("partial_period") else ""
            insights.append({
                "feature": "申请趋势研判",
                "evidence": f"峰值出现在“{ta['peak_period']}”（{ta['peak_value']:.0f} 件）；{ta['yoy'] or '近两个完整年度基本持平'}{extra}。",
                "interpretation": "趋势仅描述申请数量变化，不能单独证明品牌投入、业务扩张或收缩，需结合产品发布和经营资料核验。",
            })
    reg_trend = core.get("reg_trend") or []
    if reg_trend:
        ta = _trend_analysis(reg_trend, as_of_year)
        if ta and not ta.get("insufficient") and ta.get("direction") != "基本持平":
            extra = f"；{ta['partial_period']} 年数据截至生成时点，未纳入趋势比较" if ta.get("partial_period") else ""
            insights.append({
                "feature": "注册趋势研判",
                "evidence": f"注册峰值在“{ta['peak_period']}”（{ta['peak_value']:.0f} 件）；{ta['yoy'] or ta['direction']}{extra}。",
                "interpretation": "注册趋势描述报告期核准数量，可能对应更早申请；不能单独证明权利持续稳固。",
            })
    # A reported zero is evidence of zero applications, not evidence of strategy.
    try:
        this_year_n = int(float(str(recent))) if recent is not None else None
    except (TypeError, ValueError):
        this_year_n = None
    if this_year_n == 0:
        insights.append({
            "feature": "本年度申请观察",
            "evidence": "本年度（截至生成时点）新申请商标数为 0。",
            "interpretation": "该字段仅表示返回口径下本年度申请数为 0，不能单独判断品牌布局停滞或保护不足。",
        })
    if not insights:
        insights.append({
            "feature": "数据完整性",
            "evidence": "部分维度未返回有效数据。",
            "interpretation": "建议核对匹配关键词是否为企业全称，或检查 MCP 连接与上游数据产品覆盖范围。",
        })
    return insights


def build_abstract(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]]) -> str:
    name = subject.get("enterprise") or subject.get("match_raw") or "目标企业"
    if subject.get("requires_confirmation"):
        return f"原始关键词“{subject.get('match_raw') or name}”返回多个候选主体，待人工确认；本报告未调用商标详情，也不形成主体商标结论。"
    if not metrics and not any(_has_evidence(v) for k, v in core.items() if k != "sections"):
        return f"本次未获得“{name}”的可用商标证据，核心维度覆盖率为 0；不得据此判断商标数量、类别或状态。"
    parts = [f"本报告以“{name}”为分析对象，基于商标公开数据，系统呈现企业商标概况、申请/注册趋势、状态分布、类别分布与商标检索明细。"]
    if metrics:
        kv = "、".join(f"{m['label']} {m['value']}" for m in metrics[:5])
        parts.append(f"关键指标包括：{kv}。")
    parts.append("报告同时给出商标类别集中度与近期申请活跃度的结构化解读，便于品牌管理、竞争分析与知识产权决策参考。")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Dry-run sample
# --------------------------------------------------------------------------- #

def build_dry_run_payload(raw: str, keyword_type: str) -> Dict[str, Any]:
    try:
        sample = load_json_file(SAMPLE_PATH)
    except Exception:
        sample = {}
    sample = sample if isinstance(sample, dict) else {}
    subject = sample.get("subject") or {"enterprise": raw, "matchKeyword": raw, "keywordType": keyword_type, "match_raw": raw}
    subject = {**subject, "match_raw": raw, "keywordType": keyword_type}
    core = sample.get("core_analysis") or {}
    metrics = sample.get("metrics") or []
    return apply_content_review(_assemble(subject, core, metrics, dry_run=True))


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def _generated_year(value: Any) -> Optional[int]:
    text = _text(value)
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _content_quality_report(core: Mapping[str, Any]) -> Dict[str, Any]:
    """Section-fill rate from final declared sections and actual bodies.

    0 is populated; only None/""/[]/{} count as empty. Not an accuracy score.
    """
    ca = core if isinstance(core, dict) else {}
    sections = [s for s in (ca.get("sections") or []) if isinstance(s, dict) and s.get("key")]

    def filled(value: Any) -> bool:
        return value is not None and value != "" and value != [] and value != {}

    if sections:
        total = len(sections)
        populated = sum(1 for spec in sections if filled(ca.get(spec["key"])))
    else:
        keys = [k for k in ca if k != "sections"]
        total = max(1, len(keys))
        populated = sum(1 for key in keys if filled(ca.get(key)))
    return {
        "total_sections": total,
        "populated_sections": populated,
        "empty_sections": total - populated,
        "coverage_pct": round(populated / max(1, total) * 100),
    }


def _assemble(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]], *, dry_run: bool) -> Dict[str, Any]:
    abstract = build_abstract(subject, core, metrics)
    records = build_records(core)
    generated_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    insights = build_insights(subject, core, metrics, _generated_year(generated_at))
    quality_report = _content_quality_report(core)
    if quality_report["populated_sections"] == 0:
        import sys
        print("⚠️ 质量门禁警告: 所有核心分析维度均无数据", file=sys.stderr)
    title = f"{subject.get('enterprise') or '目标企业'} 商标大数据报告"
    report = {
        "report_type": REPORT_TYPE,
        "title": title,
        "banner": REPORT_BANNER,
        "subject": dict(subject),
        "abstract": abstract,
        "summary": abstract,
        "executive_summary": [item["interpretation"] for item in insights][:5] or [abstract[:120]],
        "metrics": list(metrics),
        "caliber": build_caliber(subject),
        "core_analysis": dict(core),
        "representative_records": records,
        "insights": insights,
        "data_source": {
            "mcp_server": "trademark-mcp-server",
            "products": [
                {"name": "商标检索", "product_id": "66b485eadaf8c77fb249a3cc"},
                {"name": "商标概况", "product_id": "671357d127ab3417e1f3f21b"},
                {"name": "商标趋势统计", "product_id": "66d5b7df537c3f61d646c2dc"},
            ],
            "generated_at": generated_at,
            "dry_run": dry_run,
            "quality_report": quality_report,
        },
    }
    return report


def _normalize_metric_labels(metrics: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Bring older payload metric labels in line with the corrected semantics."""
    renamed = {
        "近一年申请": ("本年度申请", "生成年度内申请数（年初至生成时点）"),
        "同期注册/申请量比": ("报告期注册/申请量比", "各报告期注册总量/申请总量，非同一申请队列，不是成功率"),
    }
    out: List[Dict[str, Any]] = []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        item = dict(metric)
        if item.get("label") in renamed:
            label, hint = renamed[item["label"]]
            item["label"] = label
            item["hint"] = hint
        out.append(item)
    return out


def apply_content_review(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Rebuild trademark narrative content from an existing payload.

    Pure/in-memory: uses core analysis, metrics, subject and generated_at already
    present in the payload. Changes: drops sample-scope metrics, normalizes
    current-year/registration-ratio labels, excludes the upstream "其他" class
    from concentration, rebuilds trend insights without the partial current
    year, and refreshes abstract/summary/executive_summary.
    """
    out = copy.deepcopy(dict(report))
    metrics = _normalize_metric_labels([m for m in (out.get("metrics") or []) if isinstance(m, dict) and m.get("label") not in SAMPLE_METRIC_LABELS])
    out["metrics"] = metrics
    subject = out.get("subject") or {}
    core = out.get("core_analysis") or {}
    source = out.get("data_source") or {}
    insights = build_insights(subject, core, metrics, _generated_year(source.get("generated_at")))
    out["insights"] = insights
    abstract = build_abstract(subject, core, metrics)
    out["abstract"] = abstract
    out["summary"] = abstract
    out["executive_summary"] = [item["interpretation"] for item in insights][:5] or [abstract[:120]]
    out.setdefault("data_source", {})["quality_report"] = _content_quality_report(core)
    return out


@paid_call_scope
def build_payload(raw: str, keyword_type: str, tm_status: Optional[str], page_size: int, max_pages: Optional[int] = 1) -> Dict[str, Any]:
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages 必须为正整数")
    resolved = resolve_enterprise_name(raw)
    if (resolved.get("fuzzy_total") or 0) > 1:
        subject = build_subject(raw, resolved, keyword_type)
        subject["tmStatus"] = tm_status
        core = build_core_analysis({}, {}, {}, raw, tm_status)
        return _assemble(subject, core, [], dry_run=False)
    enterprise = resolved["enterprise"]
    # Trademark detail tools use matchKeyword (full name) + keywordType.
    mk_args: Dict[str, Any] = {"matchKeyword": enterprise, "keywordType": keyword_type}
    search_args: Dict[str, Any] = {"matchKeyword": enterprise, "keywordType": "申请人", "pageIndex": 1, "pageSize": page_size}
    if tm_status:
        search_args["tmStatus"] = tm_status
    profile = _safe_call(T_PROFILE, mk_args)
    stats = _safe_call(T_STATS, mk_args)
    search = _collect_detail_pages(T_SEARCH, search_args, max_pages)
    search_total = _safe_total(search) if isinstance(search, dict) else None

    subject = build_subject(raw, resolved, keyword_type)
    subject["tmStatus"] = tm_status
    core = build_core_analysis(profile, search, stats, enterprise, tm_status)
    metrics = build_metrics(profile if isinstance(profile, dict) else {}, search_total, stats if isinstance(stats, dict) else {})
    # --- Enterprise profile enrichment (from fuzzy_search) ---
    _enrich_metrics_with_profile(metrics, resolved.get("record") if isinstance(resolved, dict) else None)
    report = apply_content_review(_assemble(subject, core, metrics, dry_run=False))
    return attach_pagination(report, {"商标检索明细": search})


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Compose a trademark big-data report via the trademark MCP.")
    parser.add_argument("--enterprise", required=True, help="企业全称或关键词（关键词将自动模糊补全）")
    parser.add_argument("--keyword-type", default="name", help="主体类型：name/nameId/regNumber/socialCreditCode")
    parser.add_argument("--tm-status", default=None, help="可选商标状态过滤（如 商标已注册 / 商标申请中 / 初审公告 等）")
    parser.add_argument("--page-size", type=int, default=10, help="商标检索明细分页大小（最多 50）")
    parser.add_argument("--max-pages", type=int, default=1, help="商标检索明细最多查询页数（正整数，默认 1 页；按报告结论需要先计划预算再显式扩大）")
    parser.add_argument("--dry-run", action="store_true", help="不调用真实 MCP，使用样例数据组装报告骨架")
    parser.add_argument("--output", help="输出 JSON 路径；省略则打印到 stdout")
    parser.add_argument("--report-output", help="同时输出 HTML 报告（.html）与 Markdown 报告（.md）")
    parser.add_argument("--pdf-output", help="额外输出 PDF 报告（.pdf）；需要 Playwright + Chromium")
    args = parser.parse_args()

    if args.max_pages < 1:
        parser.error("--max-pages 必须为正整数")
    if args.dry_run:
        payload = build_dry_run_payload(args.enterprise, args.keyword_type)
    else:
        payload = build_payload(args.enterprise, args.keyword_type, args.tm_status, args.page_size, args.max_pages)

    payload = prepare_draft(payload)

    if args.output:
        out = pathlib.Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json_dumps(payload, pretty=True), encoding="utf-8")
        print_json({"ok": True, "json": str(out), "dry_run": args.dry_run})
    else:
        print_json(payload)

    if args.report_output:
        base_out = pathlib.Path(args.report_output).expanduser()
        base_out.parent.mkdir(parents=True, exist_ok=True)
        html_path = base_out.with_suffix(".html") if base_out.suffix.lower() not in (".html", ".htm") else base_out
        md_path = html_path.with_suffix(".md")
        html_path.write_text(render_html(payload), encoding="utf-8")
        md_path.write_text(render_markdown(payload), encoding="utf-8")
        if args.pdf_output:
            pdf_path = pathlib.Path(args.pdf_output).expanduser()
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            html_to_pdf(render_html(payload), str(pdf_path))
        print_json({"ok": True, "html": str(html_path), "markdown": str(md_path), "pdf": str(pdf_path) if args.pdf_output else None, "dry_run": args.dry_run})


if __name__ == "__main__":
    main()
