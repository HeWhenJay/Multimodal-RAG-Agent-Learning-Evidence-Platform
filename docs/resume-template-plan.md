# 简历模板字段级内容补丁与确定性 DOCX 应用方案

更新日期：2026-06-22

## 目标

实现“字段级内容补丁生成 + 人工确认 + 确定性 DOCX 应用”的简历模板能力：用户上传受控 DOCX 简历后，系统解析出可修改字段；模型只生成严格 JSON Schema 约束的内容补丁；用户确认后由 Python 只改写既有 `w:r/w:t` 文本节点并导出新版本，不覆盖原文件，不让模型接触或修改排版结构。

当前阶段仍只属于 RAG 闭环，不实现 Agent 编排、长任务调度、自主规划或工具调用。

## 核心判断

Structured Outputs / JSON Schema 的价值是把 LLM 限制为“字段级内容补丁生成器”，降低自由生成 DOCX、Markdown 或排版字段的风险。它不能直接保证 DOCX 排版不变；排版保护必须由确定性 DOCX 修改算法、人工确认、版本校验和版式 fingerprint 回归校验共同完成。

## 硬边界

- 不新增、不复用、不扩展 `ai-python/agent/`、`/internal/agent/*`、`docs/api/agent.md`、`docs/agent/`、Java `Agent*`、`PythonAgentClient`、`AgentToolGatewayService*`、Agent 表、LangGraph 或 Tool Gateway。
- Python 新能力放在 `ai-python/rag/resume_template/`、`ai-python/app/schemas/resume_template.py`，内部接口挂在 `/internal/rag/resume/templates/*`。
- Java 只负责登录用户、资源归属、状态机、数据库记录、统一 `Result<T>` 和调用 Python，不直接实现 LLM 改写逻辑。
- React 只调用 Java `/api/rag/resume-templates/*`，不得直连 Python。

## 核心模型

### ResumeTemplateBinding

字段绑定由 Python DOCX 解析器生成，Java 持久化后返回给前端。

| 字段 | 说明 |
| --- | --- |
| `templateId` | 模板 ID |
| `version` | 模板版本，导出后递增 |
| `fieldId` | 字段 ID |
| `sectionKey` | 枚举：`personal_info/summary/education/work_experience/project_experience/skills/awards/certifications/research/other` |
| `displayName` | 前端展示名 |
| `sourceText` | 字段原文 |
| `sourceTextHash` | 原文 hash，用于版本冲突和覆盖保护 |
| `locationRefs` | 只由解析器生成，记录正文段落或表格单元格定位 |
| `styleFingerprint` | 段落和 run 样式摘要 |
| `maxChars/maxLines` | 文本长度和行数限制 |
| `requiredEvidencePolicy` | `NONE/OPTIONAL/REQUIRED` |
| `unsupportedRegions` | 当前字段命中的复杂结构或不支持原因 |

`locationRefs` 禁止由 LLM 输出或修改，前端也不允许提交自定义定位。

### ResumeContentPatch

模型只能输出以下字段：

| 字段 | 说明 |
| --- | --- |
| `fieldId` | 目标字段 |
| `sourceTextHash` | 生成时看到的原文字段 hash |
| `newText` | 新字段内容，纯文本 |
| `rewriteReason` | 改写原因 |
| `evidenceIds` | 使用的候选 evidence ID |
| `confidence` | 置信度 |
| `riskFlags` | `NONE/MISSING_EVIDENCE/LOW_CONFIDENCE/OVER_LENGTH/LAYOUT_RISK/SENSITIVE_INFO/UNSUPPORTED_REGION/INJECTION_RISK` |
| `status` | `DRAFT/VALIDATED/CONFIRMED/REJECTED/EXPORTED` |

JSON Schema 必须启用 strict：所有字段 required，所有 object `additionalProperties:false`，枚举字段用 enum。Schema 禁止 `style/font/layout/xml/path/locationRefs/run/paragraph/table/cell/header/footer` 等排版字段。长度限制在 schema、Pydantic 和 Java 三层校验。

## DOCX 修改与版式保护

- 禁止使用 `paragraph.text = ...`、`cell.text = ...` 等会重建 run 或样式的 API。
- 只允许修改既有 `w:r/w:t` 文本节点，不新增、删除、重排 paragraph/table/run。
- 首版跨 run 字段保守拒绝自动修改，返回 `UNSUPPORTED_REGION` 或校验错误。
- 超链接、域代码、目录、批注、修订痕迹、脚注尾注、文本框/形状、SmartArt、页眉页脚等复杂结构进入 `unsupportedRegions`，导出时拒绝自动修改。
- 应用前后生成 layout fingerprint，校验段落数、表格数、run 数、媒体文件列表、document relationships 和清空正文后的 XML 结构 hash 不变。
- 任一校验失败返回 `RESUME_LAYOUT_CHANGED` 或对应验证错误，拒绝导出。
- 若本地 LibreOffice/渲染工具不可用，XML fingerprint 是最低验收；视觉完全不变仍需 DOCX 转 PDF/图片渲染对比补充验证。

## 流程

1. Java 接收登录用户上传的 DOCX，保存为受控资源。
2. Java 读取受控资源字节，调用 Python `/internal/rag/resume/templates/parse`。
3. Python 解析字段绑定、hash、style/layout fingerprint 和 `unsupportedRegions`。
4. Java 持久化模板、字段和 fingerprint，返回字段列表。
5. 用户输入 JD 后，Java 基于当前用户 RAG 检索 evidence 候选，不写查询历史。
6. Java 调用 Python `/internal/rag/resume/templates/patches/generate`。OpenAI 可用时走 Structured Outputs；百炼兼容路径使用 JSON 输出 + Pydantic/JSON Schema 校验，失败则返回安全草稿或拒绝。
7. Java 保存补丁草稿，前端展示原文、新文本、证据和风险。
8. 用户逐条确认或拒绝补丁。
9. Java 校验版本、归属、状态和幂等键后调用 Python `/internal/rag/resume/templates/exports`。
10. Python 确定性应用补丁，layout fingerprint 通过后返回新 DOCX 字节。
11. Java 保存导出文件为新版本，记录导出结果，不覆盖原始文件。

## 数据库

只新增 RAG 简历模板表，不新增或复用任何 `agent_*` 表。

- `resume_template`：用户、原始文件资源、导出文件资源、文件类型、版本、状态、layout fingerprint。
- `resume_template_field`：字段绑定、hash、locationRefs、styleFingerprint、限制和 unsupported 状态。
- `resume_template_patch_draft`：补丁草稿、状态机、证据引用、校验结果、版本。
- `resume_template_export`：导出文件位置、基于版本、使用的 patch draft、layout 校验结果、幂等键。

导出接口必须校验幂等键和版本冲突，避免重复生成和覆盖。

## 测试验收

Python 测试覆盖：

- schema validation、未知 `fieldId`、hash mismatch、evidence 缺失、超长文本。
- 排版字段注入、Markdown/HTML/XML 注入。
- 跨 run 拒绝、表格单元格替换、复杂结构 `unsupportedRegions`。
- 应用前后 layout fingerprint 变化拒绝。

Java 测试覆盖：

- 鉴权和资源归属。
- Python client 错误映射。
- `Result<T>` 返回。
- 幂等、版本冲突、patch 状态机。
- 人工确认前不允许导出。

前端验收：

- 展示待确认、风险、校验失败、版本冲突和导出成功。
- 构建通过。

必跑命令：

```powershell
conda run -n learning-evidence-rag python -B -m pytest ai-python/tests -q
cd backend-java; mvn test
cd frontend-react; npm run build
```
