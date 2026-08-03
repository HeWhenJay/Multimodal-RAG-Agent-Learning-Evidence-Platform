# 复习卡片 PAE/ReAct 生成图设计

更新日期：2026-08-03

## 目标

一次性 DeepSeek 输出可能因为问题不完整、问答错位、引用不存在、答案缺少 evidence 支撑或结构化问题覆盖不足而被整批拒绝。复习中心需要把这些确定性的质量门禁结果反馈给模型继续修正，并在自动修复确实无法完成时让用户参与，而不是让用户无上下文地重复点击生成。

## 图边界

复习生成使用 `ai-python/app/review/generation_graph.py` 中的独立 LangGraph，不接入统一 Agent 图。Java 继续只负责业务状态和统一响应；Python 负责 evidence 清洗、DeepSeek 调用、图循环、质量校验与人工处理终态。

```text
planner -> actor -> observer
                      | 通过 -> END
                      | 拒绝
                      v
                    repair -> actor
                      | 尝试耗尽
                      v
                 human_review -> END
```

- `planner`：固定生成目标、结构化问题数、最大卡片数和用户补充说明。
- `actor`：调用 DeepSeek 输出唯一 JSON；第 2 轮起必须接收上一轮逐项质量反馈。
- `observer`：运行现有问题完整性、提示质量、sourceQuestion、evidence 引用、逐论断忠实度和结构化问题覆盖门禁。
- `repair`：整理并去重失败原因，未达模型尝试上限时回到 `actor`。
- `human_review`：把结果标记为 `NEEDS_REVIEW`，保存尝试次数和质量反馈，不再参加后台自动重试。

## 循环与失败控制

- LangGraph 调用固定使用 `recursion_limit=999`，为多节点循环留出足够空间。
- 真实质量修复默认最多 8 次，由 `REVIEW_GENERATION_MAX_ATTEMPTS` 配置并限制在安全范围内；空响应或非法 JSON 在当前质量轮内短程重试，不额外消耗质量修复轮次。递归限制与模型调用预算相互独立。
- 缺少 `DEEPSEEK_API_KEY`、资料无 evidence 等无法通过修复 Prompt 解决的问题直接保存为 `FAILED`。
- 质量门禁耗尽或 `GraphRecursionError` 保存为 `NEEDS_REVIEW`。用户提交 `userFeedback` 后启动一轮新的图执行，尝试次数按本轮重新计算，历史持久化反馈作为界面诊断，不污染原始 evidence。
- 任一失败终态都停用旧卡片，禁止继续展示旧 Prompt 或未通过门禁的内容。

## 持久化与公开状态

`learning_review_material` 新增：

- `generation_attempts INTEGER NOT NULL DEFAULT 0`
- `quality_feedback JSONB NOT NULL DEFAULT '[]'`

状态约束新增 `NEEDS_REVIEW`。公开 `ReviewMaterial` 增加 `generationAttempts`、`qualityFeedback` 和 `needsManualReview`。后台同步跳过 `NEEDS_REVIEW`，只有当前认证用户的显式生成接口可以重新启动图。

## 文件夹拖拽语义

今日资料组的标题手柄写入文档 `materialId`。文件夹卡片只接受当前复习中心的文档拖拽，投放后调用 `PUT /api/reviews/materials/folder`。拖拽期间今日队列可提供临时排序预览，但投放文件夹时必须恢复拖拽前顺序，不调用排序接口；文件夹归属、FSRS 到期状态和资料优先级保持互相独立。批量选择移动继续作为键盘与触屏替代路径。
