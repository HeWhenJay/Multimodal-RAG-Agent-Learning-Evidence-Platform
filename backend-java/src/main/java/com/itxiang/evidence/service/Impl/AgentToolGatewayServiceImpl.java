package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.client.TavilyClient;
import com.itxiang.evidence.dto.AgentMutationToolExecuteDTO;
import com.itxiang.evidence.dto.AgentReadToolRequestDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
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
import com.itxiang.evidence.service.AgentMemoryService;
import com.itxiang.evidence.service.AgentToolGatewayService;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.AgentMemoryVO;
import com.itxiang.evidence.vo.AgentToolResultVO;
import com.itxiang.evidence.vo.MaterialPreviewVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagQueryVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class AgentToolGatewayServiceImpl implements AgentToolGatewayService {

    private static final String SCOPE_CURRENT_USER = "current_user_or_authorized";
    private static final Set<String> SAFE_RESPONSE_KEYS = Set.of(
            "id",
            "materialId",
            "title",
            "documentId",
            "documentTitle",
            "status",
            "parser",
            "documentType",
            "source",
            "chunkCount",
            "contentType",
            "truncated",
            "maxChars",
            "evidenceCount",
            "documentTypeDistribution",
            "retrievalSourceDistribution",
            "operationId",
            "operationType",
            "resourceType",
            "resourceId",
            "beforeSnapshotRef",
            "afterSnapshotRef",
            "undoDeadline",
            "query",
            "retrievedAt",
            "requestId",
            "responseTime",
            "resultCount",
            "memoryId",
            "memoryCount",
            "candidateCount"
    );
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };

    private final AgentTaskMapper agentTaskMapper;
    private final AgentToolCallMapper agentToolCallMapper;
    private final AgentHumanReviewMapper agentHumanReviewMapper;
    private final AgentOperationMapper agentOperationMapper;
    private final AgentOperationSnapshotMapper agentOperationSnapshotMapper;
    private final AgentMemoryService agentMemoryService;
    private final RagService ragService;
    private final TavilyClient tavilyClient;
    private final ObjectMapper objectMapper;

    /**
     * 执行只读工具，所有用户范围都从 taskId 反查得到。
     */
    @Override
    @Transactional
    public AgentToolResultVO executeReadTool(AgentReadToolRequestDTO request) {
        String taskId = text(request == null ? null : request.getTaskId());
        String toolName = text(request == null ? null : request.getToolName());
        String toolCallId = defaultText(request == null ? null : request.getToolCallId(), "tool-call-" + UUID.randomUUID().toString().replace("-", ""));
        if (taskId == null || toolName == null) {
            return AgentToolResultVO.failed(taskId, toolCallId, toolName, "AGENT_VALIDATION_FAILED", "任务 ID 和工具名不能为空", false);
        }
        AgentTask task = agentTaskMapper.findById(taskId);
        if (task == null) {
            return AgentToolResultVO.failed(taskId, toolCallId, toolName, "AGENT_TASK_NOT_FOUND", "Agent 任务不存在", false);
        }
        AgentToolCall toolCall = ensureToolCall(task, request, toolCallId, toolName);
        try {
            Object data = executeReadToolData(toolName, task, arguments(request));
            AgentToolResultVO result = AgentToolResultVO.builder()
                    .taskId(taskId)
                    .toolCallId(toolCallId)
                    .toolName(toolName)
                    .status("SUCCEEDED")
                    .ownershipVerified(true)
                    .scope(SCOPE_CURRENT_USER)
                    .data(data)
                    .diagnostics(new LinkedHashMap<>())
                    .retryable(false)
                    .build();
            completeToolCall(toolCall, result);
            return result;
        } catch (AgentToolFailureException e) {
            AgentToolResultVO result = failureResult(taskId, toolCallId, toolName, e.errorCode, e.getMessage(), e.retryable, e.diagnostics);
            completeToolCall(toolCall, result);
            return result;
        } catch (Exception e) {
            AgentToolResultVO result = mapUnexpectedFailure(taskId, toolCallId, toolName, e);
            completeToolCall(toolCall, result);
            return result;
        }
    }

    /**
     * 执行阶段 4 已审批变更工具，变更范围限定在 Agent 自身任务状态。
     */
    @Override
    @Transactional
    public AgentToolResultVO executeMutationTool(AgentMutationToolExecuteDTO request) {
        String taskId = text(request == null ? null : request.getTaskId());
        String toolName = text(request == null ? null : request.getToolName());
        String toolCallId = defaultText(request == null ? null : request.getToolCallId(), "tool-call-" + UUID.randomUUID().toString().replace("-", ""));
        if (taskId == null || toolName == null) {
            return AgentToolResultVO.failed(taskId, toolCallId, toolName, "AGENT_VALIDATION_FAILED", "任务 ID 和工具名不能为空", false);
        }
        AgentTask task = agentTaskMapper.findById(taskId);
        if (task == null) {
            return AgentToolResultVO.failed(taskId, toolCallId, toolName, "AGENT_TASK_NOT_FOUND", "Agent 任务不存在", false);
        }
        AgentToolCall toolCall = ensureMutationToolCall(task, request, toolCallId, toolName);
        try {
            if ("agent_memory_candidate_save".equals(toolName)) {
                AgentHumanReview review = approvedCrudReview(task, request);
                Map<String, Object> arguments = arguments(request);
                AgentMemoryVO memory = agentMemoryService.saveCandidateFromTool(task, arguments, boolArg(arguments, "explicitRemember", false));
                AgentToolResultVO result = memorySaveSuccess(taskId, toolCallId, toolName, review, memory);
                completeToolCall(toolCall, result);
                return result;
            }
            MutationSpec spec = mutationSpec(toolName, task);
            AgentHumanReview review = approvedCrudReview(task, request);
            AgentOperation existing = findExistingOperation(task, request, spec);
            if (existing != null) {
                AgentToolResultVO result = mutationSuccess(taskId, toolCallId, toolName, existing);
                completeToolCall(toolCall, result);
                return result;
            }
            AgentOperation operation = createPendingOperation(task, review, request, spec);
            AgentOperationSnapshot before = createSnapshot(operation, "BEFORE", task);
            agentOperationSnapshotMapper.insert(before);
            operation.setBeforeSnapshotRef(snapshotRef(before));
            applyMutation(spec, task);
            agentTaskMapper.updateFromEvent(task);
            AgentOperationSnapshot after = createSnapshot(operation, "AFTER", task);
            agentOperationSnapshotMapper.insert(after);
            operation.setAfterSnapshotRef(snapshotRef(after));
            operation.setStatus("APPLIED_UNDOABLE");
            operation.setUndoDeadline(OffsetDateTime.now().plusMinutes(30));
            operation.setAuditEventId(System.currentTimeMillis());
            operation.setErrorCode(null);
            operation.setErrorMessage(null);
            agentOperationMapper.updateResult(operation);
            AgentToolResultVO result = mutationSuccess(taskId, toolCallId, toolName, operation);
            completeToolCall(toolCall, result);
            return result;
        } catch (AgentToolFailureException e) {
            AgentToolResultVO result = failureResult(taskId, toolCallId, toolName, e.errorCode, e.getMessage(), e.retryable, e.diagnostics);
            completeToolCall(toolCall, result);
            return result;
        } catch (Exception e) {
            AgentToolResultVO result = failureResult(taskId, toolCallId, toolName, "AGENT_VALIDATION_FAILED",
                    e.getMessage() == null ? "变更工具执行失败" : e.getMessage(), false, Map.of());
            completeToolCall(toolCall, result);
            return result;
        }
    }

    /**
     * 分发阶段 1 支持的只读工具。
     */
    private Object executeReadToolData(String toolName, AgentTask task, Map<String, Object> arguments) {
        return switch (toolName) {
            case "material_status_reader" -> ragService.getMaterial(longArg(arguments, "materialId"), task.getUserId());
            case "material_evidence_reader" -> ragService.listMaterialEvidences(
                    longArg(arguments, "materialId"),
                    task.getUserId(),
                    intArg(arguments, "topK", intArg(arguments, "limit", 20))
            );
            case "material_preview_reader" -> previewMaterial(arguments, task.getUserId());
            case "rag_query_probe_non_persistent" -> ragService.queryNonPersistent(queryDto(arguments), task.getUserId());
            case "retrieval_coverage_probe" -> retrievalCoverage(arguments, task.getUserId());
            case "web_search_probe" -> webSearch(arguments);
            case "agent_memory_retriever" -> memoryRetriever(task, arguments);
            case "agent_memory_candidate_proposer" -> agentMemoryService.proposeCandidates(task, arguments);
            default -> throw new AgentToolFailureException("AGENT_TOOL_UNKNOWN", "未知只读工具：" + toolName, false);
        };
    }

    /**
     * 执行记忆检索工具，并返回可注入的短摘要上下文。
     */
    private Map<String, Object> memoryRetriever(AgentTask task, Map<String, Object> arguments) {
        List<Map<String, Object>> memoryContext = agentMemoryService.retrieveForTask(task, arguments);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("memoryContext", memoryContext);
        data.put("memories", memoryContext);
        data.put("memoryCount", memoryContext.size());
        return data;
    }

    /**
     * 调用 Tavily 联网搜索，只返回结构化外部参考，不写 RAG evidence。
     */
    private Map<String, Object> webSearch(Map<String, Object> arguments) {
        String query = textArg(arguments, "query", "联网搜索 query 不能为空");
        String retrievedAt = OffsetDateTime.now().toString();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("query", query);
        payload.put("max_results", intArg(arguments, "maxResults", 5));
        payload.put("search_depth", normalizedSearchDepth(textArg(arguments, "searchDepth", null)));
        payload.put("topic", defaultText(textArg(arguments, "topic", null), "general"));
        try {
            Map<String, Object> raw = tavilyClient.search(payload);
            Map<String, Object> data = new LinkedHashMap<>();
            data.put("query", query);
            data.put("retrievedAt", retrievedAt);
            data.put("requestId", raw.get("request_id"));
            data.put("responseTime", raw.get("response_time"));
            data.put("results", normalizeTavilyResults(raw.get("results"), retrievedAt));
            data.put("resultCount", data.get("results") instanceof List<?> list ? list.size() : 0);
            return data;
        } catch (TavilyClient.TavilyClientException e) {
            String errorCode = e.getStatusCode() == null && e.getMessage() != null && e.getMessage().contains("未配置")
                    ? "AGENT_TAVILY_NOT_CONFIGURED"
                    : "AGENT_TAVILY_DOWNSTREAM_FAILED";
            Map<String, Object> diagnostics = new LinkedHashMap<>();
            diagnostics.put("tavilyOperation", e.getOperation());
            diagnostics.put("tavilyEndpoint", e.getEndpoint());
            diagnostics.put("statusCode", e.getStatusCode());
            diagnostics.put("responseBody", truncate(e.getResponseBody(), 500));
            throw new AgentToolFailureException(errorCode, e.getMessage(), true, diagnostics);
        }
    }

    /**
     * 归一化 Tavily 搜索深度。
     */
    private String normalizedSearchDepth(String value) {
        String normalized = defaultText(value, "basic");
        if (!Set.of("basic", "advanced", "fast", "ultra-fast").contains(normalized)) {
            return "basic";
        }
        return normalized;
    }

    /**
     * 只保留联网搜索的来源、摘要和分数。
     */
    private List<Map<String, Object>> normalizeTavilyResults(Object value, String retrievedAt) {
        if (!(value instanceof List<?> list)) {
            return List.of();
        }
        return list.stream()
                .filter(Map.class::isInstance)
                .map(item -> normalizeTavilyResult((Map<?, ?>) item, retrievedAt))
                .toList();
    }

    /**
     * 归一化单条 Tavily 结果。
     */
    private Map<String, Object> normalizeTavilyResult(Map<?, ?> item, String retrievedAt) {
        double score = doubleValue(item.get("score"));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("title", defaultText(text(item.get("title")), "未命名网页"));
        result.put("sourceUrl", text(item.get("url")));
        result.put("summary", truncate(defaultText(text(item.get("content")), ""), 800));
        result.put("score", score);
        result.put("confidence", score >= 0.75 ? "HIGH" : score >= 0.45 ? "MEDIUM" : "LOW");
        result.put("retrievedAt", retrievedAt);
        return result;
    }

    /**
     * 校验并返回当前支持的变更工具规格。
     */
    private MutationSpec mutationSpec(String toolName, AgentTask task) {
        return switch (toolName) {
            case "resume_revision_save" -> new MutationSpec(toolName, "RESUME_REVISION_SAVE", "agent_task_draft", task.getId());
            case "jd_learning_plan_save" -> new MutationSpec(toolName, "JD_PLAN_SAVE", "agent_task_draft", task.getId());
            case "agent_task_cancel_request" -> new MutationSpec(toolName, "TASK_CANCEL", "agent_task", task.getId());
            default -> throw new AgentToolFailureException("AGENT_TOOL_UNKNOWN", "未知变更工具：" + toolName, false);
        };
    }

    /**
     * 变更工具必须绑定当前任务 owner 已批准的 CRUD 审批。
     */
    private AgentHumanReview approvedCrudReview(AgentTask task, AgentMutationToolExecuteDTO request) {
        String approvalId = text(request == null ? null : request.getApprovalId());
        if (approvalId == null) {
            throw new AgentToolFailureException("AGENT_REVIEW_REQUIRED", "变更工具必须绑定已批准的 CRUD 审批", false);
        }
        AgentHumanReview review = agentHumanReviewMapper.findById(approvalId);
        if (review == null || !task.getId().equals(review.getTaskId())) {
            throw new AgentToolFailureException("AGENT_REVIEW_NOT_FOUND", "CRUD 审批记录不存在", false);
        }
        if (!"CRUD".equals(review.getReviewType())) {
            throw new AgentToolFailureException("AGENT_REVIEW_TYPE_MISMATCH", "审批类型不是 CRUD", false);
        }
        if (!"APPROVED".equals(review.getStatus())) {
            throw new AgentToolFailureException("AGENT_REVIEW_NOT_APPROVED", "CRUD 审批尚未通过", false);
        }
        if (!task.getUserId().equals(review.getReviewedBy())) {
            throw new AgentToolFailureException("AGENT_RESOURCE_FORBIDDEN", "CRUD 审批不属于当前任务 owner", false);
        }
        return review;
    }

    /**
     * 查询幂等维度或显式 operationId 已存在的操作。
     */
    private AgentOperation findExistingOperation(AgentTask task, AgentMutationToolExecuteDTO request, MutationSpec spec) {
        String idempotencyKey = requireText(request.getIdempotencyKey(), "幂等键不能为空");
        AgentOperation existing = agentOperationMapper.findByIdempotency(
                task.getUserId(),
                spec.operationType(),
                spec.resourceType(),
                spec.resourceId(),
                idempotencyKey
        );
        if (existing != null) {
            return existing;
        }
        String operationId = text(request.getOperationId());
        if (operationId == null) {
            return null;
        }
        AgentOperation operationById = agentOperationMapper.findById(operationId);
        if (operationById == null) {
            return null;
        }
        if (sameOperation(task, request, spec, operationById)) {
            return operationById;
        }
        throw new AgentToolFailureException("AGENT_IDEMPOTENCY_CONFLICT", "operationId 已存在但幂等维度不一致", false);
    }

    /**
     * 判断已有操作是否与当前请求完全一致。
     */
    private boolean sameOperation(AgentTask task, AgentMutationToolExecuteDTO request, MutationSpec spec, AgentOperation operation) {
        return task.getId().equals(operation.getTaskId())
                && task.getUserId().equals(operation.getUserId())
                && spec.operationType().equals(operation.getOperationType())
                && spec.resourceType().equals(operation.getResourceType())
                && spec.resourceId().equals(operation.getResourceId())
                && request.getIdempotencyKey().equals(operation.getIdempotencyKey());
    }

    /**
     * 创建待执行操作记录，实际变更仍在同一事务内完成。
     */
    private AgentOperation createPendingOperation(AgentTask task,
                                                  AgentHumanReview review,
                                                  AgentMutationToolExecuteDTO request,
                                                  MutationSpec spec) {
        AgentOperation operation = new AgentOperation();
        operation.setId(defaultText(request.getOperationId(), "agent-operation-" + UUID.randomUUID().toString().replace("-", "")));
        operation.setTaskId(task.getId());
        operation.setReviewId(review.getId());
        operation.setUserId(task.getUserId());
        operation.setOperationType(spec.operationType());
        operation.setResourceType(spec.resourceType());
        operation.setResourceId(spec.resourceId());
        operation.setStatus("PENDING_APPROVAL");
        operation.setIdempotencyKey(requireText(request.getIdempotencyKey(), "幂等键不能为空"));
        operation.setErrorCode(null);
        operation.setErrorMessage(null);
        agentOperationMapper.insert(operation);
        return operation;
    }

    /**
     * 生成 Agent 任务自身状态快照。
     */
    private AgentOperationSnapshot createSnapshot(AgentOperation operation, String snapshotType, AgentTask task) {
        Map<String, Object> snapshotBody = new LinkedHashMap<>();
        snapshotBody.put("taskId", task.getId());
        snapshotBody.put("taskType", task.getTaskType());
        snapshotBody.put("status", task.getStatus());
        snapshotBody.put("plan", readMap(task.getPlanJson()));
        snapshotBody.put("draft", readMap(task.getDraftJson()));
        snapshotBody.put("final", readMap(task.getFinalJson()));
        snapshotBody.put("pythonThreadId", task.getPythonThreadId());
        snapshotBody.put("errorCode", task.getErrorCode());
        snapshotBody.put("errorMessage", task.getErrorMessage());
        String snapshotJson = toJson(snapshotBody, "{}");
        AgentOperationSnapshot snapshot = new AgentOperationSnapshot();
        snapshot.setId("snapshot-" + snapshotType.toLowerCase(Locale.ROOT) + "-" + UUID.randomUUID().toString().replace("-", ""));
        snapshot.setOperationId(operation.getId());
        snapshot.setSnapshotType(snapshotType);
        snapshot.setResourceType(operation.getResourceType());
        snapshot.setResourceId(operation.getResourceId());
        snapshot.setSnapshotJson(snapshotJson);
        snapshot.setContentHash(sha256(snapshotJson));
        return snapshot;
    }

    /**
     * 将当前变更应用到 Agent 任务自身状态。
     */
    private void applyMutation(MutationSpec spec, AgentTask task) {
        if ("TASK_CANCEL".equals(spec.operationType())) {
            task.setStatus("CANCELED");
            task.setErrorCode(null);
            task.setErrorMessage(null);
            return;
        }
        task.setStatus("COMPLETED");
        task.setFinalJson(defaultText(task.getDraftJson(), "{}"));
        task.setErrorCode(null);
        task.setErrorMessage(null);
    }

    /**
     * 构造变更工具成功响应。
     */
    private AgentToolResultVO mutationSuccess(String taskId, String toolCallId, String toolName, AgentOperation operation) {
        return AgentToolResultVO.builder()
                .taskId(taskId)
                .toolCallId(toolCallId)
                .toolName(toolName)
                .status("SUCCEEDED")
                .ownershipVerified(true)
                .scope(SCOPE_CURRENT_USER)
                .data(operationResultData(operation))
                .diagnostics(new LinkedHashMap<>())
                .retryable(false)
                .build();
    }

    /**
     * 构造记忆候选保存成功响应。
     */
    private AgentToolResultVO memorySaveSuccess(String taskId,
                                                String toolCallId,
                                                String toolName,
                                                AgentHumanReview review,
                                                AgentMemoryVO memory) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("memoryId", memory.getId());
        data.put("status", memory.getStatus());
        data.put("reviewId", review.getId());
        data.put("namespace", memory.getNamespace());
        data.put("subjectKey", memory.getSubjectKey());
        return AgentToolResultVO.builder()
                .taskId(taskId)
                .toolCallId(toolCallId)
                .toolName(toolName)
                .status("SUCCEEDED")
                .ownershipVerified(true)
                .scope(SCOPE_CURRENT_USER)
                .data(data)
                .diagnostics(new LinkedHashMap<>())
                .retryable(false)
                .build();
    }

    /**
     * 提供给 Python 和前端的操作摘要。
     */
    private Map<String, Object> operationResultData(AgentOperation operation) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("operationId", operation.getId());
        data.put("operationType", operation.getOperationType());
        data.put("resourceType", operation.getResourceType());
        data.put("resourceId", operation.getResourceId());
        data.put("status", operation.getStatus());
        data.put("beforeSnapshotRef", operation.getBeforeSnapshotRef());
        data.put("afterSnapshotRef", operation.getAfterSnapshotRef());
        data.put("idempotencyKey", operation.getIdempotencyKey());
        data.put("undoDeadline", operation.getUndoDeadline());
        data.put("auditEventId", operation.getAuditEventId());
        data.put("errorCode", operation.getErrorCode());
        data.put("errorMessage", operation.getErrorMessage());
        return data;
    }

    /**
     * 读取文本预览并由 Java 控制返回长度。
     */
    private Map<String, Object> previewMaterial(Map<String, Object> arguments, String userId) {
        MaterialPreviewVO preview = ragService.previewMaterial(
                longArg(arguments, "materialId"),
                textArg(arguments, "source", null),
                userId
        );
        Map<String, Object> result = objectToMap(preview);
        int maxChars = intArg(arguments, "maxChars", 4000);
        Object content = result.get("content");
        if (content != null && String.valueOf(content).length() > maxChars) {
            result.put("content", String.valueOf(content).substring(0, maxChars));
            result.put("truncated", true);
            result.put("maxChars", maxChars);
        }
        return result;
    }

    /**
     * 基于非持久化查询结果生成召回覆盖摘要。
     */
    private Map<String, Object> retrievalCoverage(Map<String, Object> arguments, String userId) {
        RagQueryVO result = ragService.queryNonPersistent(queryDto(arguments), userId);
        List<RagEvidenceVO> evidences = result.getEvidences() == null ? List.of() : result.getEvidences();
        Map<String, Long> byDocumentType = evidences.stream()
                .collect(Collectors.groupingBy(e -> defaultText(e.getDocumentType(), "unknown"), LinkedHashMap::new, Collectors.counting()));
        Map<String, Long> byRetrievalSource = evidences.stream()
                .collect(Collectors.groupingBy(e -> defaultText(e.getRetrievalSource(), "unknown"), LinkedHashMap::new, Collectors.counting()));
        Map<String, Object> coverage = new LinkedHashMap<>();
        coverage.put("answer", result.getAnswer());
        coverage.put("expandedQueries", result.getExpandedQueries());
        coverage.put("evidenceCount", evidences.size());
        coverage.put("documentTypeDistribution", byDocumentType);
        coverage.put("retrievalSourceDistribution", byRetrievalSource);
        coverage.put("diagnostics", result.getDiagnostics());
        return coverage;
    }

    /**
     * 将工具参数转换为 RAG 查询 DTO。
     */
    @SuppressWarnings("unchecked")
    private RagQueryDTO queryDto(Map<String, Object> arguments) {
        RagQueryDTO dto = new RagQueryDTO();
        dto.setQuestion(textArg(arguments, "question", "问题不能为空"));
        dto.setTopK(intArg(arguments, "topK", 5));
        dto.setCandidateMultiplier(intArg(arguments, "candidateMultiplier", 4));
        Object metadataFilter = arguments.get("metadataFilter");
        if (metadataFilter instanceof Map<?, ?> map) {
            Map<String, Object> normalized = new LinkedHashMap<>();
            map.forEach((key, value) -> normalized.put(String.valueOf(key), value));
            dto.setMetadataFilter(normalized);
        }
        return dto;
    }

    /**
     * 确保工具调用记录存在。
     */
    private AgentToolCall ensureToolCall(AgentTask task,
                                         AgentReadToolRequestDTO request,
                                         String toolCallId,
                                         String toolName) {
        AgentToolCall existing = agentToolCallMapper.findById(toolCallId);
        if (existing != null) {
            return existing;
        }
        AgentToolCall toolCall = new AgentToolCall();
        toolCall.setId(toolCallId);
        toolCall.setTaskId(task.getId());
        toolCall.setToolName(toolName);
        toolCall.setToolType("READ");
        toolCall.setStatus("RUNNING");
        toolCall.setRequestJson(toJson(arguments(request), "{}"));
        toolCall.setResponseJson("{}");
        toolCall.setOwnershipVerified(false);
        toolCall.setScope(SCOPE_CURRENT_USER);
        agentToolCallMapper.insert(toolCall);
        return toolCall;
    }

    /**
     * 确保变更工具调用记录存在。
     */
    private AgentToolCall ensureMutationToolCall(AgentTask task,
                                                 AgentMutationToolExecuteDTO request,
                                                 String toolCallId,
                                                 String toolName) {
        AgentToolCall existing = agentToolCallMapper.findById(toolCallId);
        if (existing != null) {
            return existing;
        }
        AgentToolCall toolCall = new AgentToolCall();
        toolCall.setId(toolCallId);
        toolCall.setTaskId(task.getId());
        toolCall.setToolName(toolName);
        toolCall.setToolType("MUTATION");
        toolCall.setStatus("RUNNING");
        toolCall.setRequestJson(toJson(arguments(request), "{}"));
        toolCall.setResponseJson("{}");
        toolCall.setOwnershipVerified(false);
        toolCall.setScope(SCOPE_CURRENT_USER);
        agentToolCallMapper.insert(toolCall);
        return toolCall;
    }

    /**
     * 回写工具调用结果。
     */
    private void completeToolCall(AgentToolCall toolCall, AgentToolResultVO result) {
        toolCall.setStatus(result.getStatus());
        toolCall.setResponseJson(toJson(safeToolCallResponse(result), "{}"));
        toolCall.setOwnershipVerified(Boolean.TRUE.equals(result.getOwnershipVerified()));
        toolCall.setScope(defaultText(result.getScope(), SCOPE_CURRENT_USER));
        toolCall.setErrorCode(result.getErrorCode());
        toolCall.setErrorMessage(result.getErrorMessage());
        agentToolCallMapper.updateResult(toolCall);
    }

    /**
     * 构造脱敏后的工具调用观测记录，避免保存资料正文、完整回答和 evidence snippet。
     */
    private Map<String, Object> safeToolCallResponse(AgentToolResultVO result) {
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("status", result.getStatus());
        response.put("ownershipVerified", result.getOwnershipVerified());
        response.put("scope", result.getScope());
        response.put("errorCode", result.getErrorCode());
        response.put("errorMessage", result.getErrorMessage());
        response.put("retryable", result.getRetryable());
        response.put("diagnosticKeys", result.getDiagnostics() == null ? List.of() : result.getDiagnostics().keySet());
        response.put("dataSummary", summarizeData(result.getData()));
        return response;
    }

    /**
     * 将工具结果数据压缩为安全摘要。
     */
    private Object summarizeData(Object data) {
        if (data == null) {
            return Map.of();
        }
        if (data instanceof List<?> list) {
            return Map.of("itemCount", list.size());
        }
        if (data instanceof Map<?, ?> map) {
            Map<String, Object> normalized = new LinkedHashMap<>();
            map.forEach((key, value) -> normalized.put(String.valueOf(key), value));
            return summarizeMap(normalized);
        }
        try {
            return summarizeMap(objectToMap(data));
        } catch (Exception e) {
            return Map.of("valueType", data.getClass().getSimpleName());
        }
    }

    /**
     * 只保留可观测元数据，并把正文类字段转为长度或数量。
     */
    private Map<String, Object> summarizeMap(Map<String, Object> data) {
        Map<String, Object> summary = new LinkedHashMap<>();
        data.forEach((key, value) -> {
            if (SAFE_RESPONSE_KEYS.contains(key)) {
                summary.put(key, value);
            }
        });
        putTextLength(summary, data, "content", "contentLength");
        putTextLength(summary, data, "answer", "answerLength");
        putTextLength(summary, data, "snippet", "snippetLength");
        putTextLength(summary, data, "question", "questionLength");
        putListSize(summary, data, "evidences", "evidenceCount");
        putListSize(summary, data, "expandedQueries", "expandedQueryCount");
        if (data.containsKey("diagnostics") && data.get("diagnostics") instanceof Map<?, ?> diagnostics) {
            summary.put("diagnosticKeys", diagnostics.keySet().stream().map(String::valueOf).toList());
        }
        return summary;
    }

    /**
     * 将文本字段记录为长度。
     */
    private void putTextLength(Map<String, Object> summary, Map<String, Object> data, String sourceKey, String targetKey) {
        Object value = data.get(sourceKey);
        if (value != null) {
            summary.put(targetKey, String.valueOf(value).length());
        }
    }

    /**
     * 将列表字段记录为数量。
     */
    private void putListSize(Map<String, Object> summary, Map<String, Object> data, String sourceKey, String targetKey) {
        Object value = data.get(sourceKey);
        if (value instanceof List<?> list) {
            summary.put(targetKey, list.size());
        }
    }

    /**
     * 将异常映射为 Agent 工具失败响应。
     */
    private AgentToolResultVO mapUnexpectedFailure(String taskId, String toolCallId, String toolName, Exception e) {
        if (e instanceof PythonRagClient.PythonRagClientException pythonException) {
            Map<String, Object> diagnostics = new LinkedHashMap<>();
            diagnostics.put("pythonOperation", pythonException.getOperation());
            diagnostics.put("pythonEndpoint", pythonException.getEndpoint());
            diagnostics.put("statusCode", pythonException.getStatusCode());
            diagnostics.put("responseBody", truncate(pythonException.getResponseBody(), 500));
            Integer statusCode = pythonException.getStatusCode();
            boolean retryable = statusCode == null || statusCode >= 500;
            String errorCode = statusCode == null && containsTimeout(pythonException.getMessage())
                    ? "AGENT_PYTHON_TIMEOUT"
                    : statusCode == null ? "AGENT_PYTHON_UNAVAILABLE" : "AGENT_TOOL_DOWNSTREAM_FAILED";
            return failureResult(taskId, toolCallId, toolName, errorCode, "只读工具下游调用失败", retryable, diagnostics);
        }
        if (e instanceof IllegalArgumentException && e.getMessage() != null && e.getMessage().contains("资料不存在")) {
            return failureResult(taskId, toolCallId, toolName, "AGENT_RESOURCE_FORBIDDEN", "当前任务无权读取该资源", false, Map.of());
        }
        String message = e.getMessage() == null ? "只读工具执行失败" : e.getMessage();
        return failureResult(taskId, toolCallId, toolName, "AGENT_VALIDATION_FAILED", message, false, Map.of());
    }

    /**
     * 构造失败响应。
     */
    private AgentToolResultVO failureResult(String taskId,
                                            String toolCallId,
                                            String toolName,
                                            String errorCode,
                                            String errorMessage,
                                            boolean retryable,
                                            Map<String, Object> diagnostics) {
        return AgentToolResultVO.builder()
                .taskId(taskId)
                .toolCallId(toolCallId)
                .toolName(toolName)
                .status("REJECTED")
                .ownershipVerified(false)
                .scope(SCOPE_CURRENT_USER)
                .diagnostics(diagnostics == null ? new LinkedHashMap<>() : diagnostics)
                .errorCode(errorCode)
                .errorMessage(errorMessage)
                .retryable(retryable)
                .build();
    }

    /**
     * 读取请求参数。
     */
    private Map<String, Object> arguments(AgentReadToolRequestDTO request) {
        return request == null || request.getArguments() == null ? new LinkedHashMap<>() : request.getArguments();
    }

    /**
     * 读取变更工具请求参数，并补充审计所需字段。
     */
    private Map<String, Object> arguments(AgentMutationToolExecuteDTO request) {
        Map<String, Object> arguments = request == null || request.getArguments() == null ? new LinkedHashMap<>() : new LinkedHashMap<>(request.getArguments());
        if (request != null) {
            arguments.put("approvalId", request.getApprovalId());
            arguments.put("operationId", request.getOperationId());
            arguments.put("idempotencyKey", request.getIdempotencyKey());
        }
        return arguments;
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
     * 读取 Long 参数。
     */
    private Long longArg(Map<String, Object> arguments, String key) {
        Object value = arguments.get(key);
        if (value instanceof Number number) {
            return number.longValue();
        }
        try {
            return Long.parseLong(String.valueOf(value));
        } catch (Exception e) {
            throw new AgentToolFailureException("AGENT_VALIDATION_FAILED", key + " 参数不能为空或格式错误", false);
        }
    }

    /**
     * 读取 Integer 参数。
     */
    private Integer intArg(Map<String, Object> arguments, String key, Integer defaultValue) {
        Object value = arguments.get(key);
        if (value == null) {
            return defaultValue;
        }
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(value));
        } catch (Exception e) {
            return defaultValue;
        }
    }

    /**
     * 读取布尔参数。
     */
    private boolean boolArg(Map<String, Object> arguments, String key, boolean defaultValue) {
        Object value = arguments.get(key);
        if (value == null) {
            return defaultValue;
        }
        if (value instanceof Boolean bool) {
            return bool;
        }
        return "true".equalsIgnoreCase(String.valueOf(value));
    }

    /**
     * 读取 Double 值，无法解析时按低可信处理。
     */
    private double doubleValue(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        try {
            return Double.parseDouble(String.valueOf(value));
        } catch (Exception e) {
            return 0.0;
        }
    }

    /**
     * 读取文本参数。
     */
    private String textArg(Map<String, Object> arguments, String key, String errorWhenBlank) {
        String value = text(arguments.get(key));
        if (value == null && errorWhenBlank != null) {
            throw new AgentToolFailureException("AGENT_VALIDATION_FAILED", errorWhenBlank, false);
        }
        return value;
    }

    /**
     * 将对象转为 Map。
     */
    private Map<String, Object> objectToMap(Object value) {
        return objectMapper.convertValue(value, MAP_TYPE);
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
     * 去除空白文本。
     */
    private String text(Object value) {
        if (value == null) {
            return null;
        }
        String text = String.valueOf(value).trim();
        return text.isEmpty() ? null : text;
    }

    /**
     * 校验非空文本。
     */
    private String requireText(String value, String message) {
        String normalized = text(value);
        if (normalized == null) {
            throw new AgentToolFailureException("AGENT_VALIDATION_FAILED", message, false);
        }
        return normalized;
    }

    /**
     * 为空文本提供默认值。
     */
    private String defaultText(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }

    /**
     * 判断异常信息是否表示超时。
     */
    private boolean containsTimeout(String message) {
        return message != null && message.toLowerCase(Locale.ROOT).contains("timeout");
    }

    /**
     * 截断响应摘要。
     */
    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }

    /**
     * 生成快照内容哈希，便于后续审计比对。
     */
    private String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            return "hash-unavailable";
        }
    }

    /**
     * 生成快照引用。
     */
    private String snapshotRef(AgentOperationSnapshot snapshot) {
        return "agent-operation-snapshot:" + snapshot.getId();
    }

    /**
     * 变更工具规格。
     */
    private record MutationSpec(String toolName, String operationType, String resourceType, String resourceId) {
    }

    /**
     * 工具可预期失败，保留明确错误码。
     */
    private static class AgentToolFailureException extends RuntimeException {
        private final String errorCode;
        private final boolean retryable;
        private final Map<String, Object> diagnostics;

        private AgentToolFailureException(String errorCode, String message, boolean retryable) {
            this(errorCode, message, retryable, Map.of());
        }

        private AgentToolFailureException(String errorCode, String message, boolean retryable, Map<String, Object> diagnostics) {
            super(message);
            this.errorCode = errorCode;
            this.retryable = retryable;
            this.diagnostics = diagnostics;
        }
    }
}
