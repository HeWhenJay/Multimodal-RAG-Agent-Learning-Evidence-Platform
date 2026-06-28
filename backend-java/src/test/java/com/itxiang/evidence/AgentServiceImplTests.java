package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonAgentClient;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.dto.AgentOperationUndoDTO;
import com.itxiang.evidence.dto.AgentReviewDecisionDTO;
import com.itxiang.evidence.dto.AgentTaskCreateDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.dto.AgentToolCallEventDTO;
import com.itxiang.evidence.entity.AgentHumanReview;
import com.itxiang.evidence.entity.AgentOperation;
import com.itxiang.evidence.entity.AgentOperationSnapshot;
import com.itxiang.evidence.entity.AgentTask;
import com.itxiang.evidence.entity.AgentToolCall;
import com.itxiang.evidence.vo.AgentOperationVO;
import com.itxiang.evidence.vo.AgentTaskVO;
import com.itxiang.evidence.mapper.AgentHumanReviewMapper;
import com.itxiang.evidence.mapper.AgentOperationMapper;
import com.itxiang.evidence.mapper.AgentOperationSnapshotMapper;
import com.itxiang.evidence.mapper.AgentTaskMapper;
import com.itxiang.evidence.mapper.AgentToolCallMapper;
import com.itxiang.evidence.service.AgentMemoryService;
import com.itxiang.evidence.service.Impl.AgentServiceImpl;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AgentServiceImplTests {

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
    private PythonAgentClient pythonAgentClient;

    @TempDir
    private Path tempDir;

    private AgentProperties agentProperties;
    private AgentServiceImpl service;

    @BeforeEach
    void setUp() {
        agentProperties = new AgentProperties();
        agentProperties.setInternalToken("agent-secret");
        service = new AgentServiceImpl(
                agentTaskMapper,
                agentToolCallMapper,
                agentHumanReviewMapper,
                agentOperationMapper,
                agentOperationSnapshotMapper,
                agentMemoryService,
                pythonAgentClient,
                agentProperties,
                new ObjectMapper()
        );
    }

    @Test
    void createPureReadTaskStartsPythonAgentWhenInternalTokenConfigured() {
        AtomicReference<AgentTask> savedTask = new AtomicReference<>();
        doAnswer(invocation -> {
            savedTask.set(invocation.getArgument(0));
            return null;
        }).when(agentTaskMapper).insert(any(AgentTask.class));
        when(agentTaskMapper.findById(anyString())).thenAnswer(invocation -> savedTask.get());

        AgentTaskCreateDTO dto = new AgentTaskCreateDTO();
        dto.setTaskType("pure_read_query");
        dto.setTitle("查询 Redis 学习证据");
        dto.setInput(new LinkedHashMap<>(Map.of("goal", "Redis 学到了什么", "topK", 3)));

        service.createTask(dto, "7");

        ArgumentCaptor<Map<String, Object>> inputCaptor = ArgumentCaptor.forClass(Map.class);
        verify(pythonAgentClient).startTask(any(AgentTask.class), inputCaptor.capture());
        assertThat(savedTask.get().getTaskType()).isEqualTo("pure_read_query");
        assertThat(inputCaptor.getValue()).containsEntry("goal", "Redis 学到了什么");
    }

    @Test
    void createPlanningTaskStartsPythonAgentWhenInternalTokenConfigured() {
        AtomicReference<AgentTask> savedTask = new AtomicReference<>();
        doAnswer(invocation -> {
            savedTask.set(invocation.getArgument(0));
            return null;
        }).when(agentTaskMapper).insert(any(AgentTask.class));
        when(agentTaskMapper.findById(anyString())).thenAnswer(invocation -> savedTask.get());
        AgentTaskCreateDTO dto = new AgentTaskCreateDTO();
        dto.setTaskType("planning_task");
        dto.setTitle("后端实习 JD 适配分析");
        dto.setInput(new LinkedHashMap<>(Map.of("goal", "分析 JD", "jobDescription", "要求 Java 和 RAG")));

        service.createTask(dto, "7");

        ArgumentCaptor<AgentTask> taskCaptor = ArgumentCaptor.forClass(AgentTask.class);
        verify(pythonAgentClient).startTask(taskCaptor.capture(), any());
        assertThat(taskCaptor.getValue().getTaskType()).isEqualTo("planning_task");
    }

    @Test
    void createAgentTaskUsesLocalSharedTokenWhenInternalTokenMissing() {
        agentProperties.setInternalToken("");
        System.setProperty("evidence.agent.internal-token-file", tempDir.resolve("agent-internal-token").toString());
        try {
            AtomicReference<AgentTask> savedTask = new AtomicReference<>();
            doAnswer(invocation -> {
                savedTask.set(invocation.getArgument(0));
                return null;
            }).when(agentTaskMapper).insert(any(AgentTask.class));
            when(agentTaskMapper.findById(anyString())).thenAnswer(invocation -> savedTask.get());
            AgentTaskCreateDTO dto = new AgentTaskCreateDTO();
            dto.setTaskType("pure_read_query");
            dto.setTitle("查询 Redis 学习证据");
            dto.setInput(new LinkedHashMap<>(Map.of("goal", "Redis 学到了什么")));

            AgentTaskVO result = service.createTask(dto, "7");

            assertThat(result.getStatus()).isEqualTo("CREATED");
            assertThat(agentProperties.getInternalToken()).isNotBlank();
            verify(pythonAgentClient).startTask(any(), any());
            verify(agentTaskMapper, never()).updateFromEvent(any(AgentTask.class));
        } finally {
            System.clearProperty("evidence.agent.internal-token-file");
        }
    }

    @Test
    void handleCompletedEventUpdatesTaskAndToolCallObservation() {
        AgentTask task = task();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentToolCallMapper.findById("tool-call-1")).thenReturn(null);
        AgentTaskEventDTO event = new AgentTaskEventDTO();
        event.setEventType("TASK_COMPLETED");
        event.setStatus("COMPLETED");
        event.setPythonThreadId("agent-task-1");
        event.setFinalResult(new LinkedHashMap<>(Map.of(
                "answer", "Redis 证据集中在缓存淘汰和持久化",
                "evidenceIds", List.of("material-12-1")
        )));
        AgentToolCallEventDTO toolCall = new AgentToolCallEventDTO();
        toolCall.setId("tool-call-1");
        toolCall.setToolName("rag_query_probe_non_persistent");
        toolCall.setStatus("SUCCEEDED");
        toolCall.setOwnershipVerified(true);
        toolCall.setScope("current_user_or_authorized");
        toolCall.setResponse(new LinkedHashMap<>(Map.of("evidenceCount", 1)));
        event.setToolCall(toolCall);

        Map<String, Object> result = service.handleEvent("agent-task-1", event);

        assertThat(result).containsEntry("status", "COMPLETED");
        ArgumentCaptor<AgentTask> taskCaptor = ArgumentCaptor.forClass(AgentTask.class);
        verify(agentTaskMapper).updateFromEvent(taskCaptor.capture());
        assertThat(taskCaptor.getValue().getFinalJson()).contains("Redis 证据");
        ArgumentCaptor<AgentToolCall> toolCaptor = ArgumentCaptor.forClass(AgentToolCall.class);
        verify(agentToolCallMapper).insert(toolCaptor.capture());
        verify(agentToolCallMapper).updateResult(toolCaptor.getValue());
        assertThat(toolCaptor.getValue().getResponseJson()).contains("evidenceCount");
    }

    @Test
    void reviewRequestedEventCreatesPendingReview() {
        AgentTask task = planningTask();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentHumanReviewMapper.findById("review-plan-agent-task-1")).thenReturn(null);
        AgentTaskEventDTO event = new AgentTaskEventDTO();
        event.setEventType("REVIEW_REQUESTED");
        event.setStatus("WAITING_PLAN_REVIEW");
        event.setPythonThreadId("agent-task-1");
        event.setReviewRequest(new LinkedHashMap<>(Map.of(
                "id", "review-plan-agent-task-1",
                "reviewType", "PLAN",
                "proposal", Map.of("title", "计划", "tools", List.of("rag_query_probe_non_persistent"))
        )));

        Map<String, Object> result = service.handleEvent("agent-task-1", event);

        assertThat(result).containsEntry("status", "WAITING_PLAN_REVIEW");
        ArgumentCaptor<AgentHumanReview> reviewCaptor = ArgumentCaptor.forClass(AgentHumanReview.class);
        verify(agentHumanReviewMapper).insert(reviewCaptor.capture());
        assertThat(reviewCaptor.getValue().getReviewType()).isEqualTo("PLAN");
        assertThat(reviewCaptor.getValue().getStatus()).isEqualTo("PENDING");
        assertThat(reviewCaptor.getValue().getProposalJson()).contains("计划");
    }

    @Test
    void approvePlanReviewCallsPythonResume() {
        AgentTask task = planningTask();
        AgentHumanReview review = review("review-plan-agent-task-1", "PLAN");
        when(agentTaskMapper.findByIdAndUserId("agent-task-1", "7")).thenReturn(task);
        when(agentHumanReviewMapper.findById("review-plan-agent-task-1")).thenReturn(review);
        when(agentHumanReviewMapper.findByTaskId("agent-task-1")).thenReturn(List.of(review));
        when(agentToolCallMapper.findByTaskId("agent-task-1")).thenReturn(List.of());
        AgentReviewDecisionDTO dto = new AgentReviewDecisionDTO();
        dto.setDecision("APPROVED");
        dto.setComment("同意继续");

        service.decideReview("agent-task-1", "review-plan-agent-task-1", dto, "7");

        verify(agentHumanReviewMapper).updateDecision(any(AgentHumanReview.class));
        verify(pythonAgentClient).resumeTask(any(AgentTask.class), eq("PLAN"), eq("APPROVED"), any(), any());
    }

    @Test
    void approveOutputReviewCompletesTaskWithoutPythonResume() {
        AgentTask task = planningTask();
        task.setDraftJson("{\"matchSummary\":\"适配草稿\"}");
        AgentHumanReview review = review("review-output-agent-task-1", "OUTPUT");
        when(agentTaskMapper.findByIdAndUserId("agent-task-1", "7")).thenReturn(task);
        when(agentHumanReviewMapper.findById("review-output-agent-task-1")).thenReturn(review);
        when(agentHumanReviewMapper.findByTaskId("agent-task-1")).thenReturn(List.of(review));
        when(agentToolCallMapper.findByTaskId("agent-task-1")).thenReturn(List.of());
        AgentReviewDecisionDTO dto = new AgentReviewDecisionDTO();
        dto.setDecision("APPROVED");

        service.decideReview("agent-task-1", "review-output-agent-task-1", dto, "7");

        ArgumentCaptor<AgentTask> taskCaptor = ArgumentCaptor.forClass(AgentTask.class);
        verify(agentTaskMapper).updateFromEvent(taskCaptor.capture());
        assertThat(taskCaptor.getAllValues()).anySatisfy(updated -> {
            assertThat(updated.getStatus()).isEqualTo("COMPLETED");
            assertThat(updated.getFinalJson()).contains("适配草稿");
        });
    }

    @Test
    void approveOutputReviewWithSaveIntentResumesPythonForCrudReview() {
        AgentTask task = planningTask();
        task.setDraftJson("{\"matchSummary\":\"适配草稿\"}");
        task.setInputJson("{\"goal\":\"保存学习计划\",\"saveDraft\":true,\"toolHints\":[\"jd_learning_plan_save\"]}");
        AgentHumanReview review = review("review-output-agent-task-1", "OUTPUT");
        when(agentTaskMapper.findByIdAndUserId("agent-task-1", "7")).thenReturn(task);
        when(agentHumanReviewMapper.findById("review-output-agent-task-1")).thenReturn(review);
        when(agentHumanReviewMapper.findByTaskId("agent-task-1")).thenReturn(List.of(review));
        when(agentToolCallMapper.findByTaskId("agent-task-1")).thenReturn(List.of());
        AgentReviewDecisionDTO dto = new AgentReviewDecisionDTO();
        dto.setDecision("APPROVED");
        dto.setComment("确认保存");

        service.decideReview("agent-task-1", "review-output-agent-task-1", dto, "7");

        verify(pythonAgentClient).resumeTask(any(AgentTask.class), eq("OUTPUT"), eq("APPROVED"), any(), any());
        ArgumentCaptor<AgentTask> taskCaptor = ArgumentCaptor.forClass(AgentTask.class);
        verify(agentTaskMapper).updateFromEvent(taskCaptor.capture());
        assertThat(taskCaptor.getAllValues()).anySatisfy(updated -> assertThat(updated.getStatus()).isEqualTo("RUNNING"));
    }

    @Test
    void undoOperationRestoresBeforeSnapshotAndMarksUndone() {
        AgentOperation operation = operation("APPLIED_UNDOABLE", OffsetDateTime.now().plusMinutes(10));
        AgentTask task = planningTask();
        task.setStatus("COMPLETED");
        task.setDraftJson("{\"learningPlan\":\"新计划\"}");
        task.setFinalJson("{\"learningPlan\":\"新计划\"}");
        AgentOperationSnapshot before = beforeSnapshot();
        when(agentOperationMapper.findById("agent-operation-1")).thenReturn(operation);
        when(agentTaskMapper.findByIdAndUserId("agent-task-1", "7")).thenReturn(task);
        when(agentOperationSnapshotMapper.findByOperationIdAndType("agent-operation-1", "BEFORE")).thenReturn(before);
        AgentOperationUndoDTO dto = new AgentOperationUndoDTO();
        dto.setIdempotencyKey("undo-idem-1");
        dto.setReason("误点保存");

        AgentOperationVO result = service.undoOperation("agent-operation-1", dto, "7");

        assertThat(result.getStatus()).isEqualTo("UNDONE");
        ArgumentCaptor<AgentTask> taskCaptor = ArgumentCaptor.forClass(AgentTask.class);
        verify(agentTaskMapper).updateFromEvent(taskCaptor.capture());
        assertThat(taskCaptor.getValue().getStatus()).isEqualTo("WAITING_OUTPUT_REVIEW");
        assertThat(taskCaptor.getValue().getDraftJson()).contains("旧计划");
        assertThat(taskCaptor.getValue().getFinalJson()).isEqualTo("{}");
        ArgumentCaptor<AgentOperation> operationCaptor = ArgumentCaptor.forClass(AgentOperation.class);
        verify(agentOperationMapper).updateResult(operationCaptor.capture());
        assertThat(operationCaptor.getValue().getStatus()).isEqualTo("UNDONE");
    }

    @Test
    void undoExpiredOperationMarksExpiredAndFails() {
        AgentOperation operation = operation("APPLIED_UNDOABLE", OffsetDateTime.now().minusMinutes(1));
        when(agentOperationMapper.findById("agent-operation-1")).thenReturn(operation);
        AgentOperationUndoDTO dto = new AgentOperationUndoDTO();
        dto.setIdempotencyKey("undo-idem-1");

        org.assertj.core.api.Assertions.assertThatThrownBy(() -> service.undoOperation("agent-operation-1", dto, "7"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("AGENT_OPERATION_UNDO_EXPIRED");

        ArgumentCaptor<AgentOperation> operationCaptor = ArgumentCaptor.forClass(AgentOperation.class);
        verify(agentOperationMapper).updateResult(operationCaptor.capture());
        assertThat(operationCaptor.getValue().getStatus()).isEqualTo("UNDO_EXPIRED");
        verify(agentTaskMapper, org.mockito.Mockito.never()).updateFromEvent(any());
    }

    /**
     * 构造事件测试任务。
     */
    private AgentTask task() {
        AgentTask task = new AgentTask();
        task.setId("agent-task-1");
        task.setUserId("7");
        task.setTaskType("pure_read_query");
        task.setStatus("RUNNING");
        task.setTitle("只读任务");
        task.setInputJson("{}");
        task.setPlanJson("{}");
        task.setDraftJson("{}");
        task.setFinalJson("{}");
        return task;
    }

    /**
     * 构造规划任务。
     */
    private AgentTask planningTask() {
        AgentTask task = task();
        task.setTaskType("planning_task");
        task.setStatus("WAITING_PLAN_REVIEW");
        task.setTitle("规划任务");
        task.setInputJson("{\"goal\":\"分析 JD\"}");
        task.setPythonThreadId("agent-task-1");
        return task;
    }

    /**
     * 构造待审批记录。
     */
    private AgentHumanReview review(String reviewId, String reviewType) {
        AgentHumanReview review = new AgentHumanReview();
        review.setId(reviewId);
        review.setTaskId("agent-task-1");
        review.setReviewType(reviewType);
        review.setStatus("PENDING");
        review.setProposalJson("{}");
        review.setDecisionJson("{}");
        return review;
    }

    /**
     * 构造变更操作。
     */
    private AgentOperation operation(String status, OffsetDateTime undoDeadline) {
        AgentOperation operation = new AgentOperation();
        operation.setId("agent-operation-1");
        operation.setTaskId("agent-task-1");
        operation.setReviewId("review-crud-agent-task-1");
        operation.setUserId("7");
        operation.setOperationType("JD_PLAN_SAVE");
        operation.setResourceType("agent_task_draft");
        operation.setResourceId("agent-task-1");
        operation.setStatus(status);
        operation.setBeforeSnapshotRef("agent-operation-snapshot:snapshot-before-1");
        operation.setAfterSnapshotRef("agent-operation-snapshot:snapshot-after-1");
        operation.setIdempotencyKey("idem-1");
        operation.setUndoDeadline(undoDeadline);
        return operation;
    }

    /**
     * 构造撤销前快照。
     */
    private AgentOperationSnapshot beforeSnapshot() {
        AgentOperationSnapshot snapshot = new AgentOperationSnapshot();
        snapshot.setId("snapshot-before-1");
        snapshot.setOperationId("agent-operation-1");
        snapshot.setSnapshotType("BEFORE");
        snapshot.setResourceType("agent_task_draft");
        snapshot.setResourceId("agent-task-1");
        snapshot.setSnapshotJson("""
                {
                  "taskId": "agent-task-1",
                  "taskType": "planning_task",
                  "status": "WAITING_OUTPUT_REVIEW",
                  "plan": {},
                  "draft": {
                    "learningPlan": "旧计划"
                  },
                  "final": {},
                  "pythonThreadId": "agent-task-1",
                  "errorCode": null,
                  "errorMessage": null
                }
                """);
        snapshot.setContentHash("hash");
        return snapshot;
    }
}
