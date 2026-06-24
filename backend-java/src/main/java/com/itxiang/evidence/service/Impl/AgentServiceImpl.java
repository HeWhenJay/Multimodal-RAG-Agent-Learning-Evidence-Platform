package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonAgentClient;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.dto.AgentOperationUndoDTO;
import com.itxiang.evidence.service.AgentMemoryService;
import com.itxiang.evidence.dto.AgentReviewDecisionDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.dto.AgentToolCallEventDTO;
import com.itxiang.evidence.dto.AgentTaskCreateDTO;
import com.itxiang.evidence.entity.AgentHumanReview;
import com.itxiang.evidence.entity.AgentOperation;
import com.itxiang.evidence.entity.AgentOperationSnapshot;
import com.itxiang.evidence.entity.AgentTask;
import com.itxiang.evidence.entity.AgentToolCall;
import com.itxiang.evidence.mapper.AgentHumanReviewMapper;
import com.itxiang.evidence.mapper.AgentOperationMapper;
import com.itxiang.evidence.mapper.AgentOperationSnapshotMapper;
import com.itxiang.evidence.mapper.AgentTaskMapper;
import com.itxiang.evidence.mapper.AgentToolCallMapper;
import com.itxiang.evidence.service.AgentService;
import com.itxiang.evidence.vo.AgentHumanReviewVO;
import com.itxiang.evidence.vo.AgentOperationVO;
import com.itxiang.evidence.vo.AgentTaskDetailVO;
import com.itxiang.evidence.vo.AgentTaskVO;
import com.itxiang.evidence.vo.AgentToolCallVO;
import com.itxiang.evidence.vo.AgentToolDefinitionVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class AgentServiceImpl implements AgentService {

    private static final Set<String> TASK_TYPES = Set.of("pure_read_query", "planning_task", "mutation_task");
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };

    private final AgentTaskMapper agentTaskMapper;
    private final AgentToolCallMapper agentToolCallMapper;
    private final AgentHumanReviewMapper agentHumanReviewMapper;
    private final AgentOperationMapper agentOperationMapper;
    private final AgentOperationSnapshotMapper agentOperationSnapshotMapper;
    private final AgentMemoryService agentMemoryService;
    private final PythonAgentClient pythonAgentClient;
    private final AgentProperties agentProperties;
    private final ObjectMapper objectMapper;

    /**
     * 创建当前用户的 Agent 任务，阶段 1 只落 Java 任务状态。
     */
    @Override
    @Transactional
    public AgentTaskVO createTask(AgentTaskCreateDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        String taskType = normalizeTaskType(dto.getTaskType());
        AgentTask task = new AgentTask();
        task.setId("agent-task-" + UUID.randomUUID().toString().replace("-", ""));
        task.setUserId(scopedUserId);
        task.setTaskType(taskType);
        task.setStatus("CREATED");
        task.setTitle(defaultTitle(dto));
        task.setInputJson(toJson(dto.getInput(), "{}"));
        task.setPlanJson("{}");
        task.setDraftJson("{}");
        task.setFinalJson("{}");
        agentTaskMapper.insert(task);
        log.info("创建 Agent 任务: taskId={}, userId={}, taskType={}", task.getId(), scopedUserId, taskType);
        schedulePythonAgentStart(task);
        return toTaskVO(agentTaskMapper.findById(task.getId()));
    }

    /**
     * 查询当前用户任务详情和工具调用时间线。
     */
    @Override
    public AgentTaskDetailVO getTask(String taskId, String userId) {
        String scopedTaskId = requireText(taskId, "任务 ID 不能为空");
        AgentTask task = agentTaskMapper.findByIdAndUserId(scopedTaskId, requireUserId(userId));
        if (task == null) {
            throw new IllegalArgumentException("Agent 任务不存在");
        }
        List<AgentToolCallVO> toolCalls = agentToolCallMapper.findByTaskId(task.getId()).stream()
                .map(this::toToolCallVO)
                .toList();
        List<AgentHumanReviewVO> reviews = agentHumanReviewMapper.findByTaskId(task.getId()).stream()
                .map(this::toReviewVO)
                .toList();
        List<AgentOperationVO> operations = agentOperationMapper.findByTaskId(task.getId()).stream()
                .map(this::toOperationVO)
                .toList();
        return AgentTaskDetailVO.builder()
                .id(task.getId())
                .taskType(task.getTaskType())
                .status(task.getStatus())
                .title(task.getTitle())
                .input(readMap(task.getInputJson()))
                .plan(readMap(task.getPlanJson()))
                .draft(readMap(task.getDraftJson()))
                .finalResult(readMap(task.getFinalJson()))
                .pythonThreadId(task.getPythonThreadId())
                .errorCode(task.getErrorCode())
                .errorMessage(task.getErrorMessage())
                .toolCalls(toolCalls)
                .reviews(reviews)
                .operations(operations)
                .createdAt(task.getCreatedAt())
                .updatedAt(task.getUpdatedAt())
                .build();
    }

    /**
     * 返回阶段化工具能力，前端可据此展示只读或审批规则。
     */
    @Override
    public List<AgentToolDefinitionVO> listTools() {
        return List.of(
                readTool("material_status_reader", 1, "读取当前用户资料解析状态、摘要和失败原因"),
                readTool("material_evidence_reader", 1, "读取当前用户资料 evidence 片段"),
                readTool("material_preview_reader", 1, "读取当前用户文本类资料预览，长度由 Java 控制"),
                readTool("rag_query_probe_non_persistent", 1, "执行非持久化 RAG 探针，不写询问历史"),
                readTool("retrieval_coverage_probe", 1, "基于非持久化 RAG diagnostics 输出覆盖摘要"),
                readTool("agent_memory_retriever", 2, "按当前任务和用户检索可注入记忆"),
                readTool("agent_memory_candidate_proposer", 2, "生成记忆候选和冲突判断，只返回候选不落库"),
                readTool("utc_time_provider", 2, "Python 本地系统时间工具，不访问用户数据"),
                readTool("resume_evidence_aligner", 3, "对齐 JD/简历要求与当前用户 evidence"),
                readTool("gap_analyzer", 3, "生成能力缺口和学习建议草稿"),
                readTool("evidence_quality_auditor", 3, "检查证据充分性、缺口和风险等级"),
                readTool("web_search_probe", 5, "联网检索公司背景、技能趋势和外部学习资源，只作为参考上下文"),
                mutationTool("resume_revision_save", 4, "用户审批后将 Agent 简历改写草稿固化到任务结果"),
                mutationTool("jd_learning_plan_save", 4, "用户审批后将 JD 学习计划草稿固化到任务结果"),
                mutationTool("agent_task_cancel_request", 4, "用户审批后取消本人 Agent 任务"),
                mutationTool("agent_memory_candidate_save", 7, "用户确认后保存记忆候选并进入索引流程")
        );
    }

    /**
     * 处理 Python Agent 回写的任务事件，按 taskId 保持幂等更新。
     */
    @Override
    @Transactional
    public Map<String, Object> handleEvent(String taskId, AgentTaskEventDTO event) {
        String scopedTaskId = requireText(taskId, "任务 ID 不能为空");
        if (event == null || event.getEventType() == null || event.getEventType().isBlank()) {
            throw new IllegalArgumentException("Agent 事件类型不能为空");
        }
        AgentTask task = agentTaskMapper.findById(scopedTaskId);
        if (task == null) {
            throw new IllegalArgumentException("Agent 任务不存在");
        }
        String status = eventStatus(task.getStatus(), event);
        task.setStatus(status);
        if (event.getPythonThreadId() != null && !event.getPythonThreadId().isBlank()) {
            task.setPythonThreadId(event.getPythonThreadId());
        }
        if (event.getDraft() != null && !event.getDraft().isEmpty()) {
            task.setDraftJson(toJson(event.getDraft(), task.getDraftJson()));
        }
        if (event.getFinalResult() != null) {
            task.setFinalJson(toJson(event.getFinalResult(), task.getFinalJson()));
        }
        task.setErrorCode(event.getErrorCode());
        task.setErrorMessage(event.getErrorMessage());
        agentTaskMapper.updateFromEvent(task);
        if ("TASK_COMPLETED".equals(event.getEventType())) {
            agentMemoryService.savePendingCandidatesFromTask(task);
        }
        upsertToolCall(task, event.getToolCall());
        createReviewIfPresent(task, event);
        return Map.of("taskId", task.getId(), "accepted", true, "status", status);
    }

    /**
     * 处理计划或输出审批决策。
     */
    @Override
    @Transactional
    public AgentTaskDetailVO decideReview(String taskId, String reviewId, AgentReviewDecisionDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        AgentTask task = agentTaskMapper.findByIdAndUserId(requireText(taskId, "任务 ID 不能为空"), scopedUserId);
        if (task == null) {
            throw new IllegalArgumentException("Agent 任务不存在");
        }
        AgentHumanReview review = agentHumanReviewMapper.findById(requireText(reviewId, "审批 ID 不能为空"));
        if (review == null || !task.getId().equals(review.getTaskId())) {
            throw new IllegalArgumentException("审批记录不存在");
        }
        if (!"PENDING".equals(review.getStatus())) {
            throw new IllegalArgumentException("审批记录不是待处理状态");
        }
        String decision = normalizeDecision(dto.getDecision());
        review.setStatus(decisionStatus(decision));
        review.setReviewedBy(scopedUserId);
        review.setReviewedAt(OffsetDateTime.now());
        review.setDecisionJson(toJson(decisionPayload(dto, decision), "{}"));
        agentHumanReviewMapper.updateDecision(review);
        handleReviewDecision(task, review, decision, dto);
        return getTask(task.getId(), scopedUserId);
    }

    /**
     * 撤销窗口内恢复变更前快照，只允许任务 owner 操作。
     */
    @Override
    @Transactional
    public AgentOperationVO undoOperation(String operationId, AgentOperationUndoDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        String scopedOperationId = requireText(operationId, "操作 ID 不能为空");
        AgentOperation operation = agentOperationMapper.findById(scopedOperationId);
        if (operation == null || !scopedUserId.equals(operation.getUserId())) {
            throw new IllegalArgumentException("Agent 变更操作不存在");
        }
        if (!"APPLIED_UNDOABLE".equals(operation.getStatus())) {
            throw new IllegalArgumentException("AGENT_OPERATION_NOT_UNDOABLE");
        }
        OffsetDateTime undoDeadline = operation.getUndoDeadline();
        if (undoDeadline == null || undoDeadline.isBefore(OffsetDateTime.now())) {
            operation.setStatus("UNDO_EXPIRED");
            operation.setErrorCode("AGENT_OPERATION_UNDO_EXPIRED");
            operation.setErrorMessage("撤销窗口已过期");
            agentOperationMapper.updateResult(operation);
            throw new IllegalArgumentException("AGENT_OPERATION_UNDO_EXPIRED");
        }
        AgentTask task = agentTaskMapper.findByIdAndUserId(operation.getTaskId(), scopedUserId);
        if (task == null) {
            throw new IllegalArgumentException("Agent 任务不存在");
        }
        AgentOperationSnapshot before = agentOperationSnapshotMapper.findByOperationIdAndType(operation.getId(), "BEFORE");
        if (before == null) {
            throw new IllegalArgumentException("AGENT_OPERATION_SNAPSHOT_MISSING");
        }
        restoreTaskFromSnapshot(task, before);
        agentTaskMapper.updateFromEvent(task);
        operation.setStatus("UNDONE");
        operation.setErrorCode(null);
        operation.setErrorMessage("用户已撤销：" + defaultText(dto == null ? null : dto.getReason(), "未填写原因"));
        agentOperationMapper.updateResult(operation);
        return toOperationVO(operation);
    }

    /**
     * 根据审批类型继续 Python 图或结束任务。
     */
    private void handleReviewDecision(AgentTask task, AgentHumanReview review, String decision, AgentReviewDecisionDTO dto) {
        if (!"APPROVED".equals(decision)) {
            task.setStatus("REJECTED".equals(decision) ? "CANCELED" : waitingStatusForReview(review));
            task.setErrorCode(null);
            task.setErrorMessage("CHANGES_REQUESTED".equals(decision) ? "用户要求调整计划或输出" : null);
            agentTaskMapper.updateFromEvent(task);
            return;
        }
        if ("OUTPUT".equals(review.getReviewType())) {
            if (requiresMutationAfterOutput(task)) {
                resumePythonAfterReview(task, review, decision, dto);
                return;
            }
            task.setStatus("COMPLETED");
            task.setFinalJson(task.getDraftJson() == null || task.getDraftJson().isBlank() ? "{}" : task.getDraftJson());
            task.setErrorCode(null);
            task.setErrorMessage(null);
            agentTaskMapper.updateFromEvent(task);
            agentMemoryService.savePendingCandidatesFromTask(task);
            return;
        }
        resumePythonAfterReview(task, review, decision, dto);
    }

    /**
     * 输出审批后是否继续进入保存类 CRUD 审批。
     */
    private boolean requiresMutationAfterOutput(AgentTask task) {
        Map<String, Object> input = readMap(task.getInputJson());
        Object saveDraft = input.get("saveDraft");
        if (Boolean.TRUE.equals(saveDraft) || "true".equalsIgnoreCase(String.valueOf(saveDraft))) {
            return true;
        }
        Object toolHints = input.get("toolHints");
        if (toolHints instanceof List<?> hints) {
            return hints.stream()
                    .map(String::valueOf)
                    .anyMatch(Set.of("resume_revision_save", "jd_learning_plan_save", "agent_task_cancel_request")::contains);
        }
        return false;
    }

    /**
     * 计划审批通过后请求 Python 继续执行只读分析。
     */
    private void resumePythonAfterReview(AgentTask task, AgentHumanReview review, String decision, AgentReviewDecisionDTO dto) {
        if (agentProperties.getInternalToken() == null || agentProperties.getInternalToken().isBlank()) {
            task.setStatus("WAITING_PLAN_REVIEW");
            task.setErrorCode("AGENT_INTERNAL_TOKEN_INVALID");
            task.setErrorMessage("内部 Agent 令牌未配置，无法恢复 Python Agent");
            agentTaskMapper.updateFromEvent(task);
            return;
        }
        task.setStatus("RUNNING");
        agentTaskMapper.updateFromEvent(task);
        Map<String, Object> payload = decisionPayload(dto, decision);
        payload.put("reviewId", review.getId());
        payload.put("reviewType", review.getReviewType());
        Map<String, Object> proposal = readMap(review.getProposalJson());
        copyIfPresent(payload, proposal, "toolName");
        copyIfPresent(payload, proposal, "operationType");
        copyIfPresent(payload, proposal, "resourceType");
        copyIfPresent(payload, proposal, "resourceId");
        copyIfPresent(payload, proposal, "idempotencyKey");
        pythonAgentClient.resumeTask(task, review.getReviewType(), decision, payload, readMap(task.getInputJson()));
    }

    /**
     * 根据审批类型返回继续等待状态。
     */
    private String waitingStatusForReview(AgentHumanReview review) {
        return "OUTPUT".equals(review.getReviewType()) ? "WAITING_OUTPUT_REVIEW" : "WAITING_PLAN_REVIEW";
    }

    /**
     * Python 请求人工审批时写入审批表。
     */
    @SuppressWarnings("unchecked")
    private void createReviewIfPresent(AgentTask task, AgentTaskEventDTO event) {
        Map<String, Object> request = event.getReviewRequest();
        if (request == null || request.isEmpty()) {
            return;
        }
        String reviewId = defaultText(String.valueOf(request.getOrDefault("id", "")), "review-" + UUID.randomUUID().toString().replace("-", ""));
        AgentHumanReview existing = agentHumanReviewMapper.findById(reviewId);
        if (existing != null) {
            return;
        }
        String reviewType = defaultText(String.valueOf(request.getOrDefault("reviewType", "")), inferReviewType(event));
        Object proposal = request.get("proposal");
        if ("PLAN".equals(reviewType) && proposal instanceof Map<?, ?>) {
            task.setPlanJson(toJson(proposal, task.getPlanJson()));
            agentTaskMapper.updateFromEvent(task);
        }
        AgentHumanReview review = new AgentHumanReview();
        review.setId(reviewId);
        review.setTaskId(task.getId());
        review.setReviewType(reviewType);
        review.setStatus("PENDING");
        review.setProposalJson(toJson(proposal instanceof Map<?, ?> ? (Map<String, Object>) proposal : request, "{}"));
        review.setDecisionJson("{}");
        agentHumanReviewMapper.insert(review);
    }

    /**
     * 从 BEFORE 快照恢复任务自身状态。
     */
    private void restoreTaskFromSnapshot(AgentTask task, AgentOperationSnapshot snapshot) {
        Map<String, Object> body = readMap(snapshot.getSnapshotJson());
        task.setStatus(defaultText(asText(body.get("status")), task.getStatus()));
        task.setPlanJson(toJson(body.get("plan"), task.getPlanJson()));
        task.setDraftJson(toJson(body.get("draft"), task.getDraftJson()));
        task.setFinalJson(toJson(body.get("final"), task.getFinalJson()));
        task.setPythonThreadId(asText(body.get("pythonThreadId")));
        task.setErrorCode(asText(body.get("errorCode")));
        task.setErrorMessage(asText(body.get("errorMessage")));
    }

    /**
     * 在任务事务提交后启动 Python Agent，避免 Python 回调读到未提交任务。
     */
    private void schedulePythonAgentStart(AgentTask task) {
        if (!shouldStartPythonAgent(task)) {
            return;
        }
        Runnable startAction = () -> startPythonAgent(task);
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    startAction.run();
                }
            });
        } else {
            startAction.run();
        }
    }

    /**
     * 判断阶段 2 是否具备启动 Python 只读 Agent 的条件。
     */
    private boolean shouldStartPythonAgent(AgentTask task) {
        return Set.of("pure_read_query", "planning_task").contains(task.getTaskType())
                && agentProperties.getInternalToken() != null
                && !agentProperties.getInternalToken().isBlank();
    }

    /**
     * 调用 Python Agent 接收任务；启动失败时只记录脱敏错误摘要。
     */
    private void startPythonAgent(AgentTask task) {
        try {
            pythonAgentClient.startTask(task, readMap(task.getInputJson()));
            log.info("已请求 Python Agent 启动任务: taskId={}", task.getId());
        } catch (PythonAgentClient.PythonAgentClientException e) {
            log.warn("Python Agent 启动失败: taskId={}, statusCode={}, message={}", task.getId(), e.getStatusCode(), e.getMessage());
            markPythonStartFailed(task, e);
        } catch (Exception e) {
            log.warn("Python Agent 启动异常: taskId={}, message={}", task.getId(), e.getMessage());
            markPythonStartFailed(task, e);
        }
    }

    /**
     * 回写 Python 启动失败状态，避免任务长期停留在 CREATED。
     */
    private void markPythonStartFailed(AgentTask task, Exception e) {
        AgentTask failed = agentTaskMapper.findById(task.getId());
        if (failed == null) {
            return;
        }
        failed.setStatus("FAILED");
        failed.setPythonThreadId(task.getId());
        failed.setErrorCode(e instanceof PythonAgentClient.PythonAgentClientException pythonException && pythonException.getStatusCode() == null
                ? "AGENT_PYTHON_TIMEOUT"
                : "AGENT_PYTHON_UNAVAILABLE");
        failed.setErrorMessage(truncate(e.getMessage() == null ? "Python Agent 不可用" : e.getMessage(), 500));
        agentTaskMapper.updateFromEvent(failed);
    }

    /**
     * 从事件类型推导任务状态。
     */
    private String eventStatus(String currentStatus, AgentTaskEventDTO event) {
        return switch (event.getEventType()) {
            case "TASK_STARTED" -> "RUNNING";
            case "TASK_COMPLETED" -> "COMPLETED";
            case "TASK_FAILED" -> "FAILED";
            case "REVIEW_REQUESTED" -> defaultText(event.getStatus(), "WAITING_PLAN_REVIEW");
            default -> defaultText(event.getStatus(), defaultText(currentStatus, "RUNNING"));
        };
    }

    /**
     * 推断审批类型。
     */
    private String inferReviewType(AgentTaskEventDTO event) {
        return "WAITING_OUTPUT_REVIEW".equals(event.getStatus()) ? "OUTPUT" : "PLAN";
    }

    /**
     * 插入或更新 Python 回写的工具观察。
     */
    private void upsertToolCall(AgentTask task, AgentToolCallEventDTO event) {
        if (event == null || event.getId() == null || event.getId().isBlank()) {
            return;
        }
        AgentToolCall toolCall = agentToolCallMapper.findById(event.getId());
        if (toolCall == null) {
            toolCall = new AgentToolCall();
            toolCall.setId(event.getId());
            toolCall.setTaskId(task.getId());
            toolCall.setToolName(defaultText(event.getToolName(), "unknown_tool"));
            toolCall.setToolType(defaultText(event.getToolType(), "READ"));
            toolCall.setStatus(defaultText(event.getStatus(), "RUNNING"));
            toolCall.setRequestJson("{}");
            toolCall.setResponseJson("{}");
            toolCall.setOwnershipVerified(Boolean.TRUE.equals(event.getOwnershipVerified()));
            toolCall.setScope(defaultText(event.getScope(), "current_user_or_authorized"));
            agentToolCallMapper.insert(toolCall);
        }
        toolCall.setStatus(defaultText(event.getStatus(), "RUNNING"));
        toolCall.setResponseJson(toJson(event.getResponse(), "{}"));
        toolCall.setOwnershipVerified(Boolean.TRUE.equals(event.getOwnershipVerified()));
        toolCall.setScope(defaultText(event.getScope(), "current_user_or_authorized"));
        toolCall.setErrorCode(event.getErrorCode());
        toolCall.setErrorMessage(event.getErrorMessage());
        agentToolCallMapper.updateResult(toolCall);
    }

    /**
     * 将任务实体转换为摘要 VO。
     */
    private AgentTaskVO toTaskVO(AgentTask task) {
        return AgentTaskVO.builder()
                .id(task.getId())
                .taskType(task.getTaskType())
                .status(task.getStatus())
                .title(task.getTitle())
                .input(readMap(task.getInputJson()))
                .plan(readMap(task.getPlanJson()))
                .draft(readMap(task.getDraftJson()))
                .finalResult(readMap(task.getFinalJson()))
                .pythonThreadId(task.getPythonThreadId())
                .errorCode(task.getErrorCode())
                .errorMessage(task.getErrorMessage())
                .createdAt(task.getCreatedAt())
                .updatedAt(task.getUpdatedAt())
                .build();
    }

    /**
     * 将工具调用实体转换为前端时间线 VO。
     */
    private AgentToolCallVO toToolCallVO(AgentToolCall toolCall) {
        return AgentToolCallVO.builder()
                .id(toolCall.getId())
                .taskId(toolCall.getTaskId())
                .toolName(toolCall.getToolName())
                .toolType(toolCall.getToolType())
                .status(toolCall.getStatus())
                .request(readMap(toolCall.getRequestJson()))
                .response(readMap(toolCall.getResponseJson()))
                .ownershipVerified(toolCall.getOwnershipVerified())
                .scope(toolCall.getScope())
                .errorCode(toolCall.getErrorCode())
                .errorMessage(toolCall.getErrorMessage())
                .createdAt(toolCall.getCreatedAt())
                .updatedAt(toolCall.getUpdatedAt())
                .build();
    }

    /**
     * 将审批实体转换为前端 VO。
     */
    private AgentHumanReviewVO toReviewVO(AgentHumanReview review) {
        return AgentHumanReviewVO.builder()
                .id(review.getId())
                .taskId(review.getTaskId())
                .reviewType(review.getReviewType())
                .status(review.getStatus())
                .proposal(readMap(review.getProposalJson()))
                .decision(readMap(review.getDecisionJson()))
                .reviewedBy(review.getReviewedBy())
                .reviewedAt(review.getReviewedAt())
                .createdAt(review.getCreatedAt())
                .updatedAt(review.getUpdatedAt())
                .expiresAt(review.getExpiresAt())
                .build();
    }

    /**
     * 将操作实体转换为前端 VO。
     */
    private AgentOperationVO toOperationVO(AgentOperation operation) {
        return AgentOperationVO.builder()
                .id(operation.getId())
                .taskId(operation.getTaskId())
                .reviewId(operation.getReviewId())
                .operationType(operation.getOperationType())
                .resourceType(operation.getResourceType())
                .resourceId(operation.getResourceId())
                .status(operation.getStatus())
                .beforeSnapshotRef(operation.getBeforeSnapshotRef())
                .afterSnapshotRef(operation.getAfterSnapshotRef())
                .idempotencyKey(operation.getIdempotencyKey())
                .undoDeadline(operation.getUndoDeadline())
                .auditEventId(operation.getAuditEventId())
                .errorCode(operation.getErrorCode())
                .errorMessage(operation.getErrorMessage())
                .createdAt(operation.getCreatedAt())
                .updatedAt(operation.getUpdatedAt())
                .build();
    }

    /**
     * 构造只读工具定义。
     */
    private AgentToolDefinitionVO readTool(String toolName, int stage, String description) {
        return AgentToolDefinitionVO.builder()
                .toolName(toolName)
                .toolType("READ")
                .requiresReview(false)
                .approvalType(null)
                .stage(stage)
                .description(description)
                .build();
    }

    /**
     * 构造变更工具定义。
     */
    private AgentToolDefinitionVO mutationTool(String toolName, int stage, String description) {
        return AgentToolDefinitionVO.builder()
                .toolName(toolName)
                .toolType("MUTATION")
                .requiresReview(true)
                .approvalType("CRUD")
                .stage(stage)
                .description(description)
                .build();
    }

    /**
     * 校验任务类型。
     */
    private String normalizeTaskType(String taskType) {
        String normalized = requireText(taskType, "任务类型不能为空");
        if (!TASK_TYPES.contains(normalized)) {
            throw new IllegalArgumentException("不支持的 Agent 任务类型：" + normalized);
        }
        return normalized;
    }

    /**
     * 校验审批决策。
     */
    private String normalizeDecision(String decision) {
        String normalized = requireText(decision, "审批决策不能为空");
        if (!Set.of("APPROVED", "REJECTED", "CHANGES_REQUESTED").contains(normalized)) {
            throw new IllegalArgumentException("不支持的审批决策：" + normalized);
        }
        return normalized;
    }

    /**
     * 将审批决策转换为审批记录状态。
     */
    private String decisionStatus(String decision) {
        return switch (decision) {
            case "APPROVED" -> "APPROVED";
            case "REJECTED" -> "REJECTED";
            default -> "CHANGES_REQUESTED";
        };
    }

    /**
     * 构造审批决策 JSON。
     */
    private Map<String, Object> decisionPayload(AgentReviewDecisionDTO dto, String decision) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("decision", decision);
        payload.put("comment", dto.getComment());
        payload.put("changes", dto.getChanges() == null ? Map.of() : dto.getChanges());
        return payload;
    }

    /**
     * 将审批提案中的变更元数据带入 Python 恢复请求。
     */
    private void copyIfPresent(Map<String, Object> target, Map<String, Object> source, String key) {
        Object value = source.get(key);
        if (value != null) {
            target.put(key, value);
        }
    }

    /**
     * 生成任务标题，避免前端未传标题时列表为空。
     */
    private String defaultTitle(AgentTaskCreateDTO dto) {
        if (dto.getTitle() != null && !dto.getTitle().isBlank()) {
            return truncate(dto.getTitle().trim(), 255);
        }
        Object goal = dto.getInput() == null ? null : dto.getInput().get("goal");
        return truncate(goal == null ? "Agent 任务" : String.valueOf(goal), 255);
    }

    /**
     * 读取 JSON 字符串为 Map。
     */
    private Map<String, Object> readMap(String json) {
        if (json == null || json.isBlank()) {
            return new LinkedHashMap<>();
        }
        try {
            return objectMapper.readValue(json, MAP_TYPE);
        } catch (Exception e) {
            return new LinkedHashMap<>();
        }
    }

    /**
     * 将对象序列化为 JSON 字符串。
     */
    private String toJson(Object value, String fallback) {
        try {
            return objectMapper.writeValueAsString(value == null ? Map.of() : value);
        } catch (Exception e) {
            return fallback;
        }
    }

    /**
     * 校验当前登录用户 ID。
     */
    private String requireUserId(String userId) {
        return requireText(userId, "登录状态已失效");
    }

    /**
     * 校验非空文本。
     */
    private String requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
        return value.trim();
    }

    /**
     * 截断过长文本。
     */
    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }

    /**
     * 为空文本提供默认值。
     */
    private String defaultText(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    /**
     * 将快照字段转为可空文本。
     */
    private String asText(Object value) {
        return value == null ? null : String.valueOf(value);
    }
}
