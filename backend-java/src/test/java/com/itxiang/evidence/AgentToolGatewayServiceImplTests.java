package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.client.TavilyClient;
import com.itxiang.evidence.dto.AgentMutationToolExecuteDTO;
import com.itxiang.evidence.dto.AgentReadToolRequestDTO;
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
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.service.AgentMemoryService;
import com.itxiang.evidence.service.Impl.AgentToolGatewayServiceImpl;
import com.itxiang.evidence.vo.AgentMemoryVO;
import com.itxiang.evidence.vo.AgentToolResultVO;
import com.itxiang.evidence.vo.MaterialPreviewVO;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AgentToolGatewayServiceImplTests {

    @Mock
    private AgentTaskMapper agentTaskMapper;

    @Mock
    private AgentToolCallMapper agentToolCallMapper;

    @Mock
    private AgentHumanReviewMapper agentHumanReviewMapper;

    @Mock
    private AgentOperationMapper agentOperationMapper;

    @Mock
    private AgentOperationSnapshotMapper agentOperationSnapshotMapper;

    @Mock
    private AgentMemoryService agentMemoryService;

    @Mock
    private RagService ragService;

    @Mock
    private TavilyClient tavilyClient;

    private AgentToolGatewayServiceImpl service;

    @BeforeEach
    void setUp() {
        service = new AgentToolGatewayServiceImpl(
                agentTaskMapper,
                agentToolCallMapper,
                agentHumanReviewMapper,
                agentOperationMapper,
                agentOperationSnapshotMapper,
                agentMemoryService,
                ragService,
                tavilyClient,
                new ObjectMapper()
        );
    }

    @Test
    void unknownToolReturnsStableErrorCode() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        AgentReadToolRequestDTO request = request("unknown_reader", Map.of());

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getStatus()).isEqualTo("REJECTED");
        assertThat(result.getErrorCode()).isEqualTo("AGENT_TOOL_UNKNOWN");
        assertThat(result.getRetryable()).isFalse();
        verify(agentToolCallMapper).insert(any(AgentToolCall.class));
        verify(agentToolCallMapper).updateResult(any(AgentToolCall.class));
    }

    @Test
    void missingTaskDoesNotCreateToolCall() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(null);
        AgentReadToolRequestDTO request = request("material_status_reader", Map.of("materialId", 1));

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getStatus()).isEqualTo("REJECTED");
        assertThat(result.getErrorCode()).isEqualTo("AGENT_TASK_NOT_FOUND");
        verify(agentToolCallMapper, never()).insert(any(AgentToolCall.class));
        verify(agentToolCallMapper, never()).updateResult(any(AgentToolCall.class));
    }

    @Test
    void materialOwnerMismatchMapsToResourceForbidden() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(ragService.getMaterial(eq(99L), eq("7"))).thenThrow(new IllegalArgumentException("资料不存在"));
        AgentReadToolRequestDTO request = request("material_status_reader", Map.of("materialId", 99));

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getStatus()).isEqualTo("REJECTED");
        assertThat(result.getErrorCode()).isEqualTo("AGENT_RESOURCE_FORBIDDEN");
        assertThat(result.getOwnershipVerified()).isFalse();
        verify(agentToolCallMapper).updateResult(any(AgentToolCall.class));
    }

    @Test
    void pythonClientHttp4xxIsDownstreamFailureAndNotRetryable() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(ragService.queryNonPersistent(any(), eq("7"))).thenThrow(new PythonRagClient.PythonRagClientException(
                "query",
                "/internal/rag/query",
                400,
                "{\"detail\":\"bad request\"}",
                "Python RAG 调用失败: 400 BAD_REQUEST",
                null
        ));
        AgentReadToolRequestDTO request = request("rag_query_probe_non_persistent", Map.of("question", "Redis 是什么"));

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getErrorCode()).isEqualTo("AGENT_TOOL_DOWNSTREAM_FAILED");
        assertThat(result.getRetryable()).isFalse();
        assertThat(result.getDiagnostics()).containsEntry("statusCode", 400);
    }

    @Test
    void pythonClientHttp5xxIsDownstreamFailureAndRetryable() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(ragService.queryNonPersistent(any(), eq("7"))).thenThrow(new PythonRagClient.PythonRagClientException(
                "query",
                "/internal/rag/query",
                503,
                "{\"detail\":\"unavailable\"}",
                "Python RAG 调用失败: 503 SERVICE_UNAVAILABLE",
                null
        ));
        AgentReadToolRequestDTO request = request("rag_query_probe_non_persistent", Map.of("question", "Redis 是什么"));

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getErrorCode()).isEqualTo("AGENT_TOOL_DOWNSTREAM_FAILED");
        assertThat(result.getRetryable()).isTrue();
        assertThat(result.getDiagnostics()).containsEntry("statusCode", 503);
    }

    @Test
    void pythonClientTimeoutWithoutStatusMapsToTimeout() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(ragService.queryNonPersistent(any(), eq("7"))).thenThrow(new PythonRagClient.PythonRagClientException(
                "query",
                "/internal/rag/query",
                null,
                null,
                "Python RAG 调用失败: timeout",
                null
        ));
        AgentReadToolRequestDTO request = request("rag_query_probe_non_persistent", Map.of("question", "Redis 是什么"));

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getErrorCode()).isEqualTo("AGENT_PYTHON_TIMEOUT");
        assertThat(result.getRetryable()).isTrue();
    }

    @Test
    void validToolUsesTaskUserIdFromDatabase() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        AgentReadToolRequestDTO request = request("material_status_reader", Map.of("materialId", 12));

        service.executeReadTool(request);

        verify(ragService).getMaterial(12L, "7");
        ArgumentCaptor<AgentToolCall> captor = ArgumentCaptor.forClass(AgentToolCall.class);
        verify(agentToolCallMapper).insert(captor.capture());
        assertThat(captor.getValue().getScope()).isEqualTo("current_user_or_authorized");
    }

    @Test
    void memoryRetrieverUsesTaskOwnerAndReturnsContext() {
        AgentTask task = task("agent-task-1", "7");
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentMemoryService.retrieveForTask(eq(task), any())).thenReturn(List.of(Map.of(
                "memoryId", "agent-memory-1",
                "summary", "用户偏好先给结论",
                "score", 0.82
        )));
        AgentReadToolRequestDTO request = request("agent_memory_retriever", Map.of("query", "Redis", "userId", "999"));

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getStatus()).isEqualTo("SUCCEEDED");
        assertThat(mutationData(result).get("memoryCount")).isEqualTo(1);
        verify(agentMemoryService).retrieveForTask(eq(task), any());
    }

    @Test
    void previewToolCallStoresOnlyRedactedResponseSummary() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(ragService.previewMaterial(eq(12L), eq(null), eq("7"))).thenReturn(MaterialPreviewVO.builder()
                .materialId(12L)
                .title("课程笔记.md")
                .documentType("markdown")
                .source("upload")
                .contentType("text/markdown")
                .content("这里是不能写入工具调用日志的资料正文")
                .build());
        AgentReadToolRequestDTO request = request("material_preview_reader", Map.of("materialId", 12));
        ArgumentCaptor<AgentToolCall> captor = ArgumentCaptor.forClass(AgentToolCall.class);

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(String.valueOf(result.getData())).contains("资料正文");
        verify(agentToolCallMapper).updateResult(captor.capture());
        assertThat(captor.getValue().getResponseJson()).doesNotContain("资料正文");
        assertThat(captor.getValue().getResponseJson()).contains("contentLength");
    }

    @Test
    void webSearchWithoutTavilyKeyReturnsRetryableError() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(tavilyClient.search(any())).thenThrow(new TavilyClient.TavilyClientException(
                "search",
                "/search",
                null,
                null,
                "Tavily API Key 未配置",
                null
        ));
        AgentReadToolRequestDTO request = request("web_search_probe", Map.of("query", "后端实习 技能趋势"));

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getStatus()).isEqualTo("REJECTED");
        assertThat(result.getErrorCode()).isEqualTo("AGENT_TAVILY_NOT_CONFIGURED");
        assertThat(result.getRetryable()).isTrue();
        verify(agentToolCallMapper).updateResult(any(AgentToolCall.class));
    }

    @Test
    void webSearchReturnsOnlySourceSummaryAndConfidence() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(tavilyClient.search(any())).thenReturn(new LinkedHashMap<>(Map.of(
                "request_id", "request-1",
                "response_time", "1.2",
                "results", List.of(Map.of(
                        "title", "Redis 后端趋势",
                        "url", "https://example.com/redis",
                        "content", "这里是联网搜索摘要，不写入 RAG evidence。",
                        "score", 0.91
                ))
        )));
        AgentReadToolRequestDTO request = request("web_search_probe", Map.of("query", "Redis 后端趋势", "maxResults", 3));

        AgentToolResultVO result = service.executeReadTool(request);

        assertThat(result.getStatus()).isEqualTo("SUCCEEDED");
        Map<String, Object> data = mutationData(result);
        assertThat(data.get("query")).isEqualTo("Redis 后端趋势");
        assertThat(data.get("resultCount")).isEqualTo(1);
        assertThat(String.valueOf(data)).contains("https://example.com/redis", "HIGH");
        ArgumentCaptor<AgentToolCall> captor = ArgumentCaptor.forClass(AgentToolCall.class);
        verify(agentToolCallMapper).updateResult(captor.capture());
        assertThat(captor.getValue().getResponseJson()).contains("resultCount");
        assertThat(captor.getValue().getResponseJson()).doesNotContain("联网搜索摘要");
    }

    @Test
    void mutationToolRequiresApprovedCrudReview() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(agentHumanReviewMapper.findById("review-crud-1")).thenReturn(null);

        AgentToolResultVO result = service.executeMutationTool(mutationRequest("jd_learning_plan_save"));

        assertThat(result.getStatus()).isEqualTo("REJECTED");
        assertThat(result.getErrorCode()).isEqualTo("AGENT_REVIEW_NOT_FOUND");
        verify(agentOperationMapper, never()).insert(any());
        verify(agentOperationSnapshotMapper, never()).insert(any());
    }

    @Test
    void memoryCandidateSaveRequiresApprovedCrudReview() {
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task("agent-task-1", "7"));
        when(agentHumanReviewMapper.findById("review-crud-1")).thenReturn(null);

        AgentToolResultVO result = service.executeMutationTool(mutationRequest("agent_memory_candidate_save"));

        assertThat(result.getStatus()).isEqualTo("REJECTED");
        assertThat(result.getErrorCode()).isEqualTo("AGENT_REVIEW_NOT_FOUND");
        verify(agentMemoryService, never()).saveCandidateFromTool(any(), any(), anyBoolean());
    }

    @Test
    void approvedMemoryCandidateSaveUsesTaskOwner() {
        AgentTask task = task("agent-task-1", "7");
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentHumanReviewMapper.findById("review-crud-1")).thenReturn(approvedCrudReview());
        when(agentMemoryService.saveCandidateFromTool(eq(task), any(), eq(true))).thenReturn(AgentMemoryVO.builder()
                .id("agent-memory-1")
                .status("ACTIVE")
                .memoryType("PREFERENCE")
                .namespace("user_preference")
                .scopeType("USER")
                .subjectKey("answer_style")
                .summary("用户偏好先给结论")
                .build());
        AgentMutationToolExecuteDTO request = mutationRequest("agent_memory_candidate_save");
        request.getArguments().put("explicitRemember", true);
        request.getArguments().put("content", "用户偏好先给结论");
        request.getArguments().put("userId", "999");

        AgentToolResultVO result = service.executeMutationTool(request);

        assertThat(result.getStatus()).isEqualTo("SUCCEEDED");
        assertThat(result.getOwnershipVerified()).isTrue();
        assertThat(mutationData(result).get("memoryId")).isEqualTo("agent-memory-1");
        verify(agentMemoryService).saveCandidateFromTool(eq(task), any(), eq(true));
    }

    @Test
    void approvedMutationCreatesSnapshotsAndAppliesDraftToFinal() {
        AgentTask task = task("agent-task-1", "7");
        task.setTaskType("planning_task");
        task.setStatus("WAITING_CRUD_REVIEW");
        task.setDraftJson("{\"learningPlan\":\"学习 Redis 和 RAG\"}");
        task.setFinalJson("{}");
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentHumanReviewMapper.findById("review-crud-1")).thenReturn(approvedCrudReview());
        when(agentOperationMapper.findByIdempotency(eq("7"), eq("JD_PLAN_SAVE"), eq("agent_task_draft"), eq("agent-task-1"), eq("idem-1")))
                .thenReturn(null);

        AgentToolResultVO result = service.executeMutationTool(mutationRequest("jd_learning_plan_save"));

        assertThat(result.getStatus()).isEqualTo("SUCCEEDED");
        assertThat(result.getOwnershipVerified()).isTrue();
        assertThat(result.getData()).isInstanceOf(Map.class);
        assertThat(mutationData(result).get("operationType")).isEqualTo("JD_PLAN_SAVE");
        ArgumentCaptor<AgentOperation> operationCaptor = ArgumentCaptor.forClass(AgentOperation.class);
        verify(agentOperationMapper).insert(operationCaptor.capture());
        verify(agentOperationMapper).updateResult(operationCaptor.capture());
        assertThat(operationCaptor.getAllValues().get(1).getStatus()).isEqualTo("APPLIED_UNDOABLE");
        assertThat(operationCaptor.getAllValues().get(1).getBeforeSnapshotRef()).startsWith("agent-operation-snapshot:");
        assertThat(operationCaptor.getAllValues().get(1).getAfterSnapshotRef()).startsWith("agent-operation-snapshot:");
        ArgumentCaptor<AgentOperationSnapshot> snapshotCaptor = ArgumentCaptor.forClass(AgentOperationSnapshot.class);
        verify(agentOperationSnapshotMapper, org.mockito.Mockito.times(2)).insert(snapshotCaptor.capture());
        assertThat(snapshotCaptor.getAllValues()).extracting(AgentOperationSnapshot::getSnapshotType).containsExactly("BEFORE", "AFTER");
        ArgumentCaptor<AgentTask> taskCaptor = ArgumentCaptor.forClass(AgentTask.class);
        verify(agentTaskMapper).updateFromEvent(taskCaptor.capture());
        assertThat(taskCaptor.getValue().getStatus()).isEqualTo("COMPLETED");
        assertThat(taskCaptor.getValue().getFinalJson()).contains("学习 Redis");
    }

    @Test
    void mutationIdempotencyReturnsExistingOperationWithoutReapplying() {
        AgentTask task = task("agent-task-1", "7");
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentHumanReviewMapper.findById("review-crud-1")).thenReturn(approvedCrudReview());
        AgentOperation existing = operation("agent-operation-1", "APPLIED_UNDOABLE");
        when(agentOperationMapper.findByIdempotency(eq("7"), eq("TASK_CANCEL"), eq("agent_task"), eq("agent-task-1"), eq("idem-1")))
                .thenReturn(existing);

        AgentToolResultVO result = service.executeMutationTool(mutationRequest("agent_task_cancel_request"));

        assertThat(result.getStatus()).isEqualTo("SUCCEEDED");
        assertThat(mutationData(result).get("operationId")).isEqualTo("agent-operation-1");
        verify(agentTaskMapper, never()).updateFromEvent(any(AgentTask.class));
        verify(agentOperationSnapshotMapper, never()).insert(any());
    }

    @Test
    void explicitOperationIdConflictIsRejected() {
        AgentTask task = task("agent-task-1", "7");
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentHumanReviewMapper.findById("review-crud-1")).thenReturn(approvedCrudReview());
        when(agentOperationMapper.findByIdempotency(eq("7"), eq("TASK_CANCEL"), eq("agent_task"), eq("agent-task-1"), eq("idem-1")))
                .thenReturn(null);
        AgentOperation conflicting = operation("operation-fixed", "APPLIED_UNDOABLE");
        conflicting.setIdempotencyKey("other-idem");
        when(agentOperationMapper.findById("operation-fixed")).thenReturn(conflicting);
        AgentMutationToolExecuteDTO request = mutationRequest("agent_task_cancel_request");
        request.setOperationId("operation-fixed");

        AgentToolResultVO result = service.executeMutationTool(request);

        assertThat(result.getStatus()).isEqualTo("REJECTED");
        assertThat(result.getErrorCode()).isEqualTo("AGENT_IDEMPOTENCY_CONFLICT");
        verify(agentTaskMapper, never()).updateFromEvent(any(AgentTask.class));
    }

    /**
     * 构造测试任务。
     */
    private AgentTask task(String taskId, String userId) {
        AgentTask task = new AgentTask();
        task.setId(taskId);
        task.setUserId(userId);
        task.setTaskType("pure_read_query");
        task.setStatus("RUNNING");
        return task;
    }

    /**
     * 构造只读工具请求。
     */
    private AgentReadToolRequestDTO request(String toolName, Map<String, Object> arguments) {
        AgentReadToolRequestDTO request = new AgentReadToolRequestDTO();
        request.setTaskId("agent-task-1");
        request.setToolCallId("tool-call-1");
        request.setToolName(toolName);
        request.setArguments(new LinkedHashMap<>(arguments));
        return request;
    }

    /**
     * 构造变更工具请求。
     */
    private AgentMutationToolExecuteDTO mutationRequest(String toolName) {
        AgentMutationToolExecuteDTO request = new AgentMutationToolExecuteDTO();
        request.setTaskId("agent-task-1");
        request.setToolCallId("tool-call-mutation-1");
        request.setApprovalId("review-crud-1");
        request.setToolName(toolName);
        request.setIdempotencyKey("idem-1");
        request.setArguments(new LinkedHashMap<>(Map.of("reason", "用户确认保存")));
        return request;
    }

    /**
     * 读取变更工具结果数据。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> mutationData(AgentToolResultVO result) {
        return (Map<String, Object>) result.getData();
    }

    /**
     * 构造已批准 CRUD 审批。
     */
    private AgentHumanReview approvedCrudReview() {
        AgentHumanReview review = new AgentHumanReview();
        review.setId("review-crud-1");
        review.setTaskId("agent-task-1");
        review.setReviewType("CRUD");
        review.setStatus("APPROVED");
        review.setReviewedBy("7");
        review.setProposalJson("{}");
        review.setDecisionJson("{}");
        return review;
    }

    /**
     * 构造已有变更操作。
     */
    private AgentOperation operation(String operationId, String status) {
        AgentOperation operation = new AgentOperation();
        operation.setId(operationId);
        operation.setTaskId("agent-task-1");
        operation.setReviewId("review-crud-1");
        operation.setUserId("7");
        operation.setOperationType("TASK_CANCEL");
        operation.setResourceType("agent_task");
        operation.setResourceId("agent-task-1");
        operation.setStatus(status);
        operation.setBeforeSnapshotRef("agent-operation-snapshot:snapshot-before-1");
        operation.setAfterSnapshotRef("agent-operation-snapshot:snapshot-after-1");
        operation.setIdempotencyKey("idem-1");
        return operation;
    }
}
