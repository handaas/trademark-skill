---
name: trademark-report
description: Use for generating a professional trademark big-data report (商标大数据报告) from the HandaaS trademark MCP — covering 商标检索明细、商标概况、商标申请/注册趋势、状态分布、类别分布. Trigger when users ask for “商标大数据报告”, “商标分析报告”, “查一家公司的商标”, “商标检索”, “商标概况”, “商标趋势”, “商标类别分布”, or “企业商标画像”. Infer the canonical enterprise name (auto-fuzzy-completing a keyword), pick the right MCP tools, and produce HTML + Markdown + JSON reports automatically. MCP 服务名称触发词：商标注册信息。
---

# 商标大数据报告

## 用户契约

把“商标大数据报告”作为面向用户的调用短语。`trademark-report` 仅为内部包名。

当本 skill 处于激活状态：

1. 不要向用户索要 product_id、MCP 工具名、API 字段、内部参数或凭证信息；只接受企业名称、统一社会信用代码、注册号或企业 ID。
2. 接受自然目标，例如“查一下某某公司的商标”“分析这家企业的商标布局”“给我一份某某公司的商标概况报告”“看看这家公司商标注册趋势”。
3. 当用户只给关键词时，自动调用关键词模糊查询补全企业全称，再查商标详情。
4. 优先使用 MCP 连接（`TRADEMARK_MCP_URL` Remote MCP 或本地 `handaas-mcp-server/trademark-mcp-server`）；不要让用户处理签名或凭证。
5. 同时产出 HTML（可分享交付）、Markdown（知识库 / wiki）、JSON（系统集成）三类产物。
6. 报告正文必须是专业研究报告风格：只见商标事实与结构化数据，绝不出现工具名、入参、product_id、内部字段或空表。
7. 绝不打印 `secret_id`、`secret_key`、签名、token 或原始签名请求。
8. 默认 dry-run；真实付费 / 凭证调用需用户明确要求且 MCP 连接配置完整。
9. 数据为空时明确说明数据范围 / 口径，不渲染空表、不臆造事实。


- MCP 返回的嵌套 JSON 字符串（如金额 `{"coinType":"人民币","value":430000000.0}`、地址 `{"city":"杭州市",...}`）必须解析为可读文本（如"4.30 亿 人民币"、"浙江省杭州市"），绝不在报告正文、表格或指标中输出原始 JSON 字符串。
- 报告所有章节标题、指标卡标签必须用中文；`core_analysis.sections` 的 `title` 字段必须中文，不可显示英文 key（如 `holders`、`investments`）。
- 指标值必须可读化：金额格式为"X 亿/万 + 币种"，地址拼接省市区，比率显示百分号。详见 `references/report-output.md` 的「数据格式约束」。

## 内容整理与交付审校（强制）

1. **阶段分离**：申请、初审公告、注册、无效是不同阶段。申请中或初审公告不得写成“已注册”；“商标已注册”只说明当前状态，不等于权利无争议。
2. **去重规则**：同一商标名跨不同国际分类是独立申请，不得按名称去重；仅“同一申请号/注册号 + 同一类别”可判重复。检索明细是当前页样本，不能据此生成全量“状态数/分类数”。
3. **类别口径**：类别用 1–45 类规范名；上游“其他”是未细分聚合，不得当作一个类别参与集中度或“核心类别”判断。已按类别/状态筛选时，不再把筛选后的 100% 分布当结构结论。
4. **趋势只比完整年度**：当前年度为年度迄今数据，不参与峰值/同比；近两年任一不足 10 件时只列计数，不作升降判断；“注册/申请量比”是不同年份总量之比，不是成功率。
5. **证据边界**：有效/无效、类别集中度、状态结构只描述当前返回口径；形成原因须回到续展、驳回、撤销、无效宣告记录。不得由商标数量推断品牌价值或经营状况。
6. 保留原始状态、类别、申请号供核对；正常生成与离线修订均调用 `apply_content_review(report)`，只使用报告既有字段。

## 付费调用与分页预算（强制）

每次 MCP 业务调用和每一页明细请求都可能计费；希望得到 total / 页数计算**不是**全量拉取的授权。

