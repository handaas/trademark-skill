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
import datetime as dt
import json
import pathlib
import sys
from typing import Any, Dict, List, Mapping, Optional

from common import REPORT_BANNER, REPORT_TYPE, json_dumps, load_json_file, print_json
import mcp_client
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


def _is_api_error(value: Any) -> bool:
    """Detect MCP API error responses (not empty data, but actual failures like 405)."""
    if value is None:
        return False
    if isinstance(value, str):
        return any(s in value for s in ("接口调用失败", "查询失败", "状态码：4", "状态码：5"))
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, str) and any(s in v for s in ("接口调用失败", "查询失败", "状态码：4", "状态码：5")):
                return True
    return False

def _first_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if _is_api_error(value):
            return []
        for key in ("resultList", "list", "items", "data"):
            if isinstance(value.get(key), list):
                return value[key]
    if value in (None, "", {}):
        return []
    return [value]


def _first_record(value: Any) -> Dict[str, Any]:
    for record in _first_list(value):
        if isinstance(record, dict):
            return record
    if isinstance(value, dict):
        return value
    return {}


def _text(value: Any, limit: int = 0) -> str:
    if value is None:
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
        result = mcp_client.call_tool(tool, arguments)
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
    name = str(record.get("name") or "").strip()
    if name:
        return {"keyword": raw, "enterprise": name, "resolved": True, "reason": "由关键词模糊查询补全", "fuzzy_total": _int(_safe_total(fuzzy)), "record": record}
    return {"keyword": raw, "enterprise": raw, "resolved": False, "reason": "模糊查询未命中企业全称，按关键词直查"}


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


