# trademark-skill：已验收示例

本目录只展示 **6 个完全通过的真实正向场景**（最低要求4例）。

每例均为真实、非空、非缓存调用；技术检查、独立业务检查和离线浏览器检查全部通过。不把空数据、报错处理或仅生成页面计作通过。历史验收记录保留在本地 test-records/；本目录的 HTML/Markdown 由原 JSON 重渲染，不代表重新查询或重新验收。

| 场景 | 真实使用需求 | 报告 |
|---|---|---|
| [互联网企业商标组合评估](01-brand-portfolio/README.md) | 分析阿里巴巴（中国）有限公司的商标概况、申请趋势、类别和状态。 | [HTML](01-brand-portfolio/report.html) / [MD](01-brand-portfolio/report.md) / [JSON](01-brand-portfolio/report.json) |
| [已注册商标抽样](02-registered-marks/README.md) | 抽取华为技术有限公司状态为商标已注册的商标样本。 | [HTML](02-registered-marks/report.html) / [MD](02-registered-marks/report.md) / [JSON](02-registered-marks/report.json) |
| [申请中商标跟踪](03-pending-marks/README.md) | 查看小米科技有限责任公司处于商标申请中的记录和类别分布。 | [HTML](03-pending-marks/report.html) / [MD](03-pending-marks/report.md) / [JSON](03-pending-marks/report.json) |
| [初审公告商标监测](04-prelim-announcement/README.md) | 检查北京京东世纪贸易有限公司处于初审公告阶段的商标样本。 | [HTML](04-prelim-announcement/report.html) / [MD](04-prelim-announcement/report.md) / [JSON](04-prelim-announcement/report.json) |
| [白酒品牌商标结构](05-consumer-brand/README.md) | 分析贵州茅台酒股份有限公司的商标类别集中度和状态分布。 | [HTML](05-consumer-brand/report.html) / [MD](05-consumer-brand/report.md) / [JSON](05-consumer-brand/report.json) |
| [商标快速抽样](07-small-sample/README.md) | 为比亚迪股份有限公司生成前 5 条商标明细快报。 | [HTML](07-small-sample/report.html) / [MD](07-small-sample/report.md) / [JSON](07-small-sample/report.json) |

- [逐例验收结果](results.json) · [业务复核](review.json) · [证据摘要](evidence-index.json)
- 完整测试记录仅存于本地 test-records/live-replaced-20261027/（仅本地审计用，不随本目录发布），不属于可展示示例。
- 当前数据与结论限于原查询时点及公开样本；覆盖率不代表准确率或低风险。
