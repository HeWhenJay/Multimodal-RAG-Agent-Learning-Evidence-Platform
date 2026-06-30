package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonAgentClient;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.dto.AgentConversationFolderCreateDTO;
import com.itxiang.evidence.dto.AgentConversationFolderUpdateDTO;
import com.itxiang.evidence.dto.AgentConversationMoveDTO;
import com.itxiang.evidence.dto.AgentConversationSummarySaveDTO;
import com.itxiang.evidence.dto.AgentOperationUndoDTO;
import com.itxiang.evidence.service.AgentMemoryService;
import com.itxiang.evidence.dto.AgentReviewDecisionDTO;
import com.itxiang.evidence.dto.AgentTaskEventDTO;
import com.itxiang.evidence.dto.AgentToolCallEventDTO;
import com.itxiang.evidence.dto.AgentTaskCreateDTO;
import com.itxiang.evidence.entity.AgentChatMessage;
import com.itxiang.evidence.entity.AgentConversationFolder;
import com.itxiang.evidence.entity.AgentConversationSummary;
import com.itxiang.evidence.entity.AgentHumanReview;
import com.itxiang.evidence.entity.AgentOperation;
import com.itxiang.evidence.entity.AgentOperationSnapshot;
import com.itxiang.evidence.entity.AgentTask;
import com.itxiang.evidence.entity.AgentToolCall;
import com.itxiang.evidence.mapper.AgentChatMessageMapper;
import com.itxiang.evidence.mapper.AgentConversationFolderMapper;
import com.itxiang.evidence.mapper.AgentConversationSummaryMapper;
import com.itxiang.evidence.mapper.AgentHumanReviewMapper;
import com.itxiang.evidence.mapper.AgentOperationMapper;
import com.itxiang.evidence.mapper.AgentOperationSnapshotMapper;
import com.itxiang.evidence.mapper.AgentTaskMapper;
import com.itxiang.evidence.mapper.AgentToolCallMapper;
import com.itxiang.evidence.service.AgentRuntimeStateAdapter;
import com.itxiang.evidence.service.AgentService;
import com.itxiang.evidence.vo.AgentChatMessageVO;
import com.itxiang.evidence.vo.AgentConversationFolderVO;
import com.itxiang.evidence.vo.AgentConversationSummaryVO;
import com.itxiang.evidence.vo.AgentConversationTreeVO;
import com.itxiang.evidence.vo.AgentContextRestoreVO;
import com.itxiang.evidence.vo.AgentHumanReviewVO;
import com.itxiang.evidence.vo.AgentMessagePageVO;
import com.itxiang.evidence.vo.AgentOperationVO;
import com.itxiang.evidence.vo.AgentTaskDetailVO;
import com.itxiang.evidence.vo.AgentTaskVO;
import com.itxiang.evidence.vo.AgentToolCallVO;
import com.itxiang.evidence.vo.AgentToolDefinitionVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

@Slf4j
@Service
@RequiredArgsConstructor
public class AgentServiceImpl implements AgentService {