def _derive_core_metrics(metrics: List[Dict[str, Any]], core: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Derive additional metrics from core analysis sections."""
    search = core.get("search_records", []) if isinstance(core, dict) else []
    stats = core.get("trademark_stats", {}) if isinstance(core, dict) else {}
    if isinstance(search, list) and search:
        try:
            categories = set(str(r.get("商标分类", "")) for r in search if r.get("商标分类"))
            if categories:
                metrics.append({"label": "商标分类数", "value": str(len(categories)), "hint": "不同国际分类数量"})
            statuses = set(str(r.get("商标状态", "")) for r in search if r.get("商标状态"))
            if statuses:
                metrics.append({"label": "商标状态数", "value": str(len(statuses)), "hint": "不同商标状态数量"})
        except Exception:
            pass
    return metrics


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
    invalid_n = _int(p.get("tmInvalidNumber"))
    metrics.append({"label": "商标总数", "value": _text(p.get("tmCount")) or "-", "hint": "企业商标总量"})
    if valid_n is not None and total_n:
        share = valid_n / total_n * 100
        metrics.append({"label": "有效商标", "value": _text(p.get("tmValidNumber")) or "-", "hint": "当前有效商标数", "delta": f"有效率 {share:.0f}%"})
    else:
        metrics.append({"label": "有效商标", "value": _text(p.get("tmValidNumber")) or "-", "hint": "当前有效商标数"})
    metrics.append({"label": "无效商标", "value": _text(p.get("tmInvalidNumber")) or "-", "hint": "无效商标数"})
    metrics.append({"label": "近一年申请", "value": _text(p.get("tmNumberThisYear")) or "-", "hint": "最近一年申请商标数"})
    metrics.append({"label": "类别覆盖", "value": (str(len(p.get("tmTypeList") or [])) + " 类" if isinstance(p.get("tmTypeList"), list) else "-"), "hint": "涵盖商标类别数"})
    metrics.append({"label": "检索结果", "value": str(search_total or "0") + " 条", "hint": "本次检索命中商标条数"})
    # 商标注册成功率 = sum(tmRegTimeStat counts) / sum(tmAppTimeStat counts) * 100
    app_total = _sum_stat_counts(stats, "tmAppTimeStat")
    reg_total = _sum_stat_counts(stats, "tmRegTimeStat")
    if app_total > 0:
        success_rate = reg_total / app_total * 100
        metrics.append({"label": "注册成功率", "value": f"{success_rate:.0f}%", "hint": f"注册数/申请数（{reg_total}/{app_total}）", "delta": f"申请 {app_total}、注册 {reg_total}"})
    return [m for m in metrics if m.get("value") not in ("", None, "-")]


def build_caliber(subject: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "match_target": subject.get("enterprise") or subject.get("match_raw"),
        "match_type": f"商标概况按企业主体匹配（keywordType={subject.get('keywordType', 'name')}）；检索支持商标名称/申请号/申请人/代理机构",
        "data_scope": "商标概况、商标检索明细、商标申请/注册趋势、状态分布、类别分布",
        "products": ["商标检索", "商标概况", "商标趋势统计"],
        "limit": "数据来自商标公开数据库；少量字段可能存在更新延迟。",
    }


def _stat_rows(stats: Mapping[str, Any], key: str, label_key: str, count_key: str = "count", year_key: str = "year") -> List[Dict[str, Any]]:
    rows = []
    for item in _first_list(stats.get(key)):
        if not isinstance(item, dict):
            continue
        label = item.get(label_key) or item.get(year_key) or item.get("tmStatus") or item.get("tmName") or "-"
        rows.append({
            "名称/类别/年份": _text(label),
            "数量": _text(item.get(count_key) or item.get("value") or "-"),
        })
    return rows


def build_core_analysis(profile: Mapping[str, Any], search: Any, stats: Mapping[str, Any]) -> Dict[str, Any]:
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
        if not isinstance(item, dict):
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
        {"key": "search_records", "title": "商标检索明细", "kind": "table", "note": f"本次检索命中 {total if total is not None else '若干'} 条，展示前 {len(search_rows)} 条",
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


def _trend_analysis(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Compute trend direction, peak, and YoY change from a {period,count} series."""
    nums = []
    for r in rows:
        try:
            nums.append(float(str(r.get("数量", 0)).replace(",", "")))
        except (TypeError, ValueError):
            nums.append(0.0)
    if not nums:
        return {}
    peak_idx = max(range(len(nums)), key=lambda i: nums[i])
    direction = "持平"
    yoy = ""
    if len(nums) >= 2:
        last, prev = nums[-1], nums[-2]
        if prev > 0:
            pct = (last - prev) / prev * 100
            if pct > 5:
                direction = f"上升 {pct:.0f}%"
            elif pct < -5:
                direction = f"下降 {abs(pct):.0f}%"
            yoy = f"同比 {pct:+.0f}%"
    return {"peak_period": rows[peak_idx].get("名称/类别/年份", "-"), "peak_value": nums[peak_idx], "direction": direction, "yoy": yoy, "last": nums[-1]}


def _concentration(rows: List[Mapping[str, Any]], top_n: int = 3) -> Dict[str, Any]:
    """Compute top-N concentration (CRn) and dominant category."""
    items = []
    for r in rows:
        try:
            items.append((r.get("名称/类别/年份", "-"), float(str(r.get("数量", 0)).replace(",", ""))))
        except (TypeError, ValueError):
            items.append((r.get("名称/类别/年份", "-"), 0.0))
    total = sum(v for _, v in items)
    if not total:
        return {}
    items.sort(key=lambda x: x[1], reverse=True)
    cr = sum(v for _, v in items[:top_n]) / total * 100
    return {"top": items[0][0], "top_share": items[0][1] / total * 100, "cr": cr, "total": total}


def _distribution_evenness(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Assess whether category distribution is even (uniform/defensive) vs concentrated.
    Uses coefficient of variation (CV = std/mean); CV low => evenly spread."""
    nums = []
    for r in rows:
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


def build_insights(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    insights: List[Dict[str, Any]] = []
    metric_map = {m["label"]: str(m["value"]) for m in metrics}
    tm_count = metric_map.get("商标总数")
    valid = metric_map.get("有效商标")
    invalid = metric_map.get("无效商标")
    recent = metric_map.get("近一年申请")

    if tm_count:
        insights.append({
            "feature": "商标资产规模",
            "evidence": f"企业商标总数 {tm_count} 件。",
            "interpretation": "商标总量反映企业品牌布局广度；结合有效/无效比例可评估商标资产健康度。",
        })
    if valid or invalid:
        try:
            v_n = float(valid) if valid else 0.0
            i_n = float(invalid) if invalid else 0.0
            denom = v_n + i_n
            ratio = f"有效率 {v_n / denom * 100:.0f}%" if denom else ""
        except (TypeError, ValueError):
            ratio = ""
        evidence = "、".join(p for p in (f"有效商标 {valid}" if valid else None, f"无效商标 {invalid}" if invalid else None) if p) + "。"
        insights.append({
            "feature": "商标有效性",
            "evidence": evidence + (f"（{ratio}）" if ratio else ""),
            "interpretation": "有效占比高说明商标维护较好；无效较多可能源于未续展、被驳回或被无效宣告，建议结合状态分布进一步核查。",
        })
    type_stat = core.get("type_stat") or []
    if type_stat:
        evenness = _distribution_evenness(type_stat)
        if evenness and evenness.get("even"):
            # Evenly spread → 均匀布局/防御性注册
            insights.append({
                "feature": "类别均匀布局",
                "evidence": f"覆盖 {evenness['count']} 个类别，各类别数量接近（均值 {evenness['mean']:.1f}，变异系数 {evenness['cv']:.2f}），分布均匀。",
                "interpretation": "类别分布均匀（低方差）通常意味着企业采取防御性注册或多品牌矩阵策略，覆盖多个业务线以防止傍牌与跨类侵权，而非聚焦单一赛道。",
            })
        else:
            conc = _concentration(type_stat, 3)
            if conc:
                insights.append({
                    "feature": "类别集中度",
                    "evidence": f"“{conc['top']}”占比约 {conc['top_share']:.0f}%，前 3 类合计 {conc['cr']:.0f}%（CR3）。",
                    "interpretation": "类别集中度反映企业核心业务领域与品牌防御性注册策略；CR3 越高说明品牌布局越聚焦核心赛道，反之则业务面更宽。",
                })
    status_stat = core.get("status_stat") or []
    if status_stat:
        conc = _concentration(status_stat, 2)
        if conc:
            insights.append({
                "feature": "商标状态结构",
                "evidence": f"“{conc['top']}”为主流状态，占比约 {conc['top_share']:.0f}%。",
                "interpretation": "已注册占比高说明商标资产稳定；申请中较多说明近期品牌布局活跃；无效/驳回较多则提示商标维护存在风险。",
            })
    apply_trend = core.get("apply_trend") or []
    if apply_trend:
        ta = _trend_analysis(apply_trend)
        if ta:
            insights.append({
                "feature": "申请趋势研判",
                "evidence": f"峰值出现在“{ta['peak_period']}”（{ta['peak_value']:.0f} 件），近年趋势{ta['direction']}，{ta.get('yoy', '')}。",
                "interpretation": "申请量上升表明品牌投入持续加码、新业务商标布局活跃；下降可能意味着品牌矩阵阶段性成熟或投入收缩，需结合业务周期判断。",
            })
    reg_trend = core.get("reg_trend") or []
    if reg_trend:
        ta = _trend_analysis(reg_trend)
        if ta and ta.get("direction") != "持平":
            insights.append({
                "feature": "注册趋势研判",
                "evidence": f"注册峰值在“{ta['peak_period']}”（{ta['peak_value']:.0f} 件），近年{ta['direction']}。",
                "interpretation": "注册趋势反映商标被核准授权的节奏；注册量稳步增长通常意味着品牌权利持续稳固。",
            })
    # 品牌布局停滞预警：近一年申请为 0
    try:
        this_year_n = int(float(str(recent))) if recent is not None else None
    except (TypeError, ValueError):
        this_year_n = None
    if this_year_n == 0:
        insights.append({
            "feature": "品牌布局停滞",
            "evidence": "近一年（tmNumberThisYear）新申请商标数为 0。",
            "interpretation": "近一年无新增商标申请，提示品牌布局可能停滞；需结合业务扩张节奏判断——若处于新业务/新品发布期却无对应商标布局，存在品牌保护滞后与被抢注风险。",
        })
    if not insights:
        insights.append({
            "feature": "数据完整性",
            "evidence": "部分维度未返回有效数据。",
            "interpretation": "建议核对匹配关键词是否为企业全称，或检查 MCP 连接与上游数据产品覆盖范围。",
        })
    return insights


def build_abstract(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: List[Mapping[str, Any]]) -> str:
    name = subject.get("enterprise") or subject.get("match_raw") or "目标企业"
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
    return _assemble(subject, core, metrics, dry_run=True)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def _assemble(subject: Mapping[str, Any], core: Mapping[str, Any], metrics: List[Mapping[str, Any]], *, dry_run: bool) -> Dict[str, Any]:
    abstract = build_abstract(subject, core, metrics)
    records = build_records(core)
    insights = build_insights(subject, core, metrics)
    # Quality gate: count populated core-analysis sections.
    ca = core if isinstance(core, dict) else {}
    secs = ca.get("sections", [])
    if secs:
        total_secs = len(secs)
        populated = sum(1 for s in secs if isinstance(s, dict) and ca.get(s.get("key")) not in (None, "", [], {}))
    else:
        total_secs = max(1, len([k for k in ca if k != "sections"]))
        populated = sum(1 for k in ca if k != "sections" and ca.get(k) not in (None, "", [], {}))
    quality_report = {
        "total_sections": total_secs,
        "populated_sections": populated,
        "empty_sections": total_secs - populated,
        "coverage_pct": round(populated / max(1, total_secs) * 100),
    }
    if populated == 0:
        import sys
        print("⚠️ 质量门禁警告: 所有核心分析维度均无数据", file=sys.stderr)
    title = f"{subject.get('enterprise') or '目标企业'} 商标大数据报告"
    return {
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
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "dry_run": dry_run,
            "quality_report": quality_report,
        },
    }


def build_payload(raw: str, keyword_type: str, tm_status: Optional[str], page_size: int) -> Dict[str, Any]:
    resolved = resolve_enterprise_name(raw)
    enterprise = resolved["enterprise"]
    # Trademark detail tools use matchKeyword (full name) + keywordType.
    mk_args: Dict[str, Any] = {"matchKeyword": enterprise, "keywordType": keyword_type}
    search_args: Dict[str, Any] = {"matchKeyword": enterprise, "keywordType": keyword_type, "pageIndex": 1, "pageSize": page_size}
    if tm_status:
        search_args["tmStatus"] = tm_status
    profile = _safe_call(T_PROFILE, mk_args)
    stats = _safe_call(T_STATS, mk_args)
    search = _safe_call(T_SEARCH, search_args)
    search_total = _safe_total(search) if isinstance(search, dict) else None

    subject = build_subject(raw, resolved, keyword_type)
    core = build_core_analysis(profile, search, stats)
    metrics = build_metrics(profile if isinstance(profile, dict) else {}, search_total, stats if isinstance(stats, dict) else {})
    _derive_core_metrics(metrics, core if isinstance(core, dict) else {})
    # --- Enterprise profile enrichment (from fuzzy_search) ---
    _enrich_metrics_with_profile(metrics, resolved.get("record") if isinstance(resolved, dict) else None)
    return _assemble(subject, core, metrics, dry_run=False)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Compose a trademark big-data report via the trademark MCP.")
    parser.add_argument("--enterprise", required=True, help="企业全称或关键词（关键词将自动模糊补全）")
    parser.add_argument("--keyword-type", default="name", help="主体类型：name/nameId/regNumber/socialCreditCode")
    parser.add_argument("--tm-status", default=None, help="可选商标状态过滤（如 商标已注册 / 商标申请中 / 初审公告 等）")
    parser.add_argument("--page-size", type=int, default=10, help="商标检索明细分页大小（最多 50）")
    parser.add_argument("--dry-run", action="store_true", help="不调用真实 MCP，使用样例数据组装报告骨架")
    parser.add_argument("--output", help="输出 JSON 路径；省略则打印到 stdout")
    parser.add_argument("--report-output", help="同时输出 HTML 报告（.html）与 Markdown 报告（.md）")
    parser.add_argument("--pdf-output", help="额外输出 PDF 报告（.pdf）；需要 Playwright + Chromium")
    args = parser.parse_args()

    if args.dry_run:
        payload = build_dry_run_payload(args.enterprise, args.keyword_type)
    else:
        payload = build_payload(args.enterprise, args.keyword_type, args.tm_status, args.page_size)

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