1. **先计划再调用**：调用前先推导本次报告必需的结论、需要的数据维度、工具与参数方案、所需页数和最大调用预算。上游 total 未知时，第一个必要的明细页同时用于发现 total 与报告数据，绝不为此页重复付费。
2. **优先统计 / 概况接口**：当结论可由概况、统计等聚合接口回答时优先使用它们；仅在结论、图表、代表性记录或用户明确要求全量/计数时才拉取明细。只有在用户确实要求综合报告时才跑完整流水线，绝不因为工具存在就全部调用。
3. **预算内取数**：使用 schema 允许的合理最大 pageSize 获取所需行数，达到证据/样本目标即停止，且始终在显式预算内。额外翻页必须有缺失结论的正当理由，而不是“想填满所有章节”。不得用样本分布冒充全量结论。
4. **复用相同请求**：同一任务/报告内相同的工具+参数只调用一次（含首屏规划数据）；渲染只用本地已保存 JSON 重渲，不重跑 compose/MCP。禁止自动重试、重复拉取相同统计/明细、并行拉取相同数据。补充查询前先列出具体缺口并复用已返回记录。
5. **总数与完整性口径**：`total_pages` / `remaining_pages` 只是按请求 pageSize 的估算；短而未完的页在预算内要继续翻。仅当稳定有效的 total 与实扫条数一致才算完整采集；报错、提前空页、重复页、total 变化/缺失或数量溢出均视为不完整。因显式预算或样本目标提前停止的都是部分采集，绝不宣称全量数据。
6. **预算不足要说清缺口**：若预算不足以支撑某个关键结论，在申请扩大授权前先说明证据缺口与预计的额外调用数；不得静默降级结论，也不得擅自扩大计费范围。

本 skill 分页明细：仅 `trademark_bigdata_trademark_search`（商标检索明细，pageIndex/pageSize，单页最多 50）支持分页采集，CLI `--max-pages`（默认 1）为其显式页数预算；概况（profile）与统计（stats）为聚合接口、关键词模糊查询仅用于主体确认，均保持单次调用、不分页遍历。

## MCP 服务入口

- MCP 服务当前名称：商标注册信息（连接仍使用原服务目录标识）。

- 上游 MCP 项目：`handaas-mcp-server/trademark-mcp-server`（位于 `HANDAAS_MCP_SERVER_ROOT` 或本仓库同级目录）。
- Remote MCP：设置环境变量 `TRADEMARK_MCP_URL`（streamable-http），可选 `TRADEMARK_MCP_TOKEN`。
- 本地 MCP：设置 `HANDAAS_MCP_SERVER_ROOT` 指向 `handaas-mcp-server` 仓库根目录；该 server 自己的 `.env` 提供 `INTEGRATOR_ID` / `SECRET_ID` / `SECRET_KEY`。
- 首次真实查询前，运行 `scripts/mcp_client.py ping` 与 `scripts/mcp_client.py list-tools` 验证连通。

## 按需加载 references

- 不清楚该 MCP 有哪些工具、参数、返回字段、何时调用：`references/mcp-tools-reference.md`。
- 报告结构、章节、质量底线、渲染工作流：`references/report-output.md`。

## 意图路由

| 用户意图 | 内部工作流 |
| --- | --- |
| 查一家公司的全维度商标报告 | 调商标概况 + 趋势 + 检索组装全量报告；`compose_report.py --enterprise ...` |
| 只要商标概况 / 趋势 / 类别分布 | 仅调对应工具，按统一骨架组装 |
| 按商标状态过滤（如已注册 / 申请中） | `compose_report.py --tm-status ...` |
| 按商标名称 / 申请号 / 代理机构检索（非企业主体） | 用 `trademark_bigdata_trademark_search`，`keywordType` 设为对应枚举 |
| 只给关键词（不是全称） | 先 `trademark_bigdata_fuzzy_search` 补全全称，再查详情 |
| 只要 JSON / 只要 HTML / 只要 Markdown | 用 `--output`（JSON）或 `--report-output`（HTML+MD），或 `render_report.py` 重渲染 |
| 连接 / 工具不存在 / 传参错误 | `mcp_client.py ping` / `list-tools` 排查；报脱敏后的缺失项 |