    private static final Set<String> TASK_TYPES = Set.of("pure_read_query", "planning_task", "mutation_task");
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };
    private static final TypeReference<List<Map<String, Object>>> LIST_MAP_TYPE = new TypeReference<>() {
    };
    private static final int TASK_DETAIL_MESSAGE_WINDOW = 30;
    private static final int TASK_DETAIL_SUMMARY_WINDOW = 8;

    private final AgentTaskMapper agentTaskMapper;
    private final AgentConversationFolderMapper agentConversationFolderMapper;
    private final AgentChatMessageMapper agentChatMessageMapper;
    private final AgentConversationSummaryMapper agentConversationSummaryMapper;
    private final AgentToolCallMapper agentToolCallMapper;
    private final AgentHumanReviewMapper agentHumanReviewMapper;
    private final AgentOperationMapper agentOperationMapper;
    private final AgentOperationSnapshotMapper agentOperationSnapshotMapper;
    private final AgentMemoryService agentMemoryService;
    private final AgentRuntimeStateAdapter agentRuntimeStateAdapter;
    private final PythonAgentClient pythonAgentClient;
    private final AgentProperties agentProperties;
    private final ObjectMapper objectMapper;
    private final PlatformTransactionManager transactionManager;

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
        task.setFolderId(resolveFolderIdForUser(dto.getFolderId(), scopedUserId));
        task.setTaskType(taskType);
        task.setStatus("CREATED");
        task.setTitle(defaultTitle(dto));
        task.setInputJson(toJson(dto.getInput(), "{}"));
        task.setPlanJson("{}");
        task.setDraftJson("{}");
        task.setFinalJson("{}");
        agentTaskMapper.insert(task);
        appendChatMessage(
                task,
                "USER",
                "USER_GOAL",
                displayGoal(dto.getInput(), task.getTitle()),
                Map.of("input", dto.getInput() == null ? Map.of() : dto.getInput(), "taskType", taskType),
                "TASK_CREATED",
                task.getId(),
                "user_goal"
        );
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
        List<AgentToolCallVO> toolCalls = safeList(agentToolCallMapper.findByTaskId(task.getId())).stream()
                .map(this::toToolCallVO)
                .toList();
        List<AgentHumanReviewVO> reviews = safeList(agentHumanReviewMapper.findByTaskId(task.getId())).stream()
                .map(this::toReviewVO)
                .toList();
        List<AgentOperationVO> operations = safeList(agentOperationMapper.findByTaskId(task.getId())).stream()
                .map(this::toOperationVO)
                .toList();
        List<AgentChatMessageVO> messages = safeList(agentChatMessageMapper.findRecentByTaskId(task.getId(), TASK_DETAIL_MESSAGE_WINDOW)).stream()
                .map(this::toChatMessageVO)
                .toList();
        int summaryCount = agentConversationSummaryMapper.countByTaskId(task.getId());
        List<AgentConversationSummaryVO> summaries = safeList(agentConversationSummaryMapper.findRecentByTaskId(task.getId(), TASK_DETAIL_SUMMARY_WINDOW)).stream()
                .map(this::toConversationSummaryVO)
                .toList();
        Long oldestSequenceNo = oldestSequenceNo(messages);
        boolean hasMoreMessagesBefore = oldestSequenceNo != null
                && agentChatMessageMapper.countBeforeSequence(task.getId(), oldestSequenceNo) > 0;
        return AgentTaskDetailVO.builder()
                .id(task.getId())
                .folderId(task.getFolderId())
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
                .messages(messages)
                .summaries(summaries)
                .messageWindowLimit(TASK_DETAIL_MESSAGE_WINDOW)
                .hasMoreMessagesBefore(hasMoreMessagesBefore)
                .summaryWindowLimit(TASK_DETAIL_SUMMARY_WINDOW)
                .hasMoreSummaries(summaryCount > summaries.size())
                .createdAt(task.getCreatedAt())
                .updatedAt(task.getUpdatedAt())
                .build();
    }

    /**
     * 返回阶段化工具能力，前端可据此展示只读或审批规则。
     */
    @Override
    public List<AgentTaskVO> listRecentTasks(String userId, Integer limit) {
        String scopedUserId = requireUserId(userId);
        int safeLimit = limit == null ? 20 : Math.max(1, Math.min(limit, 50));
        return agentTaskMapper.findRecentByUserId(scopedUserId, safeLimit).stream()
                .map(this::toTaskVO)
                .toList();
    }

    /**
     * 查询当前用户某个任务的聊天消息记录。
     */
    @Override
    public AgentMessagePageVO listTaskMessages(String taskId,
                                               String userId,
                                               Long beforeSequenceNo,
                                               Long afterSequenceNo,
                                               Integer limit) {
        String scopedUserId = requireUserId(userId);
        AgentTask task = agentTaskMapper.findByIdAndUserId(requireText(taskId, "任务 ID 不能为空"), scopedUserId);
        if (task == null) {
            throw new IllegalArgumentException("Agent 任务不存在");
        }
        int safeLimit = clamp(limit, TASK_DETAIL_MESSAGE_WINDOW, 1, 100);
        List<AgentChatMessageVO> messages = safeList(agentChatMessageMapper
                .findPageBySequence(task.getId(), beforeSequenceNo, afterSequenceNo, safeLimit))
                .stream()
                .map(this::toChatMessageVO)
                .toList();
        Long oldestSequenceNo = oldestSequenceNo(messages);
        Long newestSequenceNo = newestSequenceNo(messages);
        boolean hasMoreBefore = oldestSequenceNo != null && agentChatMessageMapper.countBeforeSequence(task.getId(), oldestSequenceNo) > 0;
        boolean hasMoreAfter = newestSequenceNo != null && agentChatMessageMapper.countAfterSequence(task.getId(), newestSequenceNo) > 0;
        return AgentMessagePageVO.builder()
                .taskId(task.getId())
                .messages(messages)
                .oldestSequenceNo(oldestSequenceNo)
                .newestSequenceNo(newestSequenceNo)
                .hasMoreBefore(hasMoreBefore)
                .hasMoreAfter(hasMoreAfter)
                .limit(safeLimit)
                .build();
    }

    /**
     * 查询 Redis SSE 重连缓冲；未启用 Redis 时返回空列表。
     */
    @Override
    public List<Map<String, Object>> listTaskStreamEvents(String taskId, String userId) {
        String scopedUserId = requireUserId(userId);
        AgentTask task = agentTaskMapper.findByIdAndUserId(requireText(taskId, "任务 ID 不能为空"), scopedUserId);
        if (task == null) {
            throw new IllegalArgumentException("Agent 任务不存在");
        }
        return safeList(agentRuntimeStateAdapter.readSseEvents(task.getId()));
    }

    /**
     * 查询侧边栏会话树，未分类作为虚拟文件夹返回。
     */
    @Override
    public AgentConversationTreeVO listConversationTree(String userId, Integer limitPerFolder) {
        String scopedUserId = requireUserId(userId);
        int safeLimit = limitPerFolder == null ? 8 : Math.max(1, Math.min(limitPerFolder, 30));
        AgentConversationFolderVO unfiled = AgentConversationFolderVO.builder()
                .id(null)
                .name("未分类")
                .sortOrder(0)
                .conversations(agentTaskMapper.findUnfiledByUserId(scopedUserId, safeLimit).stream().map(this::toTaskVO).toList())
                .build();
        unfiled.setConversationCount(unfiled.getConversations().size());
        List<AgentConversationFolderVO> folders = agentConversationFolderMapper.findByUserId(scopedUserId).stream()
                .map(folder -> {
                    List<AgentTaskVO> conversations = agentTaskMapper.findByUserIdAndFolderId(scopedUserId, folder.getId(), safeLimit)
                            .stream()
                            .map(this::toTaskVO)
                            .toList();
                    return toFolderVO(folder, conversations);
                })
                .toList();
        return AgentConversationTreeVO.builder()
                .unfiled(unfiled)
                .folders(folders)
                .build();
    }

    /**
     * 创建当前用户会话文件夹。
     */
    @Override
    @Transactional
    public AgentConversationFolderVO createConversationFolder(AgentConversationFolderCreateDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        AgentConversationFolder folder = new AgentConversationFolder();
        folder.setId("agent-folder-" + UUID.randomUUID().toString().replace("-", ""));
        folder.setUserId(scopedUserId);
        folder.setName(truncate(requireText(dto.getName(), "文件夹名称不能为空"), 80));
        Integer nextSortOrder = agentConversationFolderMapper.nextSortOrder(scopedUserId);
        folder.setSortOrder(dto.getSortOrder() == null ? nextSortOrder : Math.max(0, dto.getSortOrder()));
        agentConversationFolderMapper.insert(folder);
        return toFolderVO(agentConversationFolderMapper.findByIdAndUserId(folder.getId(), scopedUserId), List.of());
    }

    /**
     * 更新当前用户会话文件夹。
     */
    @Override
    @Transactional
    public AgentConversationFolderVO updateConversationFolder(String folderId, AgentConversationFolderUpdateDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        AgentConversationFolder folder = requireFolder(folderId, scopedUserId);
        folder.setName(truncate(requireText(dto.getName(), "文件夹名称不能为空"), 80));
        if (dto.getSortOrder() != null) {
            folder.setSortOrder(Math.max(0, dto.getSortOrder()));
        }
        agentConversationFolderMapper.update(folder);
        List<AgentTaskVO> conversations = agentTaskMapper.findByUserIdAndFolderId(scopedUserId, folder.getId(), 8)
                .stream()
                .map(this::toTaskVO)
                .toList();
        return toFolderVO(agentConversationFolderMapper.findByIdAndUserId(folder.getId(), scopedUserId), conversations);
    }

    /**
     * 删除当前用户会话文件夹，会话保留并回到未分类。
     */
    @Override
    @Transactional
    public void deleteConversationFolder(String folderId, String userId) {
        String scopedUserId = requireUserId(userId);
        AgentConversationFolder folder = requireFolder(folderId, scopedUserId);
        agentTaskMapper.clearFolderForUser(folder.getId(), scopedUserId);
        agentConversationFolderMapper.deleteByIdAndUserId(folder.getId(), scopedUserId);
    }

    /**
     * 移动当前用户会话到指定文件夹。
     */
    @Override
    @Transactional
    public AgentTaskVO moveConversation(String taskId, AgentConversationMoveDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        AgentTask task = agentTaskMapper.findByIdAndUserId(requireText(taskId, "任务 ID 不能为空"), scopedUserId);
        if (task == null) {
            throw new IllegalArgumentException("Agent 任务不存在");
        }
        String folderId = resolveFolderIdForUser(dto == null ? null : dto.getFolderId(), scopedUserId);
        agentTaskMapper.updateFolder(task.getId(), scopedUserId, folderId);
        return toTaskVO(agentTaskMapper.findByIdAndUserId(task.getId(), scopedUserId));
    }

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
            updateConversationTitleIfPresent(task, event.getDraft());
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
        appendEventMessages(task, event, status);
        boolean terminal = terminalStatus(status);
        agentRuntimeStateAdapter.appendSseEvent(task.getId(), streamEventPayload(task, event, status));
        agentRuntimeStateAdapter.refreshTaskTtl(task.getUserId(), task.getId(), terminal);
        return Map.of("taskId", task.getId(), "accepted", true, "status", status);
    }

    /**
     * 恢复 Agent 上下文，Redis 未命中时也完全依赖 PostgreSQL 权威记录。
     */
    @Override
    public AgentContextRestoreVO restoreContext(String taskId,
                                                String query,
                                                Integer recentLimit,
                                                Integer summaryLimit,
                                                Integer bestWindowTokens) {
        AgentTask task = requireInternalTask(taskId);
        boolean terminal = terminalStatus(task.getStatus());
        var cachedContext = agentRuntimeStateAdapter.readContext(task.getUserId(), task.getId());
        if (cachedContext.isPresent()) {
            agentRuntimeStateAdapter.refreshTaskTtl(task.getUserId(), task.getId(), terminal);
            return cachedContext.get();
        }
        int safeRecentLimit = clamp(recentLimit, 10, 1, 30);
        int safeSummaryLimit = clamp(summaryLimit, 6, 1, 20);
        List<AgentChatMessageVO> messageWindow = agentChatMessageMapper.findRecentByTaskId(task.getId(), safeRecentLimit)
                .stream()
                .map(this::toChatMessageVO)
                .toList();
        List<AgentConversationSummaryVO> activeSummaries = agentConversationSummaryMapper
                .findByTaskIdAndStatus(task.getId(), "ACTIVE", safeSummaryLimit)
                .stream()
                .map(this::toConversationSummaryVO)
                .toList();
        List<AgentConversationSummaryVO> summarySegments = rankSummaries(task.getId(), query, safeSummaryLimit);
        int promptTarget = clamp(bestWindowTokens, 18_000, 4_000, 64_000);
        AgentConversationSummary latestCovered = agentConversationSummaryMapper.findLatestCoveredByTask(task.getId());
        String latestCoveredMessageEndId = latestCovered == null ? null : latestCovered.getCoveredMessageEndId();
        int uncompressedMessageCount = agentChatMessageMapper.countCompressionCandidates(task.getId(), latestCoveredMessageEndId, safeRecentLimit);
        int candidateLimit = compressionCandidateLimit(promptTarget);
        List<AgentChatMessageVO> compressionCandidates = agentChatMessageMapper
                .findCompressionCandidates(task.getId(), latestCoveredMessageEndId, safeRecentLimit, candidateLimit)
                .stream()
                .map(this::toChatMessageVO)
                .toList();
        int rawTokenEstimate = estimateMessages(messageWindow)
                + estimateMessages(compressionCandidates)
                + summarySegments.stream().mapToInt(item -> estimateTokens(item.getSummaryText())).sum();
        AgentContextRestoreVO restored = AgentContextRestoreVO.builder()
                .taskId(task.getId())
                .userId(task.getUserId())
                .messageWindow(messageWindow)
                .compressionCandidateMessages(compressionCandidates)
                .activeSummaries(activeSummaries)
                .summarySegments(summarySegments)
                .budgetMetadata(Map.of(
                        "promptTargetTokens", promptTarget,
                        "recentMessageLimit", safeRecentLimit,
                        "summaryLimit", safeSummaryLimit,
                        "uncompressedMessageCount", uncompressedMessageCount,
                        "compressionCandidateCount", compressionCandidates.size(),
                        "latestCoveredMessageEndId", defaultText(latestCoveredMessageEndId, ""),
                        "rawTokenEstimate", rawTokenEstimate,
                        "redisPolicy", "Redis 仅作短期热态缓存；恢复能力以 PostgreSQL 消息和摘要段为准",
                        "restoreSource", "postgresql"
                ))
                .build();
        agentRuntimeStateAdapter.writeContext(restored, terminal);
        return restored;
    }

    /**
     * 保存压缩摘要段；同一任务新的 ACTIVE 摘要会把旧 ACTIVE 标记为 SUPERSEDED。
     */
    @Override
    @Transactional
    public AgentConversationSummaryVO saveConversationSummary(String taskId, AgentConversationSummarySaveDTO dto) {
        AgentTask task = requireInternalTask(taskId);
        if (dto == null) {
            throw new IllegalArgumentException("摘要内容不能为空");
        }
        String summaryId = defaultText(dto.getSummaryId(), "agent-summary-" + UUID.randomUUID().toString().replace("-", ""));
        AgentConversationSummary existing = agentConversationSummaryMapper.findByIdAndTask(summaryId, task.getId());
        if (existing != null) {
            return toConversationSummaryVO(existing);
        }
        validateSummaryCoveredRange(task.getId(), dto.getCoveredMessageStartId(), dto.getCoveredMessageEndId());
        String status = defaultText(dto.getStatus(), "ACTIVE");
        if ("ACTIVE".equals(status)) {
            agentConversationSummaryMapper.supersedeActiveByTask(task.getId(), task.getUserId());
        }
        AgentConversationSummary summary = new AgentConversationSummary();
        summary.setId(summaryId);
        summary.setTaskId(task.getId());
        summary.setUserId(task.getUserId());
        summary.setSummaryType(defaultText(dto.getSummaryType(), "CONTEXT_COMPRESSION"));
        summary.setCoveredMessageStartId(dto.getCoveredMessageStartId());
        summary.setCoveredMessageEndId(dto.getCoveredMessageEndId());
        summary.setCoveredMessageCount(nonNegative(dto.getCoveredMessageCount()));
        summary.setRawTokenEstimate(nonNegative(dto.getRawTokenEstimate()));
        summary.setCompressedTokenEstimate(nonNegative(dto.getCompressedTokenEstimate()));
        Map<String, Object> summaryBody = dto.getSummary() == null ? Map.of() : dto.getSummary();
        String summaryText = defaultText(defaultText(dto.getSummaryText(), stringFromMap(summaryBody, "rollingSummary")), "暂无摘要");
        summary.setSummaryJson(toJson(summaryBody, "{}"));
        summary.setSummaryText(truncate(summaryText, 20_000));
        summary.setKeyFactsJson(toJson(dto.getKeyFacts() == null ? List.of() : dto.getKeyFacts(), "[]"));
        summary.setEvidenceRefsJson(toJson(dto.getEvidenceRefs() == null ? List.of() : dto.getEvidenceRefs(), "[]"));
        summary.setCompressionModel(dto.getCompressionModel());
        summary.setCompressionPromptVersion(defaultText(dto.getCompressionPromptVersion(), "agent-context-compression-v1"));
        summary.setCompressionVersion(dto.getCompressionVersion() == null ? 1 : Math.max(1, dto.getCompressionVersion()));
        summary.setStatus(status);
        summary.setDiagnosticsJson(toJson(dto.getDiagnostics() == null ? Map.of() : dto.getDiagnostics(), "{}"));
        agentConversationSummaryMapper.insert(summary);
        AgentConversationSummaryVO saved = toConversationSummaryVO(agentConversationSummaryMapper.findByIdAndTask(summary.getId(), task.getId()));
        appendChatMessage(
                task,
                "SYSTEM",
                "CONTEXT_SUMMARY",
                "早期上下文已压缩，保留最近原文窗口和可恢复摘要段。",
                Map.of("summaryId", summary.getId(), "coveredMessageCount", summary.getCoveredMessageCount(), "status", summary.getStatus()),
                "CONTEXT_COMPRESSED",
                summary.getId(),
                "context_summary_" + summary.getId()
        );
        agentRuntimeStateAdapter.updateSummary(task.getUserId(), task.getId(), saved, terminalStatus(task.getStatus()));
        return saved;
    }

    /**
     * 校验压缩摘要覆盖范围只能引用当前任务已有消息，且开始消息不能晚于结束消息。
     */
    private void validateSummaryCoveredRange(String taskId, String startId, String endId) {
        AgentChatMessage start = findCoveredMessage(taskId, startId, "开始");
        AgentChatMessage end = findCoveredMessage(taskId, endId, "结束");
        if (start != null && end != null) {
            long startSequence = start.getSequenceNo() == null ? 0L : start.getSequenceNo();
            long endSequence = end.getSequenceNo() == null ? 0L : end.getSequenceNo();
            if (startSequence > endSequence) {
                throw new IllegalArgumentException("摘要覆盖消息范围不合法：开始消息不能晚于结束消息");
            }
        }
    }

    /**
     * 查询摘要覆盖范围中的单条消息，空 ID 表示未声明边界。
     */
    private AgentChatMessage findCoveredMessage(String taskId, String messageId, String label) {
        if (messageId == null || messageId.isBlank()) {
            return null;
        }
        AgentChatMessage message = agentChatMessageMapper.findByIdAndTaskId(messageId.trim(), taskId);
        if (message == null) {
            throw new IllegalArgumentException("摘要覆盖" + label + "消息不存在或不属于当前任务");
        }
        return message;
    }

    /**
     * 按摘要覆盖范围或锚点回捞少量原文，避免 Python 直接查业务库。
     */
    @Override
    public List<AgentChatMessageVO> recallContextMessages(String taskId,
                                                          String summaryId,
                                                          String coveredMessageStartId,
                                                          String coveredMessageEndId,
                                                          String anchorMessageId,
                                                          Integer before,
                                                          Integer after,
                                                          Integer limit) {
        AgentTask task = requireInternalTask(taskId);
        String startId = coveredMessageStartId;
        String endId = coveredMessageEndId;
        if (summaryId != null && !summaryId.isBlank()) {
            AgentConversationSummary summary = agentConversationSummaryMapper.findByIdAndTask(summaryId.trim(), task.getId());
            if (summary == null) {
                throw new IllegalArgumentException("摘要段不存在");
            }
            startId = defaultText(startId, summary.getCoveredMessageStartId());
            endId = defaultText(endId, summary.getCoveredMessageEndId());
        }
        int safeBefore = clamp(before, 2, 0, 20);
        int safeAfter = clamp(after, 2, 0, 20);
        int safeLimit = clamp(limit, 12, 1, 40);
        List<AgentChatMessage> beforeMessages = agentChatMessageMapper.findBeforeContext(
                task.getId(), anchorMessageId, startId, safeBefore
        );
        int coreLimit = safeLimit;
        List<AgentChatMessage> coreMessages = agentChatMessageMapper.findCoreContext(
                task.getId(), anchorMessageId, startId, endId, coreLimit
        );
        List<AgentChatMessage> afterMessages = agentChatMessageMapper.findAfterContext(
                task.getId(), anchorMessageId, endId, safeAfter
        );
        List<AgentChatMessage> window = mergeContextMessages(beforeMessages, coreMessages, afterMessages, safeLimit);
        return window.stream().map(this::toChatMessageVO).toList();
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
        appendReviewDecisionMessage(task, review, decision, dto);
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
        appendOperationUndoMessage(task, operation, dto);
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
            appendChatMessage(
                    task,
                    "ASSISTANT",
                    "STATUS",
                    "REJECTED".equals(decision) ? "用户已拒绝审批，Agent 任务已取消。" : "用户要求修改，Agent 将等待调整后的继续指令。",
                    Map.of("reviewId", review.getId(), "decision", decision, "status", task.getStatus()),
                    "REVIEW_DECISION_APPLIED",
                    review.getId(),
                    "review_decision_status_" + review.getId()
            );
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
            appendTaskFinalMessage(task, "OUTPUT_REVIEW_APPROVED", "final_answer");
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
            task.setErrorMessage("内部 Agent 令牌不可用，无法恢复 Python Agent，请检查 EVIDENCE_AGENT_INTERNAL_TOKEN 或 .local/agent-internal-token");
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
        String skipReason = pythonStartSkipReason(task);
        if (skipReason != null) {
            markPythonStartSkipped(task, skipReason);
            return;
        }
        if (!shouldStartPythonAgent(task)) {
            return;
        }
        Runnable startAction = () -> CompletableFuture.runAsync(() -> startPythonAgent(task));
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
     * 返回阻止启动 Python Agent 的配置原因；不需要 Python 的任务返回 null。
     */
    private String pythonStartSkipReason(AgentTask task) {
        if (!Set.of("pure_read_query", "planning_task").contains(task.getTaskType())) {
            return null;
        }
        if (agentProperties.getInternalToken() == null || agentProperties.getInternalToken().isBlank()) {
            return "内部 Agent 令牌不可用，Java 已创建任务但不会调用 Python Agent。请检查 EVIDENCE_AGENT_INTERNAL_TOKEN 或 .local/agent-internal-token。";
        }
        return null;
    }

    /**
     * 将无法启动 Python 的配置问题回写到任务，避免前端长期等待。
     */
    private void markPythonStartSkipped(AgentTask task, String reason) {
        log.warn("跳过 Python Agent 启动: taskId={}, taskType={}, reason={}", task.getId(), task.getTaskType(), reason);
        task.setStatus("FAILED");
        task.setPythonThreadId(task.getId());
        task.setErrorCode("AGENT_INTERNAL_TOKEN_INVALID");
        task.setErrorMessage(reason);
        agentTaskMapper.updateFromEvent(task);
        appendErrorMessage(task, "AGENT_INTERNAL_TOKEN_INVALID", reason, "PYTHON_START_SKIPPED", "python_start_skipped");
    }

    /**
     * 调用 Python Agent 接收任务；启动失败时只记录脱敏错误摘要。
     */
    private void startPythonAgent(AgentTask task) {
        try {
            log.info("准备请求 Python Agent 启动任务: taskId={}, taskType={}", task.getId(), task.getTaskType());
            markPythonStartRunning(task);
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
     * 后台线程开始调用 Python 前先标记运行中，让前端立即进入响应界面。
     */
    private void markPythonStartRunning(AgentTask task) {
        AgentTask running = agentTaskMapper.findById(task.getId());
        if (running == null || !"CREATED".equals(running.getStatus())) {
            return;
        }
        running.setStatus("RUNNING");
        running.setPythonThreadId(task.getId());
        running.setErrorCode(null);
        running.setErrorMessage(null);
        agentTaskMapper.updateFromEvent(running);
        appendChatMessage(
                running,
                "ASSISTANT",
                "STATUS",
                "后端已接收任务，正在启动 Python Agent 并等待 LangGraph 回写计划和进度。",
                Map.of("status", running.getStatus()),
                "PYTHON_START_RUNNING",
                running.getId(),
                "python_start_running"
        );
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
        appendErrorMessage(failed, failed.getErrorCode(), failed.getErrorMessage(), "PYTHON_START_FAILED", "python_start_failed");
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
     * 将 Python 事件投影为可恢复的聊天消息。
     */
    private void appendEventMessages(AgentTask task, AgentTaskEventDTO event, String status) {
        if (event.getToolCall() != null) {
            appendToolMessage(task, event.getToolCall(), event.getEventType());
        }
        if (event.getReviewRequest() != null && !event.getReviewRequest().isEmpty()) {
            appendReviewRequestMessage(task, event);
        }
        if ("TASK_COMPLETED".equals(event.getEventType())) {
            appendFinalMessage(task, event);
            return;
        }
        if ("TASK_FAILED".equals(event.getEventType())) {
            appendErrorMessage(task, event.getErrorCode(), event.getErrorMessage(), event.getEventType(), "task_failed");
            return;
        }
        String message = eventMessage(event, status);
        if (message == null || message.isBlank()) {
            return;
        }
        appendChatMessage(
                task,
                "ASSISTANT",
                "DRAFT_UPDATED".equals(event.getEventType()) ? "STATUS" : "STATUS",
                message,
                eventPayload(event, status),
                event.getEventType(),
                eventSourceId(event),
                eventDedupeKey(event, message)
        );
    }

    /**
     * 记录工具观测消息，内容来自 Python toolCall 回写。
     */
    private void appendToolMessage(AgentTask task, AgentToolCallEventDTO event, String eventType) {
        String content = event.getErrorMessage();
        if (content == null || content.isBlank()) {
            content = toolObservationSummary(event);
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("toolName", defaultText(event.getToolName(), "unknown_tool"));
        payload.put("toolType", defaultText(event.getToolType(), "READ"));
        payload.put("status", defaultText(event.getStatus(), "RUNNING"));
        payload.put("response", event.getResponse() == null ? Map.of() : event.getResponse());
        payload.put("errorCode", event.getErrorCode());
        payload.put("errorMessage", event.getErrorMessage());
        appendChatMessage(
                task,
                "TOOL",
                "TOOL_OBSERVATION",
                content,
                payload,
                eventType,
                event.getId(),
                "tool_" + event.getId()
        );
    }

    /**
     * 记录人工审批请求消息，内容来自 Python reviewRequest。
     */
    private void appendReviewRequestMessage(AgentTask task, AgentTaskEventDTO event) {
        Map<String, Object> request = event.getReviewRequest();
        String reviewId = defaultText(String.valueOf(request.getOrDefault("id", "")), "review-" + stableHash(toJson(request, "{}")));
        String reviewType = defaultText(String.valueOf(request.getOrDefault("reviewType", "")), inferReviewType(event));
        String messageType = "OUTPUT".equals(reviewType) ? "OUTPUT_REVIEW" : "PLAN_REVIEW";
        Object proposal = request.get("proposal");
        String content = stringFromMap(request, "message");
        if ((content == null || content.isBlank()) && proposal instanceof Map<?, ?> proposalMap) {
            content = stringFromMap(castMap(proposalMap), "summary");
        }
        if (content == null || content.isBlank()) {
            content = "OUTPUT".equals(reviewType)
                    ? "输出草稿已生成，等待你确认后再完成任务。"
                    : "规划器已生成执行路线，等待你批准或要求修改。";
        }
        appendChatMessage(
                task,
                "ASSISTANT",
                messageType,
                content,
                request,
                event.getEventType(),
                reviewId,
                "review_" + reviewId
        );
    }

    /**
     * 记录用户审批决策消息。
     */
    private void appendReviewDecisionMessage(AgentTask task, AgentHumanReview review, String decision, AgentReviewDecisionDTO dto) {
        Map<String, Object> payload = decisionPayload(dto, decision);
        payload.put("reviewId", review.getId());
        payload.put("reviewType", review.getReviewType());
        appendChatMessage(
                task,
                "USER",
                "REVIEW_DECISION",
                reviewDecisionText(review, decision, dto),
                payload,
                "REVIEW_DECIDED",
                review.getId(),
                "review_decision_" + review.getId()
        );
    }

    /**
     * 记录最终回答消息，内容来自 final.answer 或 draft.answer。
     */
    private void appendFinalMessage(AgentTask task, AgentTaskEventDTO event) {
        Map<String, Object> finalResult = event.getFinalResult() == null ? readMap(task.getFinalJson()) : event.getFinalResult();
        String content = stringFromMap(finalResult, "answer");
        if (content == null || content.isBlank()) {
            content = stringFromMap(finalResult, "matchSummary");
        }
        if (content == null || content.isBlank()) {
            content = stringFromMap(event.getDraft(), "answer");
        }
        if (content == null || content.isBlank()) {
            content = stringFromMap(event.getDraft(), "message");
        }
        if (content == null || content.isBlank()) {
            content = "Agent 已完成任务，但后端未返回可展示的最终回答。";
        }
        appendChatMessage(
                task,
                "ASSISTANT",
                "FINAL_ANSWER",
                content,
                finalResult,
                event.getEventType(),
                task.getId(),
                "final_answer"
        );
    }

    /**
     * Java 直接完成审批输出时，将最终结果投影为聊天消息。
     */
    private void appendTaskFinalMessage(AgentTask task, String sourceEventType, String dedupeKey) {
        Map<String, Object> finalResult = readMap(task.getFinalJson());
        String content = stringFromMap(finalResult, "answer");
        if (content == null || content.isBlank()) {
            content = stringFromMap(finalResult, "matchSummary");
        }
        if (content == null || content.isBlank()) {
            content = stringFromMap(finalResult, "message");
        }
        if (content == null || content.isBlank()) {
            return;
        }
        appendChatMessage(
                task,
                "ASSISTANT",
                "FINAL_ANSWER",
                content,
                finalResult,
                sourceEventType,
                task.getId(),
                dedupeKey
        );
    }

    /**
     * 记录任务错误消息，内容来自后端错误字段。
     */
    private void appendErrorMessage(AgentTask task, String errorCode, String errorMessage, String eventType, String dedupeKey) {
        String content = defaultText(errorMessage, defaultText(errorCode, "Agent 当前无法完成该任务"));
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("status", task.getStatus());
        payload.put("errorCode", errorCode);
        payload.put("errorMessage", errorMessage);
        appendChatMessage(
                task,
                "ASSISTANT",
                "ERROR",
                content,
                payload,
                eventType,
                task.getId(),
                dedupeKey
        );
    }

    /**
     * 记录撤销操作消息。
     */
    private void appendOperationUndoMessage(AgentTask task, AgentOperation operation, AgentOperationUndoDTO dto) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("operationId", operation.getId());
        payload.put("operationType", operation.getOperationType());
        payload.put("resourceType", operation.getResourceType());
        payload.put("resourceId", operation.getResourceId());
        payload.put("reason", dto == null ? null : dto.getReason());
        appendChatMessage(
                task,
                "USER",
                "OPERATION_UNDO",
                "用户已撤销操作：" + operation.getOperationType(),
                payload,
                "OPERATION_UNDONE",
                operation.getId(),
                "operation_undo_" + operation.getId()
        );
    }

    /**
     * 新增或更新聊天消息，按 taskId + dedupeKey 幂等。
     */
    private void appendChatMessage(AgentTask task,
                                   String role,
                                   String messageType,
                                   String content,
                                   Object payload,
                                   String sourceEventType,
                                   String sourceId,
                                   String dedupeKey) {
        if (task == null || content == null || content.isBlank()) {
            return;
        }
        String safeDedupeKey = truncate(requireText(dedupeKey, "消息幂等键不能为空"), 220);
        AgentChatMessage message = new AgentChatMessage();
        message.setId("agent-msg-" + UUID.randomUUID().toString().replace("-", ""));
        message.setTaskId(task.getId());
        message.setUserId(task.getUserId());
        message.setRole(role);
        message.setMessageType(messageType);
        message.setContent(truncate(content.trim(), 8000));
        message.setPayloadJson(toJson(payload, "{}"));
        message.setSourceEventType(sourceEventType);
        message.setSourceId(sourceId);
        message.setDedupeKey(safeDedupeKey);
        runInMessageTransaction(() -> persistChatMessage(task, safeDedupeKey, message));
        agentRuntimeStateAdapter.updateMessage(task.getUserId(), task.getId(), toChatMessageVO(message), terminalStatus(task.getStatus()));
    }

    /**
     * 在事务中追加消息，确保任务行锁覆盖序号分配和写入。
     */
    private void persistChatMessage(AgentTask task, String safeDedupeKey, AgentChatMessage message) {
        agentTaskMapper.lockByIdForUpdate(task.getId());
        AgentChatMessage existing = agentChatMessageMapper.findByTaskIdAndDedupeKey(task.getId(), safeDedupeKey);
        if (existing == null) {
            Long nextSequenceNo = agentChatMessageMapper.nextSequenceNo(task.getId());
            message.setSequenceNo(nextSequenceNo == null || nextSequenceNo < 1 ? 1L : nextSequenceNo);
            agentChatMessageMapper.insert(message);
        } else {
            message.setId(existing.getId());
            message.setSequenceNo(existing.getSequenceNo());
            agentChatMessageMapper.updateByDedupeKey(message);
        }
    }

    /**
     * 已有外层事务时复用事务；后台线程追加消息时主动开启事务。
     */
    private void runInMessageTransaction(Runnable action) {
        if (TransactionSynchronizationManager.isActualTransactionActive()) {
            action.run();
            return;
        }
        new TransactionTemplate(transactionManager).executeWithoutResult(status -> action.run());
    }

    /**
     * 将聊天消息实体转换为前端 VO。
     */
    private AgentChatMessageVO toChatMessageVO(AgentChatMessage message) {
        return AgentChatMessageVO.builder()
                .id(message.getId())
                .taskId(message.getTaskId())
                .sequenceNo(message.getSequenceNo())
                .role(message.getRole())
                .messageType(message.getMessageType())
                .content(message.getContent())
                .payload(readMap(message.getPayloadJson()))
                .sourceEventType(message.getSourceEventType())
                .sourceId(message.getSourceId())
                .dedupeKey(message.getDedupeKey())
                .createdAt(message.getCreatedAt())
                .updatedAt(message.getUpdatedAt())
                .build();
    }

    /**
     * 校验文件夹归属，空值表示未分类。
     */
    private String resolveFolderIdForUser(String folderId, String userId) {
        if (folderId == null || folderId.isBlank()) {
            return null;
        }
        AgentConversationFolder folder = agentConversationFolderMapper.findByIdAndUserId(folderId.trim(), userId);
        if (folder == null) {
            throw new IllegalArgumentException("会话文件夹不存在");
        }
        return folder.getId();
    }

    /**
     * 获取当前用户的会话文件夹，不允许跨用户移动或修改。
     */
    private AgentConversationFolder requireFolder(String folderId, String userId) {
        String scopedFolderId = requireText(folderId, "会话文件夹 ID 不能为空");
        AgentConversationFolder folder = agentConversationFolderMapper.findByIdAndUserId(scopedFolderId, userId);
        if (folder == null) {
            throw new IllegalArgumentException("会话文件夹不存在");
        }
        return folder;
    }

    /**
     * 将文件夹实体和其中会话转换为侧边栏 VO。
     */
    private AgentConversationFolderVO toFolderVO(AgentConversationFolder folder, List<AgentTaskVO> conversations) {
        List<AgentTaskVO> safeConversations = conversations == null ? List.of() : conversations;
        return AgentConversationFolderVO.builder()
                .id(folder.getId())
                .name(folder.getName())
                .sortOrder(folder.getSortOrder())
                .conversationCount(safeConversations.size())
                .conversations(safeConversations)
                .createdAt(folder.getCreatedAt())
                .updatedAt(folder.getUpdatedAt())
                .build();
    }

    /**
     * 将任务实体转换为摘要 VO。
     */
    private AgentTaskVO toTaskVO(AgentTask task) {
        int summaryCount = agentConversationSummaryMapper.countByTaskId(task.getId());
        return AgentTaskVO.builder()
                .id(task.getId())
                .folderId(task.getFolderId())
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
                .summaryCount(summaryCount)
                .createdAt(task.getCreatedAt())
                .updatedAt(task.getUpdatedAt())
                .build();
    }

    /**
     * 将上下文压缩摘要转换为前端和 Python 恢复接口可用结构。
     */
    private AgentConversationSummaryVO toConversationSummaryVO(AgentConversationSummary summary) {
        return AgentConversationSummaryVO.builder()
                .id(summary.getId())
                .taskId(summary.getTaskId())
                .summaryType(summary.getSummaryType())
                .coveredMessageStartId(summary.getCoveredMessageStartId())
                .coveredMessageEndId(summary.getCoveredMessageEndId())
                .coveredMessageCount(summary.getCoveredMessageCount())
                .rawTokenEstimate(summary.getRawTokenEstimate())
                .compressedTokenEstimate(summary.getCompressedTokenEstimate())
                .summary(readMap(summary.getSummaryJson()))
                .summaryText(summary.getSummaryText())
                .keyFacts(readListMap(summary.getKeyFactsJson()))
                .evidenceRefs(readListMap(summary.getEvidenceRefsJson()))
                .compressionModel(summary.getCompressionModel())
                .compressionPromptVersion(summary.getCompressionPromptVersion())
                .compressionVersion(summary.getCompressionVersion())
                .status(summary.getStatus())
                .diagnostics(readMap(summary.getDiagnosticsJson()))
                .createdAt(summary.getCreatedAt())
                .updatedAt(summary.getUpdatedAt())
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
     * 提取用户原始目标作为第一条聊天消息。
     */
    private String displayGoal(Map<String, Object> input, String fallback) {
        if (input == null || input.isEmpty()) {
            return fallback;
        }
        String goal = stringFromMap(input, "goal");
        return defaultText(goal == null ? null : goal.split("\\n\\n")[0], fallback);
    }

    /**
     * 从事件中提取可展示的进度说明。
     */
    private String eventMessage(AgentTaskEventDTO event, String status) {
        if ("REVIEW_REQUESTED".equals(event.getEventType()) || event.getToolCall() != null) {
            return null;
        }
        if (event.getErrorMessage() != null && !event.getErrorMessage().isBlank()) {
            return event.getErrorMessage();
        }
        String draftMessage = stringFromMap(event.getDraft(), "message");
        if (draftMessage != null && !draftMessage.isBlank()) {
            return draftMessage;
        }
        return switch (event.getEventType()) {
            case "TASK_STARTED" -> "统一 Agent 图已启动，正在进入规划或只读执行流程。";
            case "MUTATION_PROPOSED" -> "Agent 已生成变更提案，等待具体审批。";
            default -> status == null || status.isBlank() ? null : "Agent 状态已更新为：" + status;
        };
    }

    /**
     * 构造事件消息 payload，保留后端明确回写的状态和摘要。
     */
    private Map<String, Object> eventPayload(AgentTaskEventDTO event, String status) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("eventType", event.getEventType());
        payload.put("status", status);
        payload.put("pythonThreadId", event.getPythonThreadId());
        payload.put("draft", event.getDraft() == null ? Map.of() : event.getDraft());
        payload.put("errorCode", event.getErrorCode());
        payload.put("errorMessage", event.getErrorMessage());
        return payload;
    }

    /**
     * 构造事件来源 ID，便于前端排查消息来源。
     */
    private String eventSourceId(AgentTaskEventDTO event) {
        if (event.getToolCall() != null && event.getToolCall().getId() != null) {
            return event.getToolCall().getId();
        }
        if (event.getReviewRequest() != null) {
            Object reviewId = event.getReviewRequest().get("id");
            if (reviewId != null) {
                return String.valueOf(reviewId);
            }
        }
        Map<String, Object> draft = event.getDraft() == null ? Map.of() : event.getDraft();
        return String.join(":",
                defaultText(event.getEventType(), "event"),
                defaultText(asText(draft.get("node")), "node"),
                defaultText(asText(draft.get("phase")), "phase"),
                defaultText(asText(draft.get("progressStatus")), "status"));
    }

    /**
     * 事件进度没有稳定 ID 时，用内容 hash 做幂等。
     */
    private String eventDedupeKey(AgentTaskEventDTO event, String message) {
        String source = eventSourceId(event);
        return "event_" + defaultText(event.getEventType(), "unknown").toLowerCase() + "_" + stableHash(source + "|" + message);
    }

    /**
     * 生成工具观测摘要，避免前端自行拼固定回答。
     */
    private String toolObservationSummary(AgentToolCallEventDTO event) {
        Map<String, Object> response = event.getResponse() == null ? Map.of() : event.getResponse();
        List<String> parts = new java.util.ArrayList<>();
        appendPart(parts, response, "summary", null);
        appendPart(parts, response, "evidenceCount", "证据 ");
        appendPart(parts, response, "answerLength", "回答长度 ");
        appendPart(parts, response, "expandedQueryCount", "扩展查询 ");
        appendPart(parts, response, "operationId", "操作 ");
        Object diagnostics = response.get("diagnosticKeys");
        if (diagnostics instanceof List<?> list) {
            parts.add("诊断 " + list.size());
        }
        if (!parts.isEmpty()) {
            return String.join(" · ", parts);
        }
        return defaultText(event.getToolName(), "工具") + " 已返回 " + defaultText(event.getStatus(), "RUNNING") + " 状态。";
    }

    /**
     * 将审批决策转换为历史消息文本。
     */
    private String reviewDecisionText(AgentHumanReview review, String decision, AgentReviewDecisionDTO dto) {
        String action = switch (decision) {
            case "APPROVED" -> "批准";
            case "REJECTED" -> "拒绝";
            default -> "要求修改";
        };
        String comment = dto == null ? null : dto.getComment();
        return action + " " + reviewTitle(review.getReviewType()) + (comment == null || comment.isBlank() ? "" : "：" + comment);
    }

    /**
     * 审批类型中文名称。
     */
    private String reviewTitle(String reviewType) {
        if ("OUTPUT".equals(reviewType)) {
            return "输出确认";
        }
        if ("CRUD".equals(reviewType)) {
            return "变更确认";
        }
        return "计划确认";
    }

    /**
     * 从 Map 中安全读取文本字段。
     */
    private String stringFromMap(Map<String, Object> body, String key) {
        if (body == null) {
            return null;
        }
        Object value = body.get(key);
        return value == null ? null : String.valueOf(value);
    }

    /**
     * 将通配 Map 转成可序列化 Map。
     */
    private Map<String, Object> castMap(Map<?, ?> source) {
        Map<String, Object> result = new LinkedHashMap<>();
        source.forEach((key, value) -> result.put(String.valueOf(key), value));
        return result;
    }

    /**
     * 按字段追加工具响应摘要片段。
     */
    private void appendPart(List<String> parts, Map<String, Object> body, String key, String prefix) {
        Object value = body.get(key);
        if (value == null || String.valueOf(value).isBlank()) {
            return;
        }
        parts.add((prefix == null ? "" : prefix) + value);
    }

    /**
     * 生成短 hash，用于无来源 ID 事件的幂等键。
     */
    private String stableHash(String value) {
        return Integer.toHexString(String.valueOf(value).hashCode());
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
     * 根据 Python Agent 回写的主题字段更新会话标题。
     */
    private void updateConversationTitleIfPresent(AgentTask task, Map<String, Object> draft) {
        if (task == null || draft == null || draft.isEmpty()) {
            return;
        }
        String title = defaultText(
                stringFromMap(draft, "conversationTitle"),
                defaultText(stringFromMap(draft, "topicTitle"), stringFromMap(draft, "conversationTopic"))
        );
        if (title == null || title.isBlank()) {
            return;
        }
        String normalized = truncate(title.replaceAll("\\s+", " ").trim(), 80);
        if (normalized.isBlank() || normalized.equals(task.getTitle())) {
            return;
        }
        task.setTitle(normalized);
        agentTaskMapper.updateTitle(task);
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
     * 读取 JSON 数组为 Map 列表。
     */
    private List<Map<String, Object>> readListMap(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return objectMapper.readValue(json, LIST_MAP_TYPE);
        } catch (Exception e) {
            return List.of();
        }
    }

    /**
     * 内部接口通过 taskId 反查用户和任务，不信任 Python 传入 userId。
     */
    private AgentTask requireInternalTask(String taskId) {
        AgentTask task = agentTaskMapper.findById(requireText(taskId, "任务 ID 不能为空"));
        if (task == null) {
            throw new IllegalArgumentException("Agent 任务不存在");
        }
        return task;
    }

    /**
     * 按 query 对历史摘要段做轻量关键词评分；无命中时回退最近摘要。
     */
    private List<AgentConversationSummaryVO> rankSummaries(String taskId, String query, int limit) {
        List<String> terms = queryTerms(query);
        return agentConversationSummaryMapper.findByTaskId(taskId).stream()
                .filter(item -> Set.of("ACTIVE", "SUPERSEDED", "HIGH_LOSS_RISK").contains(defaultText(item.getStatus(), "")))
                .map(this::toConversationSummaryVO)
                .sorted((left, right) -> Integer.compare(summaryScore(right, terms), summaryScore(left, terms)))
                .limit(limit)
                .toList();
    }

    /**
     * 统计摘要文本、关键事实和 evidence 引用中的关键词命中。
     */
    private int summaryScore(AgentConversationSummaryVO summary, List<String> terms) {
        if (terms.isEmpty()) {
            return "ACTIVE".equals(summary.getStatus()) ? 2 : 1;
        }
        String text = (defaultText(summary.getSummaryText(), "") + " "
                + toJson(summary.getSummary(), "{}") + " "
                + toJson(summary.getKeyFacts(), "[]") + " "
                + toJson(summary.getEvidenceRefs(), "[]")).toLowerCase();
        int score = "ACTIVE".equals(summary.getStatus()) ? 2 : 0;
        for (String term : terms) {
            if (text.contains(term)) {
                score += 3;
            }
        }
        return score;
    }

    /**
     * 拆分中英文关键词，供 Java fallback 检索摘要段。
     */
    private List<String> queryTerms(String query) {
        if (query == null || query.isBlank()) {
            return List.of();
        }
        String normalized = query.toLowerCase().replaceAll("[^\\p{IsHan}\\p{Alnum}]+", " ");
        return java.util.Arrays.stream(normalized.split("\\s+"))
                .map(String::trim)
                .filter(item -> item.length() >= 2)
                .limit(20)
                .toList();
    }

    /**
     * 合并 SQL 级上下文窗口，按消息 ID 去重并保留时间顺序。
     */
    private List<AgentChatMessage> mergeContextMessages(List<AgentChatMessage> beforeMessages,
                                                        List<AgentChatMessage> coreMessages,
                                                        List<AgentChatMessage> afterMessages,
                                                        int limit) {
        Map<String, AgentChatMessage> selected = new LinkedHashMap<>();
        appendContextMessages(selected, coreMessages, limit);
        int remaining = Math.max(0, limit - selected.size());
        int beforeTake = Math.min(remaining / 2 + remaining % 2, safeSize(beforeMessages));
        appendContextMessages(selected, tailMessages(beforeMessages, beforeTake), limit);
        remaining = Math.max(0, limit - selected.size());
        appendContextMessages(selected, headMessages(afterMessages, remaining), limit);
        remaining = Math.max(0, limit - selected.size());
        appendContextMessages(selected, tailMessages(beforeMessages, remaining), limit);
        Map<String, AgentChatMessage> merged = new LinkedHashMap<>();
        appendContextMessages(merged, beforeMessages, Integer.MAX_VALUE);
        appendContextMessages(merged, coreMessages, Integer.MAX_VALUE);
        appendContextMessages(merged, afterMessages, Integer.MAX_VALUE);
        List<AgentChatMessage> window = new java.util.ArrayList<>(merged.values());
        return window.stream()
                .filter(message -> selected.containsKey(message.getId()))
                .limit(limit)
                .toList();
    }

    /**
     * 追加单段消息窗口，过滤空 ID 避免覆盖。
     */
    private void appendContextMessages(Map<String, AgentChatMessage> merged, List<AgentChatMessage> messages, int limit) {
        if (messages == null || messages.isEmpty()) {
            return;
        }
        for (AgentChatMessage message : messages) {
            if (merged.size() >= limit) {
                return;
            }
            if (message != null && message.getId() != null && !message.getId().isBlank()) {
                merged.putIfAbsent(message.getId(), message);
            }
        }
    }

    /**
     * 取列表头部 N 条。
     */
    private List<AgentChatMessage> headMessages(List<AgentChatMessage> messages, int count) {
        if (messages == null || messages.isEmpty() || count <= 0) {
            return List.of();
        }
        return messages.subList(0, Math.min(count, messages.size()));
    }

    /**
     * 取列表尾部 N 条，用于保留最靠近核心窗口的前文。
     */
    private List<AgentChatMessage> tailMessages(List<AgentChatMessage> messages, int count) {
        if (messages == null || messages.isEmpty() || count <= 0) {
            return List.of();
        }
        int fromIndex = Math.max(0, messages.size() - count);
        return messages.subList(fromIndex, messages.size());
    }

    private int safeSize(List<?> items) {
        return items == null ? 0 : items.size();
    }

    /**
     * Mapper 在单测未 stub 时可能返回 null，业务侧统一按空列表处理。
     */
    private <T> List<T> safeList(List<T> items) {
        return items == null ? List.of() : items;
    }

    /**
     * 判断任务是否已经进入终态，用于 Redis TTL 从运行态切到完成态。
     */
    private boolean terminalStatus(String status) {
        return Set.of("COMPLETED", "FAILED", "CANCELED").contains(defaultText(status, ""));
    }

    /**
     * 提取当前消息窗口最早序号。
     */
    private Long oldestSequenceNo(List<AgentChatMessageVO> messages) {
        if (messages == null || messages.isEmpty()) {
            return null;
        }
        return messages.stream()
                .map(AgentChatMessageVO::getSequenceNo)
                .filter(value -> value != null)
                .min(Long::compareTo)
                .orElse(null);
    }

    /**
     * 提取当前消息窗口最新序号。
     */
    private Long newestSequenceNo(List<AgentChatMessageVO> messages) {
        if (messages == null || messages.isEmpty()) {
            return null;
        }
        return messages.stream()
                .map(AgentChatMessageVO::getSequenceNo)
                .filter(value -> value != null)
                .max(Long::compareTo)
                .orElse(null);
    }

    /**
     * 构造 SSE 重连缓冲事件，不保存完整资料正文。
     */
    private Map<String, Object> streamEventPayload(AgentTask task, AgentTaskEventDTO event, String status) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("taskId", task.getId());
        payload.put("status", status);
        payload.put("eventType", event.getEventType());
        payload.put("pythonThreadId", event.getPythonThreadId());
        payload.put("draft", event.getDraft() == null ? Map.of() : event.getDraft());
        payload.put("errorCode", event.getErrorCode());
        payload.put("errorMessage", event.getErrorMessage());
        if (event.getToolCall() != null) {
            payload.put("toolCallId", event.getToolCall().getId());
            payload.put("toolName", event.getToolCall().getToolName());
            payload.put("toolStatus", event.getToolCall().getStatus());
        }
        if (event.getReviewRequest() != null) {
            payload.put("reviewRequest", event.getReviewRequest());
        }
        payload.put("createdAt", OffsetDateTime.now().toString());
        return payload;
    }

    /**
     * 根据 best window 给恢复接口一个保守的候选消息上限，避免一次恢复拖入过多原文。
     */
    private int compressionCandidateLimit(int promptTargetTokens) {
        return Math.max(8, Math.min(40, promptTargetTokens / 800));
    }

    /**
     * 粗略估算消息窗口 token，用于 Python budget guard 判断。
     */
    private int estimateMessages(List<AgentChatMessageVO> messages) {
        return messages == null ? 0 : messages.stream().mapToInt(item -> estimateTokens(item.getContent())).sum();
    }

    /**
     * 使用字符数近似 token，中文场景按 2 字符约 1 token 保守估算。
     */
    private int estimateTokens(String text) {
        return text == null || text.isBlank() ? 0 : Math.max(1, text.length() / 2);
    }

    private int clamp(Integer value, int fallback, int min, int max) {
        int resolved = value == null ? fallback : value;
        return Math.max(min, Math.min(max, resolved));
    }

    private int nonNegative(Integer value) {
        return value == null ? 0 : Math.max(0, value);
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
