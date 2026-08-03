# 复习 Knowledge Curator × LangExtract A/B 方案与结果

更新日期：2026-08-04

## 目的

验证官方 [Google LangExtract](https://github.com/google/langextract) 的长文分块、多轮抽取和原文定位能力，是否能显著改善视频复习知识点遗漏；在实验通过预注册门槛前，不替换生产复习生成链路。

## 公平性约束

- A 臂：当前生产 `KnowledgePointExtractor` 与现有 DeepSeek 卡片质量门禁。
- B 臂：官方 `langextract==1.6.x`，通过其 OpenAI provider 调用同一 `deepseek-v4-flash`。
- 两臂读取同一数据库 evidence 快照，每份资料最多 8 次模型请求。真实运行时 A 臂显式超时为 120 秒，LangExtract 1.6 官方 OpenAI provider 没有向 `lx.extract` 暴露客户端 timeout，B 臂沿用了 OpenAI SDK 默认超时；实验结束后适配器已补为与 A 臂相同的 120 秒，回归测试锁定该默认值。
- B 臂使用 `UnicodeTokenizer`、`max_char_buffer=8000`、`extraction_passes=2` 和最多 2 个并发 worker。
- 只有 `char_interval` 能逐字回指输入正文并映射到真实 `evidenceId` 的 B 臂结果才进入最终候选。
- 人工金标知识项冻结在 `review-curator-ab-cases.jsonl`；首次运行后只把复合知识项的单串别名匹配修正为“必需关键词组可分布在多条候选中”，没有增加、删除或替换金标知识项。指标离线确定性计算，不增加一个可能偏向任一臂的裁判模型。
- 真实响应的请求数和 usage Token 只作成本审计，不保存提示词、密钥或用户完整原文。

## 预注册生产启用门槛

- 两类资料平均知识点召回率绝对提升至少 10 个百分点。
- 每份资料的 LangExtract 原始结果 evidence 映射成功率不低于 95%。
- 过滤后无支撑候选率不得高于当前方案。
- 最终候选近重复率不得高于当前方案。
- 总 Token 成本不超过当前方案的 1.5 倍。
- 陈述式课程和结构化问题资料均不得明显退化。

## 真实 A/B 结果

数据来自本机 PostgreSQL 中两份用户已报告遗漏的真实视频资料；模型运行产物写入忽略目录 `tmp/review-curator-ab/`，仓库仅保存脱敏汇总。

| 资料 | A 召回 | B 召回 | A/B 请求 | A/B Token | A/B 耗时 | B 原始定位 | A/B 近重复 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Kafka 高性能陈述式课程，10 个金标 | 70% | 100% | 1 / 2 | 12,180 / 36,856 | 78.7s / 178.2s | 92.0% | 0% / 0% |
| Python 基础面经 20 项目录 | 25% | 90% | 5 / 8 | 89,174 / 272,688 | 287.5s / 914.0s | 62.8% | 0% / 12.4% |
| 两类平均或合计 | 47.5% | 95.0% | 6 / 10 | 101,354 / 309,544 | 366.2s / 1,092.2s | — | — |

核心结果：LangExtract 的平均召回率绝对提升 47.5 个百分点，证明它对陈述式知识和长视频目录均有明显覆盖收益；但 Token 成本为当前方案的 3.05 倍，且长视频存在较多未精确定位输出和近重复候选。

实验局限：B 臂真实运行时尚未显式统一单请求超时，因此表中的耗时只能说明本次真实运行成本，不能作为严格的同超时延迟对比；召回、定位、重复、请求数和供应商 usage Token 均来自已完成响应，不受事后补齐 timeout 影响。本轮没有为修正超时重复消耗模型预算。

## 决策

本轮不把 LangExtract 直接接入生产复习卡片生成链路，因为 evidence 映射、近重复和成本三个门槛均未通过。仓库保留官方依赖、严格定位适配器、冻结金标、A/B runner 和离线测试，作为下一轮优化的可复现基线。

后续若继续优化，应优先测试单 pass、按章节自适应分块、topic 级合并和语义近重复过滤。只有重新运行相同冻结集并通过全部门槛后，才允许把 LangExtract 放在 Curator 候选发现阶段；即使启用，也不能绕过现有卡片问题、答案、evidence 忠实度和 FSRS 写入门禁。

## 复现

```powershell
$env:PYTHONPATH='ai-python'
conda run -n learning-evidence-rag python -B ai-python/rag/evaluation/run_review_curator_ab.py `
  --max-requests 8 `
  --extraction-passes 2 `
  --max-char-buffer 8000
```

运行需要只读 PostgreSQL、`DEEPSEEK_API_KEY` 和对应真实资料；结果默认写入已忽略的 `tmp/review-curator-ab/`。
