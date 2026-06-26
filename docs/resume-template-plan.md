# 简历模板字段级内容补丁与确定性 DOCX 应用方案

更新日期：2026-06-24

## 目标

实现“模板上传与确认 + Agent 发起字段级内容补丁 + 人工确认 + 确定性 DOCX 应用”的简历模板能力：用户上传受控 DOCX 简历后，系统解析出内部可修改字段；模板页只负责历史模板选择、图片预览确认和区域约束保存，不在前端展示 Agent/Python 提取出的字段原文、定位和样式指纹；岗位 JD 与简历修改任务放在 Agent 工作台发起。模型只生成严格 JSON Schema 约束的内容补丁；用户确认后由 Python 按 `LayoutChangeContract` 执行和审计，不覆盖原文件，不允许模型直接接触或修改 DOCX 排版结构。

当前接口仍在 RAG 服务边界内，由 Java 统一鉴权、持久化和调用 Python；Agent 可以作为前端和任务编排入口复用这些接口。

## 核心判断

Structured Outputs / JSON Schema 的价值是把 LLM 限制为“字段级内容补丁生成器”，降低自由生成 DOCX、Markdown 或排版字段的风险。它不能直接保证 DOCX 排版不变；排版保护必须由确定性 DOCX 修改算法、人工确认、`LayoutChangeContract`、版本校验和版式 fingerprint 差异审计共同完成。

## 硬边界

- Python 新能力放在 `ai-python/rag/resume_template/`、`ai-python/app/schemas/resume_template.py`，内部接口挂在 `/internal/rag/resume/templates/*`。
- Java 只负责登录用户、资源归属、状态机、数据库记录、统一 `Result<T>` 和调用 Python，不直接实现 LLM 改写逻辑。
- React 只调用 Java `/api/rag/resume-templates/*`，不得直连 Python，也不得展示字段原文、locationRefs、styleFingerprint 或 layoutFingerprint。
- Agent 工作台可收集岗位 JD、选择简历模板和展示补丁确认；模板页不得再收集 JD 或让用户在模板页误以为已完成简历修改。

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

JSON Schema 必须启用 strict：所有字段 required，所有 object `additionalProperties:false`，枚举字段用 enum。Schema 禁止 `style/font/layout/xml/path/locationRefs/run/paragraph/table/cell/header/footer` 等排版字段。长度限制在 schema、Pydantic 和 Java 三层校验。用户明确要求加粗、增删段落或重排版时，不把排版字段混入 `ResumeContentPatch`，而是单独生成 `LayoutChangeContract` 供确定性应用和差异审计使用。

## DOCX 修改与版式保护

- 禁止使用 `paragraph.text = ...`、`cell.text = ...` 等会重建 run 或样式的 API。
- 只允许修改既有 `w:r/w:t` 文本节点，不新增、删除、重排 paragraph/table/run。
- 首版跨 run 字段保守拒绝自动修改，返回 `UNSUPPORTED_REGION` 或校验错误。
- 超链接、域代码、目录、批注、修订痕迹、脚注尾注、文本框、SmartArt、页眉页脚等复杂结构进入 `unsupportedRegions`，导出时拒绝自动修改。
- 静态图片、头像、Logo 等 `w:drawing/w:pict` 结构可存在于模板中，不再作为导出全局拒绝条件；图片仍保留在 layout fingerprint 的媒体文件和 relationship 校验中，命中图片段落的字段仍按字段级 `unsupportedRegions` 拒绝自动修改。
- 默认 `PRESERVE_LAYOUT` 模式下，应用前后生成 layout fingerprint，校验段落数、表格数、run 数、媒体文件列表、document relationships 和清空正文后的 XML 结构 hash 不变。
- `CONTROLLED_EDIT` 模式只允许用户明确授权的 `STYLE_RANGE/INSERT_PARAGRAPH/DELETE_PARAGRAPH` 等变化，段落/run 变化必须在阈值内，媒体、关系和表格结构仍默认拒绝变化。
- 任一未授权校验失败返回 `RESUME_LAYOUT_CHANGED` 或对应验证错误，拒绝导出。
- 若本地 LibreOffice/soffice 不可用，Python 生成 `PARTIAL` 字段草图图片预览，保证图片区域确认、字段可修改范围和 Agent 后续流程可继续；草图只表示字段边界，不表示 DOCX 精确版式。视觉完全不变仍需 DOCX 转 PDF/图片渲染对比补充验证。

## 流程

1. Java 接收登录用户上传的 DOCX，保存为受控资源。
2. Java 读取受控资源字节，调用 Python `/internal/rag/resume/templates/parse`。
3. Python 解析字段绑定、hash、style/layout fingerprint 和 `unsupportedRegions`。
4. Java 持久化模板、字段和 fingerprint，对外只返回字段数量、复杂区域数量和模板摘要。
5. 用户在模板页选择历史模板或上传模板，确认图片区域约束并保存；模板页展示真实图片预览或字段草图降级预览，不展示字段原文和内部模板抽取结果。
6. 用户在 Agent 工作台输入 JD、选择已上传简历资料读取解析摘要，并选择模板后，Java 基于当前用户 RAG 检索 evidence 候选，不写查询历史。
7. Java 调用 Python `/internal/rag/resume/templates/patches/generate`。OpenAI 可用时走 Structured Outputs；百炼兼容路径使用 JSON 输出 + Pydantic/JSON Schema 校验，失败则返回安全草稿或拒绝。
8. Java 保存补丁草稿，前端展示原文、新文本、证据和风险。
9. 用户逐条确认或拒绝补丁，确认/拒绝必须有明显行级反馈和汇总反馈，不能只改右上角统计。
10. Java 校验版本、归属、状态和幂等键后调用 Python `/internal/rag/resume/templates/exports`。
11. Python 确定性应用补丁，layout fingerprint 通过后返回新 DOCX 字节。
12. Java 保存导出文件为新版本，记录导出结果，不覆盖原始文件。

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

- 模板页展示历史模板选择、真实图片预览、区域待保存/已保存状态和允许修改区域数量，不展示字段原文、内部定位或模板抽取结果。
- LibreOffice/soffice 不可用时仍展示字段草图图片预览，用户可以继续确认图片区域和可修改字段。
- Agent 工作台的简历摘要来自用户选择的已上传简历资料摘要，不提供手写摘要文本框。
- Agent 或补丁确认入口展示待确认、风险、校验失败、版本冲突和导出成功；确认/拒绝后需要行级背景、按钮状态和汇总提示同步变化。
- 构建通过。

必跑命令：

```powershell
conda run -n learning-evidence-rag python -B -m pytest ai-python/tests -q
cd backend-java; mvn test
cd frontend-react; npm run build
```
