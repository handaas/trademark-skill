# MCP 工具参考 — trademark-mcp-server

本 skill 连接的 MCP server：`handaas-mcp-server/trademark-mcp-server`（“商标大数据”）。

> **重要**：商标详情类工具（profile / stats）入参为 `matchKeyword`（**企业全称** / 注册号 / 统一社会信用代码 / 企业 id）+ `keywordType`；
> `trademark_search` 的 `matchKeyword` 可为商标名称 / 申请号 / 申请人 / 代理机构；当用户只给企业关键词时，必须先调关键词模糊查询补全全称。

## 通用约定

- `keywordType` 枚举：`name`（企业名称）/ `nameId`（企业 id）/ `regNumber`（注册号）/ `socialCreditCode`（统一社会信用代码）。
- 分页：`pageIndex` 从 1 开始；`pageSize` 单页最多 50。

---

## 工具清单

### 1. `trademark_bigdata_fuzzy_search` — 关键词模糊查询企业

用途：根据企业名称 / 人名 / 品牌 / 产品 / 岗位等关键词模糊查询企业列表，用于补全企业全称。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 匹配关键词 |
| `pageIndex` | int | 否 | 分页开始位置（默认 1） |
| `pageSize` | int | 否 | 单页最多 50 |

返回：`total` + 企业列表（`name`、`nameId`、`regCapitalValue`、`foundTime`、`operStatus`、`address`、`legalRepresentative`、`enterpriseType`、`catchReason` 命中原因等）。

product_id：`675cea1f0e009a9ea37edaa1`。

---

### 2. `trademark_bigdata_trademark_search` — 商标检索

用途：按商标名称 / 申请号 / 申请人 / 代理机构查询商标信息，可按商标状态过滤。适合商标查询、情况分析、状态跟踪。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 商标名称 / 申请号 / 申请人 / 代理机构 |
| `keywordType` | string | 否 | 搜索方式：商标名称 / 申请号 / 申请人 / 代理机构（默认匹配全部） |
| `tmStatus` | string | 否 | 商标状态枚举：驳回复审中 / 撤销/无效宣告申请审查中 / 初审公告 / 等待驳回复审 / 等待实质审查 / 商标申请中 / 商标无效 / 商标已注册 / 商标异议中 |
| `pageIndex` | int | 否 | 从 1 开始（默认 1） |
| `pageSize` | int | 否 | 单页最多 50（默认 10） |

返回（list + `total`）：`_id`、`tmName`（商标名称）、`tmRegNum`（申请号）、`tmCompanyName`（申请人）、`internationalClass` / `tmSingleInternationalClass`（国际分类）、`tmApplicationTime`（申请日期）、`tmRegTime`（注册日期）、`tmStatus`（商标状态）、`tmAgentName`（代理机构）、`tmImage`（图片链接）、`tmSpecialBeginDate` / `tmSpecialEndDate`（专用权起止）、`tmServiceContents`（商品服务项）等。

product_id：`66b485eadaf8c77fb249a3cc`。

---

### 3. `trademark_bigdata_trademark_profile` — 商标概况

用途：按企业主体返回商标总数、有效/无效数、近一年申请数、类别、状态列表。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id（无全称则先调 fuzzy_search） |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |

返回：`tmCount`（商标数量）、`tmValidNumber`（有效商标数）、`tmInvalidNumber`（无效商标数）、`tmNumberThisYear`（近一年申请数）、`tmTypeList`（涵盖类别 list）、`tmStatusList`（状态 list）、`tmStatusStat`（状态统计 dict）。

product_id：`671357d127ab3417e1f3f21b`。

---

### 4. `trademark_bigdata_trademark_stats` — 商标趋势统计

用途：返回商标申请趋势、注册趋势、状态统计、类别统计。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |

返回：`tmAppTimeStat`（申请趋势 list of {year,count}）、`tmRegTimeStat`（注册趋势 list of {year,count}）、`tmStatusStat`（状态统计 list of {tmStatus,count}）、`tmTypeStats`（类别统计 list of {tmName,count}）。

product_id：`66d5b7df537c3f61d646c2dc`。

---

## 推荐调用顺序（报告编排）

1. （若仅有关键词）`trademark_bigdata_fuzzy_search` → 取 `name` 作为全称。
2. `trademark_bigdata_trademark_profile` → 概况指标。
3. `trademark_bigdata_trademark_stats` → 申请/注册趋势 + 状态 + 类别统计。
4. `trademark_bigdata_trademark_search` → 检索明细（按需可加 `tmStatus` 过滤）。

> 单次报告通常调用 3-4 个工具；profile / stats / search 入参均为企业主体 `matchKeyword` + `keywordType`。