## Golden path for 商标大数据报告

1. **解析企业全称**：若输入含“公司/集团/有限/院/厂/中心/事务所/合作社/合伙”等后缀视为全称；否则调 `trademark_bigdata_fuzzy_search` 取首个命中。
2. **调用商标工具**：`trademark_bigdata_trademark_profile`（概况：总数/有效/无效/近一年/类别/状态）、`trademark_bigdata_trademark_stats`（申请趋势/注册趋势/状态统计/类别统计）、`trademark_bigdata_trademark_search`（明细）。入参为 `matchKeyword`（企业全称）+ `keywordType`。
3. **组装统一报告**：核心分析含商标概况（KV）、申请趋势、注册趋势、状态分布、类别分布、商标检索明细（表）。
4. **渲染三件套**：`compose_report.py --enterprise ... --output ... --report-output ...` 直接产出 JSON + HTML + Markdown；或 `render_report.py --input ... --output ...` 重渲染。
5. **返回路径**：返回 JSON、HTML、Markdown 文件路径，以及企业全称映射与数据口径。

- **渲染前置检查**：执行会产出含图表 HTML / PDF 的报告命令前，先验证 `node --version` 与本 skill 自带 `assets/vendor/echarts.min.js` 存在；内嵌静态 SVG 的生成需要 Node.js（推荐当前 LTS，无需 npm 包），缺失时报告生成明确报错。查看已生成的 HTML 与 Markdown 输出不需要 Node。

## 脚本速查

```bash
# 校验连接配置（脱敏）
python scripts/validate_config.py --allow-placeholders

# 连通性自测
python scripts/mcp_client.py ping
python scripts/mcp_client.py list-tools

# 干跑（不调真实 API，用样例数据组装报告骨架）
python scripts/compose_report.py \
  --enterprise "示例科技有限公司" \
  --dry-run \
  --output output/trademark.json \
  --report-output output/trademark.html

# 真实查询 + 渲染（需 MCP 连接就绪）
python scripts/compose_report.py \
  --enterprise "示例科技有限公司" \
  --output output/trademark.json \
  --report-output output/trademark.html

# 按商标状态过滤
python scripts/compose_report.py --enterprise "示例科技有限公司" --tm-status 商标已注册 --report-output output/tm_registered.html

# 明细页数预算：默认仅 1 页；先按报告结论计划所需页数，再显式授权扩大
python scripts/compose_report.py --enterprise "示例科技有限公司" --page-size 50 --max-pages 3 --report-output output/tm.html

# 手动调单个工具
python scripts/mcp_client.py call-tool \
  --tool trademark_bigdata_trademark_profile \
  --arguments-json '{"matchKeyword": "示例科技有限公司", "keywordType": "name"}'

# 重渲染已有 JSON
python scripts/render_report.py --input output/trademark.json --output output/trademark.html
python scripts/render_report.py --input output/trademark.json --output output/trademark.md
```

## 输出字段

- `subject`：企业全称、匹配关键词、主体类型、是否自动补全。
- `abstract` / `summary`：封面摘要与详细摘要。
- `metrics`：商标总数、有效商标、无效商标、近一年申请、类别覆盖、检索结果数。
- `caliber`：匹配对象、匹配方式、数据范围、产品、局限。
- `core_analysis`：商标概况（KV）、申请趋势（表）、注册趋势（表）、状态分布（表）、类别分布（表）、商标检索明细（表）。
- `representative_records`：代表性商标记录（名称 / 类别 / 申请日期 / 状态）。
- `insights`：结构化解读（资产规模 / 有效性 / 近期活跃度 / 类别集中度）。
- `data_source`：MCP server、数据产品、生成时间、是否 dry-run。

若 API 调用失败，明确报出缺失的配置 / 缺失的工具 / MCP 错误 / 参数校验错误 / 上游网络错误，给出 dry-run 命令或配置步骤，绝不暴露密钥。
