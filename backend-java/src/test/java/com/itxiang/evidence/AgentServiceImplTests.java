package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonAgentClient;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.dto.AgentConversationSummarySaveDTO;
import com.itxiang.evidence.dto.AgentOperationUndoDTO;
import com.itxiang.evidence.dto.AgentReviewDecisionDTO;
import com.itxiang.evidence.dto.AgentTaskCreateDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.dto.AgentToolCallEventDTO;
import com.itxiang.evidence.entity.AgentChatMessage;
import com.itxiang.evidence.entity.AgentConversationSummary;
import com.itxiang.evidence.entity.AgentHumanReview;
import com.itxiang.evidence.entity.AgentOperation;
import com.itxiang.evidence.entity.AgentOperationSnapshot;
import com.itxiang.evidence.entity.AgentTask;
import com.itxiang.evidence.entity.AgentToolCall;
import com.itxiang.evidence.vo.AgentOperationVO;
import com.itxiang.evidence.vo.AgentTaskVO;
import com.itxiang.evidence.mapper.AgentChatMessageMapper;
import com.itxiang.evidence.mapper.AgentConversationFolderMapper;
import com.itxiang.evidence.mapper.AgentConversationSummaryMapper;
import com.itxiang.evidence.mapper.AgentHumanReviewMapper;
import com.itxiang.evidence.mapper.AgentOperationMapper;
import com.itxiang.evidence.mapper.AgentOperationSnapshotMapper;
import com.itxiang.evidence.mapper.AgentTaskMapper;
import com.itxiang.evidence.mapper.AgentToolCallMapper;
import com.itxiang.evidence.service.AgentMemoryService;
import com.itxiang.evidence.service.AgentRuntimeStateAdapter;
import com.itxiang.evidence.service.Impl.AgentServiceImpl;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AgentServiceImplTests {

    @Mock
    private AgentTaskMapper agentTaskMapper;

    @Mock
    private AgentConversationFolderMapper agentConversationFolderMapper;

    @Mock
    private AgentChatMessageMapper agentChatMessageMapper;

    @Mock
    private AgentConversationSummaryMapper agentConversationSummaryMapper;

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
    private AgentRuntimeStateAdapter agentRuntimeStateAdapter;

    @Mock
    private PythonAgentClient pythonAgentClient;

    @Mock
    private PlatformTransactionManager transactionManager;

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
                agentConversationFolderMapper,
                agentChatMessageMapper,
                agentConversationSummaryMapper,
                agentToolCallMapper,
                agentHumanReviewMapper,
                agentOperationMapper,
                agentOperationSnapshotMapper,
                agentMemoryService,
                agentRuntimeStateAdapter,
                pythonAgentClient,
                agentProperties,
                new ObjectMapper(),
                transactionManager
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
        verify(pythonAgentClient, timeout(1000)).startTask(any(AgentTask.class), inputCaptor.capture());
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
        verify(pythonAgentClient, timeout(1000)).startTask(taskCaptor.capture(), any());
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

            assertThat(result.getStatus()).isIn("CREATED", "RUNNING");
            assertThat(agentProperties.getInternalToken()).isNotBlank();
            verify(pythonAgentClient, timeout(1000)).startTask(any(), any());
        } finally {
            System.clearProperty("evidence.agent.internal-token-file");
        }
    }

    @Test
    void eventInsertRechecksDedupeInsideTaskLockBeforeAllocatingMessageSequence() {
        AgentTask task = task();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentChatMessageMapper.nextSequenceNo(anyString())).thenReturn(4L);
        AgentTaskEventDTO event = new AgentTaskEventDTO();
        event.setEventType("TASK_STARTED");
        event.setStatus("RUNNING");
        event.setPythonThreadId("agent-task-1");

        service.handleEvent("agent-task-1", event);

        ArgumentCaptor<AgentChatMessage> messageCaptor = ArgumentCaptor.forClass(AgentChatMessage.class);
        verify(agentChatMessageMapper).insert(messageCaptor.capture());
        assertThat(messageCaptor.getValue().getSequenceNo()).isEqualTo(4L);
        String dedupeKey = messageCaptor.getValue().getDedupeKey();
        InOrder order = inOrder(agentTaskMapper, agentChatMessageMapper);
        order.verify(agentTaskMapper).lockByIdForUpdate("agent-task-1");
        order.verify(agentChatMessageMapper).findByTaskIdAndDedupeKey("agent-task-1", dedupeKey);
        order.verify(agentChatMessageMapper).nextSequenceNo("agent-task-1");
        order.verify(agentChatMessageMapper).insert(any(AgentChatMessage.class));
    }

    @Test
    void eventDedupeUpdateRechecksInsideTaskLockWithoutReallocatingSequence() {
        AgentTask task = task();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        AgentChatMessage existing = chatMessage("msg-existing", "旧回答");
        existing.setSequenceNo(9L);
        when(agentChatMessageMapper.findByTaskIdAndDedupeKey("agent-task-1", "final_answer")).thenReturn(existing);
        AgentTaskEventDTO event = new AgentTaskEventDTO();
        event.setEventType("TASK_COMPLETED");
        event.setStatus("COMPLETED");
        event.setFinalResult(new LinkedHashMap<>(Map.of("answer", "新的最终回答")));

        service.handleEvent("agent-task-1", event);

        ArgumentCaptor<AgentChatMessage> messageCaptor = ArgumentCaptor.forClass(AgentChatMessage.class);
        verify(agentChatMessageMapper).updateByDedupeKey(messageCaptor.capture());
        assertThat(messageCaptor.getValue().getSequenceNo()).isEqualTo(9L);
        InOrder order = inOrder(agentTaskMapper, agentChatMessageMapper);
        order.verify(agentTaskMapper).lockByIdForUpdate("agent-task-1");
        order.verify(agentChatMessageMapper).findByTaskIdAndDedupeKey("agent-task-1", "final_answer");
        order.verify(agentChatMessageMapper).updateByDedupeKey(any(AgentChatMessage.class));
        verify(agentChatMessageMapper, never()).nextSequenceNo(anyString());
        verify(agentChatMessageMapper, never()).insert(any(AgentChatMessage.class));
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
    void saveConversationSummaryReturnsExistingWhenSummaryIdReplayed() {
        AgentTask task = task();
        AgentConversationSummary existing = summary("agent-summary-1");
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentConversationSummaryMapper.findByIdAndTask("agent-summary-1", "agent-task-1")).thenReturn(existing);
        AgentConversationSummarySaveDTO dto = new AgentConversationSummarySaveDTO();
        dto.setSummaryId("agent-summary-1");
        dto.setStatus("ACTIVE");
        dto.setSummaryText("已存在摘要");

        var result = service.saveConversationSummary("agent-task-1", dto);

        assertThat(result.getId()).isEqualTo("agent-summary-1");
        assertThat(result.getSummaryText()).isEqualTo("已存在摘要");
        verify(agentConversationSummaryMapper, never()).supersedeActiveByTask(anyString(), anyString());
        verify(agentConversationSummaryMapper, never()).insert(any(AgentConversationSummary.class));
        verify(agentChatMessageMapper, never()).insert(any(AgentChatMessage.class));
    }

    @Test
    void saveConversationSummaryRejectsInvalidCoveredMessageId() {
        AgentTask task = task();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentConversationSummaryMapper.findByIdAndTask("agent-summary-invalid", "agent-task-1")).thenReturn(null);
        when(agentChatMessageMapper.findByIdAndTaskId("missing-msg", "agent-task-1")).thenReturn(null);
        AgentConversationSummarySaveDTO dto = summarySaveDto("agent-summary-invalid", "missing-msg", "msg-2");

        org.assertj.core.api.Assertions.assertThatThrownBy(() -> service.saveConversationSummary("agent-task-1", dto))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("摘要覆盖开始消息不存在");

        verify(agentConversationSummaryMapper, never()).insert(any(AgentConversationSummary.class));
        verify(agentChatMessageMapper, never()).insert(any(AgentChatMessage.class));
    }

    @Test
    void saveConversationSummaryRejectsReversedCoveredRange() {
        AgentTask task = task();
        AgentChatMessage start = chatMessage("msg-9", "较晚消息");
        start.setSequenceNo(9L);
        AgentChatMessage end = chatMessage("msg-2", "较早消息");
        end.setSequenceNo(2L);
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentConversationSummaryMapper.findByIdAndTask("agent-summary-reversed", "agent-task-1")).thenReturn(null);
        when(agentChatMessageMapper.findByIdAndTaskId("msg-9", "agent-task-1")).thenReturn(start);
        when(agentChatMessageMapper.findByIdAndTaskId("msg-2", "agent-task-1")).thenReturn(end);
        AgentConversationSummarySaveDTO dto = summarySaveDto("agent-summary-reversed", "msg-9", "msg-2");

        org.assertj.core.api.Assertions.assertThatThrownBy(() -> service.saveConversationSummary("agent-task-1", dto))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("开始消息不能晚于结束消息");

        verify(agentConversationSummaryMapper, never()).insert(any(AgentConversationSummary.class));
        verify(agentChatMessageMapper, never()).insert(any(AgentChatMessage.class));
    }

    @Test
    void saveConversationSummaryAcceptsValidCoveredRange() {
        AgentTask task = task();
        AgentChatMessage start = chatMessage("msg-1", "开始消息");
        start.setSequenceNo(1L);
        AgentChatMessage end = chatMessage("msg-4", "结束消息");
        end.setSequenceNo(4L);
        AtomicReference<AgentConversationSummary> savedSummary = new AtomicReference<>();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentConversationSummaryMapper.findByIdAndTask("agent-summary-valid", "agent-task-1"))
                .thenAnswer(invocation -> savedSummary.get());
        when(agentChatMessageMapper.findByIdAndTaskId("msg-1", "agent-task-1")).thenReturn(start);
        when(agentChatMessageMapper.findByIdAndTaskId("msg-4", "agent-task-1")).thenReturn(end);
        when(agentChatMessageMapper.nextSequenceNo("agent-task-1")).thenReturn(10L);
        doAnswer(invocation -> {
            savedSummary.set(invocation.getArgument(0));
            return null;
        }).when(agentConversationSummaryMapper).insert(any(AgentConversationSummary.class));
        AgentConversationSummarySaveDTO dto = summarySaveDto("agent-summary-valid", "msg-1", "msg-4");

        var result = service.saveConversationSummary("agent-task-1", dto);

        assertThat(result.getCoveredMessageStartId()).isEqualTo("msg-1");
        assertThat(result.getCoveredMessageEndId()).isEqualTo("msg-4");
        verify(agentConversationSummaryMapper).insert(any(AgentConversationSummary.class));
        verify(agentChatMessageMapper).insert(any(AgentChatMessage.class));
    }

    @Test
    void recallContextMessagesUsesWindowQueriesWithoutFullTaskLoad() {
        AgentTask task = task();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentChatMessageMapper.findBeforeContext("agent-task-1", "msg-3", null, 1))
                .thenReturn(List.of(chatMessage("msg-2", "上一条")));
        when(agentChatMessageMapper.findCoreContext("agent-task-1", "msg-3", null, null, 5))
                .thenReturn(List.of(chatMessage("msg-3", "锚点")));
        when(agentChatMessageMapper.findAfterContext("agent-task-1", "msg-3", null, 1))
                .thenReturn(List.of(chatMessage("msg-4", "下一条")));

        var result = service.recallContextMessages("agent-task-1", null, null, null, "msg-3", 1, 1, 5);

        assertThat(result).extracting("id").containsExactly("msg-2", "msg-3", "msg-4");
        verify(agentChatMessageMapper, never()).findByTaskId(anyString());
        verify(agentChatMessageMapper).findBeforeContext("agent-task-1", "msg-3", null, 1);
        verify(agentChatMessageMapper).findCoreContext("agent-task-1", "msg-3", null, null, 5);
        verify(agentChatMessageMapper).findAfterContext("agent-task-1", "msg-3", null, 1);
    }

    @Test
    void restoreContextReturnsCompressionCandidatesWithoutFullTaskLoad() {
        AgentTask task = task();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentRuntimeStateAdapter.readContext("7", "agent-task-1")).thenReturn(Optional.empty());
        when(agentChatMessageMapper.findRecentByTaskId("agent-task-1", 2))
                .thenReturn(List.of(chatMessage("msg-19", "最近消息 19"), chatMessage("msg-20", "最近消息 20")));
        when(agentConversationSummaryMapper.findByTaskIdAndStatus("agent-task-1", "ACTIVE", 2)).thenReturn(List.of());
        when(agentConversationSummaryMapper.findByTaskId("agent-task-1")).thenReturn(List.of());
        when(agentConversationSummaryMapper.findLatestCoveredByTask("agent-task-1")).thenReturn(null);
        when(agentChatMessageMapper.countCompressionCandidates("agent-task-1", null, 2)).thenReturn(18);
        when(agentChatMessageMapper.findCompressionCandidates("agent-task-1", null, 2, 8))
                .thenReturn(List.of(chatMessage("msg-1", "早期消息 1"), chatMessage("msg-2", "早期消息 2")));

        var result = service.restoreContext("agent-task-1", "Redis", 2, 2, 4000);

        assertThat(result.getMessageWindow()).extracting("id").containsExactly("msg-19", "msg-20");
        assertThat(result.getCompressionCandidateMessages()).extracting("id").containsExactly("msg-1", "msg-2");
        assertThat(result.getBudgetMetadata()).containsEntry("uncompressedMessageCount", 18);
        assertThat(result.getBudgetMetadata()).containsEntry("compressionCandidateCount", 2);
        verify(agentRuntimeStateAdapter).readContext("7", "agent-task-1");
        verify(agentRuntimeStateAdapter).writeContext(result, false);
        verify(agentChatMessageMapper, never()).findByTaskId(anyString());
    }

    @Test
    void restoreContextReturnsRedisHotStateWhenComplete() {
        AgentTask task = task();
        var cached = com.itxiang.evidence.vo.AgentContextRestoreVO.builder()
                .taskId("agent-task-1")
                .userId("7")
                .messageWindow(List.of(chatMessageVO("msg-hot", 20L, "Redis 热态消息")))
                .compressionCandidateMessages(List.of())
                .activeSummaries(List.of())
                .summarySegments(List.of())
                .budgetMetadata(Map.of("restoreSource", "redis"))
                .build();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentRuntimeStateAdapter.readContext("7", "agent-task-1")).thenReturn(Optional.of(cached));

        var result = service.restoreContext("agent-task-1", "Redis", 2, 2, 4000);

        assertThat(result.getMessageWindow()).extracting("id").containsExactly("msg-hot");
        verify(agentRuntimeStateAdapter).refreshTaskTtl("7", "agent-task-1", false);
        verify(agentChatMessageMapper, never()).findRecentByTaskId(anyString(), any());
        verify(agentRuntimeStateAdapter, never()).writeContext(any(), org.mockito.Mockito.anyBoolean());
    }

    @Test
    void saveConversationSummaryUpdatesRedisHotContext() {
        AgentTask task = task();
        AgentChatMessage start = chatMessage("msg-1", "开始消息");
        start.setSequenceNo(1L);
        AgentChatMessage end = chatMessage("msg-4", "结束消息");
        end.setSequenceNo(4L);
        AtomicReference<AgentConversationSummary> savedSummary = new AtomicReference<>();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentConversationSummaryMapper.findByIdAndTask("agent-summary-redis", "agent-task-1"))
                .thenAnswer(invocation -> savedSummary.get());
        when(agentChatMessageMapper.findByIdAndTaskId("msg-1", "agent-task-1")).thenReturn(start);
        when(agentChatMessageMapper.findByIdAndTaskId("msg-4", "agent-task-1")).thenReturn(end);
        when(agentChatMessageMapper.nextSequenceNo("agent-task-1")).thenReturn(10L);
        doAnswer(invocation -> {
            savedSummary.set(invocation.getArgument(0));
            return null;
        }).when(agentConversationSummaryMapper).insert(any(AgentConversationSummary.class));
        AgentConversationSummarySaveDTO dto = summarySaveDto("agent-summary-redis", "msg-1", "msg-4");

        var result = service.saveConversationSummary("agent-task-1", dto);

        assertThat(result.getId()).isEqualTo("agent-summary-redis");
        verify(agentRuntimeStateAdapter).updateSummary(eq("7"), eq("agent-task-1"), any(), eq(false));
        verify(agentRuntimeStateAdapter).updateMessage(eq("7"), eq("agent-task-1"), any(), eq(false));
    }

    @Test
    void recallContextMessagesKeepsCoreWhenLimitIsSmall() {
        AgentTask task = task();
        when(agentTaskMapper.findById("agent-task-1")).thenReturn(task);
        when(agentChatMessageMapper.findBeforeContext("agent-task-1", "msg-core", null, 5))
                .thenReturn(List.of(
                        chatMessage("msg-b1", "前文 1"),
                        chatMessage("msg-b2", "前文 2"),
                        chatMessage("msg-b3", "前文 3"),
                        chatMessage("msg-b4", "前文 4"),
                        chatMessage("msg-b5", "前文 5")
                ));
        when(agentChatMessageMapper.findCoreContext("agent-task-1", "msg-core", null, null, 3))
                .thenReturn(List.of(chatMessage("msg-core", "锚点消息")));
        when(agentChatMessageMapper.findAfterContext("agent-task-1", "msg-core", null, 5))
                .thenReturn(List.of(
                        chatMessage("msg-a1", "后文 1"),
                        chatMessage("msg-a2", "后文 2"),
                        chatMessage("msg-a3", "后文 3"),
                        chatMessage("msg-a4", "后文 4"),
                        chatMessage("msg-a5", "后文 5")
                ));

        var result = service.recallContextMessages("agent-task-1", null, null, null, "msg-core", 5, 5, 3);

        assertThat(result).extracting("id").containsExactly("msg-b5", "msg-core", "msg-a1");
        assertThat(result).extracting("id").contains("msg-core");
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
     * 构造上下文摘要段。
     */
    private AgentConversationSummary summary(String id) {
        AgentConversationSummary summary = new AgentConversationSummary();
        summary.setId(id);
        summary.setTaskId("agent-task-1");
        summary.setUserId("7");
        summary.setSummaryType("CONTEXT_COMPRESSION");
        summary.setCoveredMessageStartId("msg-1");
        summary.setCoveredMessageEndId("msg-4");
        summary.setCoveredMessageCount(4);
        summary.setRawTokenEstimate(1000);
        summary.setCompressedTokenEstimate(120);
        summary.setSummaryJson("{\"rollingSummary\":\"已存在摘要\"}");
        summary.setSummaryText("已存在摘要");
        summary.setKeyFactsJson("[]");
        summary.setEvidenceRefsJson("[]");
        summary.setCompressionModel("qwen-plus");
        summary.setCompressionPromptVersion("agent-context-compression-v1");
        summary.setCompressionVersion(1);
        summary.setStatus("ACTIVE");
        summary.setDiagnosticsJson("{}");
        return summary;
    }

    /**
     * 构造上下文摘要保存请求。
     */
    private AgentConversationSummarySaveDTO summarySaveDto(String id, String startId, String endId) {
        AgentConversationSummarySaveDTO dto = new AgentConversationSummarySaveDTO();
        dto.setSummaryId(id);
        dto.setSummaryType("CONTEXT_COMPRESSION");
        dto.setCoveredMessageStartId(startId);
        dto.setCoveredMessageEndId(endId);
        dto.setCoveredMessageCount(4);
        dto.setRawTokenEstimate(1000);
        dto.setCompressedTokenEstimate(120);
        dto.setSummary(Map.of("rollingSummary", "压缩摘要"));
        dto.setSummaryText("压缩摘要");
        dto.setKeyFacts(List.of());
        dto.setEvidenceRefs(List.of());
        dto.setCompressionModel("qwen-plus");
        dto.setCompressionPromptVersion("agent-context-compression-v1");
        dto.setCompressionVersion(1);
        dto.setStatus("ACTIVE");
        dto.setDiagnostics(Map.of());
        return dto;
    }

    /**
     * 构造聊天消息投影。
     */
    private AgentChatMessage chatMessage(String id, String content) {
        AgentChatMessage message = new AgentChatMessage();
        message.setId(id);
        message.setTaskId("agent-task-1");
        message.setUserId("7");
        message.setRole("USER");
        message.setMessageType("TEXT");
        message.setContent(content);
        message.setPayloadJson("{}");
        message.setCreatedAt(OffsetDateTime.now());
        message.setUpdatedAt(OffsetDateTime.now());
        return message;
    }

    /**
     * 构造聊天消息 VO。
     */
    private com.itxiang.evidence.vo.AgentChatMessageVO chatMessageVO(String id, Long sequenceNo, String content) {
        return com.itxiang.evidence.vo.AgentChatMessageVO.builder()
                .id(id)
                .taskId("agent-task-1")
                .sequenceNo(sequenceNo)
                .role("USER")
                .messageType("TEXT")
                .content(content)
                .payload(Map.of())
                .build();
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
