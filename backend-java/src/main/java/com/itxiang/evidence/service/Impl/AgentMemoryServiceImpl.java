package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonMemoryClient;
import com.itxiang.evidence.dto.AgentMemoryCreateDTO;
import com.itxiang.evidence.dto.AgentMemoryPatchDTO;
import com.itxiang.evidence.entity.AgentMemoryAudit;
import com.itxiang.evidence.entity.AgentMemoryItem;
import com.itxiang.evidence.entity.AgentMemoryVersion;
import com.itxiang.evidence.entity.AgentTask;
import com.itxiang.evidence.mapper.AgentMemoryAuditMapper;
import com.itxiang.evidence.mapper.AgentMemoryItemMapper;
import com.itxiang.evidence.mapper.AgentMemoryVersionMapper;
import com.itxiang.evidence.service.AgentMemoryService;
import com.itxiang.evidence.vo.AgentMemoryAuditVO;
import com.itxiang.evidence.vo.AgentMemoryDetailVO;
import com.itxiang.evidence.vo.AgentMemoryVO;
import com.itxiang.evidence.vo.AgentMemoryVersionVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

@Slf4j
@Service
@RequiredArgsConstructor
public class AgentMemoryServiceImpl implements AgentMemoryService {

    private static final Set<String> MEMORY_TYPES = Set.of("EPISODIC", "SEMANTIC", "PROCEDURAL", "PREFERENCE", "FACT");
    private static final Set<String> SCOPE_TYPES = Set.of("USER", "PROJECT", "MATERIAL", "TASK", "SESSION", "SYSTEM");
    private static final Set<String> TERMINAL_INACTIVE_STATUSES = Set.of("ARCHIVED", "SUPERSEDED", "REJECTED", "DELETED");
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };
    private static final TypeReference<List<Map<String, Object>>> LIST_MAP_TYPE = new TypeReference<>() {
    };
    private static final Pattern SECRET_PATTERN = Pattern.compile("(?i)(api[_-]?key|token|secret|password)\\s*[:=]");
    private static final Pattern SIGNED_URL_PATTERN = Pattern.compile("(?i)(x-amz-signature|signature=|expires=)");
    private static final Pattern PHONE_PATTERN = Pattern.compile("(?<!\\d)1[3-9]\\d{9}(?!\\d)");

    private final AgentMemoryItemMapper memoryItemMapper;
    private final AgentMemoryVersionMapper memoryVersionMapper;
    private final AgentMemoryAuditMapper memoryAuditMapper;
    private final PythonMemoryClient pythonMemoryClient;
    private final ObjectMapper objectMapper;

    /**
     * 创建用户显式授权记忆，并立即尝试索引。
     */
    @Override
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public AgentMemoryVO createMemory(AgentMemoryCreateDTO dto, String userId) {
        String scopedUserId = requireText(userId, "登录状态已失效");
        AgentMemoryItem item = newMemoryFromCreate(dto, scopedUserId);
        item.setStatus("PENDING_INDEX");
        item.setConsentSource("EXPLICIT_USER");
        memoryItemMapper.insert(item);
        insertAudit(item, "CREATE_CANDIDATE", "USER", null, item.getSourceHash(), "用户显式创建 Agent 记忆");
        activateAfterIndex(item);
        return toVO(memoryItemMapper.findByIdAndUserId(item.getId(), scopedUserId));
    }

    /**
     * 查询当前用户记忆列表。
     */
    @Override
    public List<AgentMemoryVO> listMemories(String userId, String status, String memoryType, String namespace, String scopeType) {
        return memoryItemMapper.findByUser(
                        requireText(userId, "登录状态已失效"),
                        emptyToNull(status),
                        emptyToNull(memoryType),
                        emptyToNull(namespace),
                        emptyToNull(scopeType)
                ).stream()
                .map(this::toVO)
                .toList();
    }

    /**
     * 查询记忆详情、版本链和脱敏审计。
     */
    @Override
    public AgentMemoryDetailVO getMemory(String memoryId, String userId) {
        String scopedUserId = requireText(userId, "登录状态已失效");
        AgentMemoryItem item = requireOwnedMemory(memoryId, scopedUserId);
        return AgentMemoryDetailVO.builder()
                .memory(toVO(item))
                .versions(memoryVersionMapper.findByMemoryId(item.getId(), scopedUserId).stream().map(this::toVersionVO).toList())
                .audits(memoryAuditMapper.findByMemoryId(item.getId(), scopedUserId).stream().map(this::toAuditVO).toList())
                .build();
    }

    /**
     * 用户确认后，记忆才可进入索引和默认检索。
     */
    @Override
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public AgentMemoryVO confirmMemory(String memoryId, String userId) {
        AgentMemoryItem item = requireOwnedMemory(memoryId, requireText(userId, "登录状态已失效"));
        if (!Set.of("PENDING_REVIEW", "INDEX_FAILED").contains(item.getStatus())) {
            throw new IllegalArgumentException("AGENT_MEMORY_REVIEW_REQUIRED：只有待确认或索引失败记忆可确认");
        }
        item.setStatus("PENDING_INDEX");
        item.setConsentSource("USER_REVIEW");
        memoryItemMapper.update(item);
        insertAudit(item, "CONFIRM", "USER", null, item.getSourceHash(), "用户确认待审 Agent 记忆");
        activateAfterIndex(item);
        return toVO(memoryItemMapper.findByIdAndUserId(item.getId(), item.getUserId()));
    }

    /**
     * 用户拒绝候选，默认检索不再召回。
     */
    @Override
    @Transactional
    public AgentMemoryVO rejectMemory(String memoryId, String userId) {
        AgentMemoryItem item = requireOwnedMemory(memoryId, requireText(userId, "登录状态已失效"));
        item.setStatus("REJECTED");
        memoryItemMapper.update(item);
        deleteIndexQuietly(item);
        insertAudit(item, "REJECT", "USER", null, item.getSourceHash(), "用户拒绝待审 Agent 记忆");
        return toVO(item);
    }

    /**
     * 修改记忆时追加新版本，旧版本不被原地覆盖。
     */
    @Override
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public AgentMemoryVO patchMemory(String memoryId, AgentMemoryPatchDTO dto, String userId) {
        AgentMemoryItem oldItem = requireOwnedMemory(memoryId, requireText(userId, "登录状态已失效"));
        ensureNotDeleted(oldItem);
        AgentMemoryPatchDTO patch = dto == null ? new AgentMemoryPatchDTO() : dto;
        String newScopeType = defaultText(emptyToNull(patch.getScopeType()), oldItem.getScopeType());
        String newScopeId = patch.getScopeId() == null ? oldItem.getScopeId() : emptyToNull(patch.getScopeId());
        ensureScopeNarrowed(oldItem.getScopeType(), oldItem.getScopeId(), newScopeType, newScopeId);

        AgentMemoryItem newItem = copyForNewVersion(oldItem);
        newItem.setNamespace(defaultText(emptyToNull(patch.getNamespace()), oldItem.getNamespace()));
        newItem.setSubjectKey(defaultText(emptyToNull(patch.getSubjectKey()), oldItem.getSubjectKey()));
        newItem.setScopeType(newScopeType);
        newItem.setScopeId(newScopeId);
        newItem.setContent(defaultText(emptyToNull(patch.getContent()), oldItem.getContent()));
        newItem.setSummary(defaultText(emptyToNull(patch.getSummary()), oldItem.getSummary()));
        rejectSensitive(newItem.getContent() + "\n" + newItem.getSummary());
        newItem.setSourceHash(sourceHash(newItem.getUserId(), newItem.getSourceTaskId(), newItem.getNamespace(), newItem.getSubjectKey(), newItem.getContent()));

        oldItem.setStatus("SUPERSEDED");
        memoryItemMapper.update(oldItem);
        memoryItemMapper.insert(newItem);
        insertVersion(newItem, oldItem.getId(), "REFINES", "ADD_NEW", "用户修改记忆后生成新版本", "USER");
        insertAudit(newItem, "EDIT", "USER", oldItem.getSourceHash(), newItem.getSourceHash(), "用户修改 Agent 记忆，已生成新版本");
        deleteIndexQuietly(oldItem);
        if ("PENDING_INDEX".equals(newItem.getStatus())) {
            activateAfterIndex(newItem);
        }
        return toVO(memoryItemMapper.findByIdAndUserId(newItem.getId(), newItem.getUserId()));
    }

    /**
     * 归档记忆时同步停用 Python 检索索引。
     */
    @Override
    @Transactional
    public AgentMemoryVO archiveMemory(String memoryId, String userId) {
        AgentMemoryItem item = requireOwnedMemory(memoryId, requireText(userId, "登录状态已失效"));
        ensureNotDeleted(item);
        item.setStatus("ARCHIVED");
        memoryItemMapper.update(item);
        deleteIndexQuietly(item);
        insertAudit(item, "ARCHIVE", "USER", item.getSourceHash(), item.getSourceHash(), "用户归档 Agent 记忆");
        return toVO(item);
    }

    /**
     * 删除记忆时擦除正文并停用索引。
     */
    @Override
    @Transactional
    public AgentMemoryVO deleteMemory(String memoryId, String userId) {
        AgentMemoryItem item = requireOwnedMemory(memoryId, requireText(userId, "登录状态已失效"));
        String beforeHash = item.getSourceHash();
        item.setStatus("DELETED");
        item.setContent("[已删除]");
        item.setSummary("[已删除]");
        item.setDeletedAt(OffsetDateTime.now());
        item.setSourceHash(sourceHash(item.getUserId(), item.getId(), "deleted", "deleted", beforeHash));
        memoryItemMapper.update(item);
        deleteIndexQuietly(item);
        insertAudit(item, "DELETE", "USER", beforeHash, item.getSourceHash(), "用户删除 Agent 记忆，正文已擦除");
        return toVO(item);
    }

    /**
     * Tool Gateway 使用当前任务 owner 检索可注入记忆。
     */
    @Override
    public List<Map<String, Object>> retrieveForTask(AgentTask task, Map<String, Object> arguments) {
        if (task == null || task.getUserId() == null || task.getUserId().isBlank()) {
            return List.of();
        }
        Map<String, Object> args = arguments == null ? Map.of() : arguments;
        String query = defaultText(text(args.get("query")), taskQuery(task));
        int topK = intValue(args.get("topK"), 5);
        List<Map<String, Object>> pythonMemories = queryPythonMemories(task, query, topK, args);
        List<Map<String, Object>> filtered = new ArrayList<>();
        for (Map<String, Object> raw : pythonMemories) {
            AgentMemoryItem item = memoryItemMapper.findByIdAndUserId(text(raw.get("memoryId")), task.getUserId());
            if (!contextAllowed(item, task, args)) {
                continue;
            }
            memoryItemMapper.markAccessed(item.getId(), item.getUserId());
            filtered.add(memoryContext(item, raw));
            if (filtered.size() >= topK) {
                break;
            }
        }
        if (!filtered.isEmpty()) {
            return filtered;
        }
        return fallbackMemories(task, query, topK, args);
    }

    /**
     * Tool Gateway 请求 Python 提炼候选，但不落库。
     */
    @Override
    public Map<String, Object> proposeCandidates(AgentTask task, Map<String, Object> arguments) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("taskId", task.getId());
        payload.put("userId", task.getUserId());
        payload.put("taskInput", valueOr(arguments, "taskInput", readMap(task.getInputJson())));
        payload.put("draft", valueOr(arguments, "draft", readMap(task.getDraftJson())));
        payload.put("final", valueOr(arguments, "final", readMap(task.getFinalJson())));
        payload.put("toolObservations", valueOr(arguments, "toolObservations", List.of()));
        try {
            return objectMapper.convertValue(pythonMemoryClient.extract(payload), MAP_TYPE);
        } catch (Exception e) {
            log.warn("Python 记忆候选提炼失败，使用 Java 降级候选: taskId={}, message={}", task.getId(), e.getMessage());
            return fallbackCandidatePayload(task, payload);
        }
    }

    /**
     * 从任务结果中保存 PENDING_REVIEW 候选，避免审批等同于长期记忆授权。
     */
    @Override
    @Transactional
    public int savePendingCandidatesFromTask(AgentTask task) {
        if (task == null || task.getUserId() == null) {
            return 0;
        }
        List<Map<String, Object>> candidates = pendingCandidates(readMap(task.getFinalJson()));
        if (candidates.isEmpty()) {
            candidates = pendingCandidates(readMap(task.getDraftJson()));
        }
        int saved = 0;
        for (Map<String, Object> candidate : candidates) {
            AgentMemoryItem item = newMemoryFromCandidate(candidate, task, false);
            if (memoryItemMapper.findByUserIdAndSourceHash(item.getUserId(), item.getSourceHash()) != null) {
                continue;
            }
            memoryItemMapper.insert(item);
            insertAudit(item, "CREATE_CANDIDATE", "JAVA_SERVICE", null, item.getSourceHash(), "任务完成后保存待确认 Agent 记忆候选");
            saved++;
        }
        return saved;
    }

    /**
     * 保存工具网关传入的单条候选。
     */
    @Override
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public AgentMemoryVO saveCandidateFromTool(AgentTask task, Map<String, Object> arguments, boolean explicitRemember) {
        AgentMemoryItem item = newMemoryFromCandidate(arguments == null ? Map.of() : arguments, task, explicitRemember);
        AgentMemoryItem existing = memoryItemMapper.findByUserIdAndSourceHash(item.getUserId(), item.getSourceHash());
        if (existing != null) {
            return toVO(existing);
        }
        memoryItemMapper.insert(item);
        insertAudit(item, "CREATE_CANDIDATE", "JAVA_SERVICE", null, item.getSourceHash(), "Tool Gateway 保存 Agent 记忆候选");
        if (explicitRemember) {
            activateAfterIndex(item);
        }
        return toVO(memoryItemMapper.findByIdAndUserId(item.getId(), item.getUserId()));
    }

    /**
     * 构造显式创建记忆实体。
     */
    private AgentMemoryItem newMemoryFromCreate(AgentMemoryCreateDTO dto, String userId) {
        AgentMemoryCreateDTO body = dto == null ? new AgentMemoryCreateDTO() : dto;
        String content = requireText(body.getContent(), "记忆内容不能为空");
        String summary = defaultText(emptyToNull(body.getSummary()), truncate(content, 160));
        rejectSensitive(content + "\n" + summary);
        AgentMemoryItem item = baseMemory(userId);
        item.setMemoryType(normalize(body.getMemoryType(), MEMORY_TYPES, "记忆类型非法"));
        item.setNamespace(requireText(body.getNamespace(), "记忆命名空间不能为空"));
        item.setScopeType(normalize(body.getScopeType(), SCOPE_TYPES, "记忆作用域非法"));
        if ("SYSTEM".equals(item.getScopeType())) {
            throw new IllegalArgumentException("AGENT_MEMORY_FORBIDDEN：普通用户不能创建 SYSTEM 记忆");
        }
        item.setScopeId(emptyToNull(body.getScopeId()));
        item.setSubjectKey(requireText(body.getSubjectKey(), "记忆主题键不能为空"));
        item.setContent(content);
        item.setSummary(summary);
        item.setEvidenceRefsJson(toJson(body.getEvidenceRefs(), "[]"));
        item.setImportance(clampScore(body.getImportance(), BigDecimal.valueOf(0.6)));
        item.setSourceHash(sourceHash(userId, null, item.getNamespace(), item.getSubjectKey(), content));
        return item;
    }

    /**
     * 从候选 Map 构造记忆实体。
     */
    private AgentMemoryItem newMemoryFromCandidate(Map<String, Object> candidate, AgentTask task, boolean explicitRemember) {
        Map<String, Object> body = candidate == null ? Map.of() : candidate;
        String content = requireText(text(body.get("content")), "候选记忆内容不能为空");
        String summary = defaultText(text(body.get("summary")), truncate(content, 160));
        rejectSensitive(content + "\n" + summary);
        AgentMemoryItem item = baseMemory(task.getUserId());
        item.setMemoryType(normalize(defaultText(text(body.get("memoryType")), "SEMANTIC"), MEMORY_TYPES, "记忆类型非法"));
        item.setNamespace(defaultText(text(body.get("namespace")), "agent_task"));
        item.setScopeType(normalize(defaultText(text(body.get("scopeType")), "USER"), SCOPE_TYPES, "记忆作用域非法"));
        item.setScopeId(emptyToNull(text(body.get("scopeId"))));
        item.setSubjectKey(defaultText(text(body.get("subjectKey")), "task_insight"));
        item.setContent(content);
        item.setSummary(summary);
        item.setEvidenceRefsJson(toJson(body.get("evidenceRefs"), "[]"));
        item.setSourceTaskId(defaultText(text(body.get("sourceTaskId")), task.getId()));
        item.setSourceToolCallId(emptyToNull(text(body.get("sourceToolCallId"))));
        item.setSourceReviewId(emptyToNull(text(body.get("sourceReviewId"))));
        item.setConfidence(clampScore(decimalValue(body.get("confidence")), BigDecimal.valueOf(0.62)));
        item.setImportance(clampScore(decimalValue(body.get("importance")), BigDecimal.valueOf(0.58)));
        item.setSensitivityLevel(defaultText(text(body.get("sensitivityLevel")), "LOW"));
        item.setConsentSource(explicitRemember ? "EXPLICIT_USER" : "AGENT_INFERRED");
        item.setStatus(explicitRemember ? "PENDING_INDEX" : "PENDING_REVIEW");
        item.setSourceHash(defaultText(text(body.get("sourceHash")), sourceHash(task.getUserId(), task.getId(), item.getNamespace(), item.getSubjectKey(), content)));
        return item;
    }

    /**
     * 生成记忆基础字段。
     */
    private AgentMemoryItem baseMemory(String userId) {
        AgentMemoryItem item = new AgentMemoryItem();
        item.setId("agent-memory-" + UUID.randomUUID().toString().replace("-", ""));
        item.setUserId(userId);
        item.setEvidenceRefsJson("[]");
        item.setStatus("PENDING_REVIEW");
        item.setConfidence(BigDecimal.valueOf(0.5));
        item.setImportance(BigDecimal.valueOf(0.5));
        item.setSensitivityLevel("LOW");
        item.setConsentSource("AGENT_INFERRED");
        item.setAccessCount(0);
        return item;
    }

    /**
     * 索引成功后激活记忆；失败保持不可检索。
     */
    private void activateAfterIndex(AgentMemoryItem item) {
        try {
            JsonNode result = pythonMemoryClient.upsertIndex(indexPayload(item));
            boolean indexed = result != null && result.path("indexed").asBoolean(false);
            item.setStatus(indexed ? "ACTIVE" : "INDEX_FAILED");
            memoryItemMapper.update(item);
            insertAudit(item, indexed ? "INDEX_UPSERT" : "INDEX_FAILED", "PYTHON_MEMORY_SERVICE",
                    item.getSourceHash(), item.getSourceHash(), indexed ? "Python 记忆索引写入成功" : "Python 记忆索引未成功");
        } catch (Exception e) {
            log.warn("Python 记忆索引失败: memoryId={}, message={}", item.getId(), e.getMessage());
            item.setStatus("INDEX_FAILED");
            memoryItemMapper.update(item);
            insertAudit(item, "INDEX_FAILED", "PYTHON_MEMORY_SERVICE", item.getSourceHash(), item.getSourceHash(), "Python 记忆索引失败：" + truncate(e.getMessage(), 300));
        }
    }

    /**
     * 请求 Python 停用或删除索引，失败时 Java 状态仍保证默认不可检索。
     */
    private void deleteIndexQuietly(AgentMemoryItem item) {
        try {
            pythonMemoryClient.deleteIndex(Map.of("memoryId", item.getId(), "userId", item.getUserId()));
            insertAudit(item, "INDEX_DELETE", "PYTHON_MEMORY_SERVICE", item.getSourceHash(), item.getSourceHash(), "Python 记忆索引已删除或停用");
        } catch (Exception e) {
            log.warn("Python 记忆索引删除失败: memoryId={}, message={}", item.getId(), e.getMessage());
            insertAudit(item, "INDEX_DELETE_FAILED", "PYTHON_MEMORY_SERVICE", item.getSourceHash(), item.getSourceHash(), "Python 记忆索引删除失败，Java 状态已不可检索");
        }
    }

    /**
     * 构造 Python 索引 payload，只传已授权记忆。
     */
    private Map<String, Object> indexPayload(AgentMemoryItem item) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("memoryId", item.getId());
        payload.put("userId", item.getUserId());
        payload.put("memoryType", item.getMemoryType());
        payload.put("namespace", item.getNamespace());
        payload.put("scopeType", item.getScopeType());
        payload.put("scopeId", item.getScopeId());
        payload.put("subjectKey", item.getSubjectKey());
        payload.put("content", item.getContent());
        payload.put("summary", item.getSummary());
        payload.put("retrievalText", retrievalText(item));
        payload.put("status", item.getStatus());
        payload.put("confidence", item.getConfidence());
        payload.put("importance", item.getImportance());
        payload.put("sensitivityLevel", item.getSensitivityLevel());
        return payload;
    }

    /**
     * 调 Python 检索记忆，失败时返回空列表。
     */
    private List<Map<String, Object>> queryPythonMemories(AgentTask task, String query, int topK, Map<String, Object> arguments) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("taskId", task.getId());
        payload.put("userId", task.getUserId());
        payload.put("query", query);
        payload.put("topK", topK);
        payload.put("namespaces", valueOr(arguments, "namespaces", List.of()));
        payload.put("memoryTypes", valueOr(arguments, "memoryTypes", List.of()));
        payload.put("allowedScopes", allowedScopes(task, arguments));
        try {
            JsonNode response = pythonMemoryClient.query(payload);
            JsonNode memories = response == null ? null : response.path("memories");
            if (memories == null || !memories.isArray()) {
                return List.of();
            }
            return objectMapper.convertValue(memories, LIST_MAP_TYPE);
        } catch (Exception e) {
            log.warn("Python 记忆检索失败，使用 Java 降级检索: taskId={}, message={}", task.getId(), e.getMessage());
            return List.of();
        }
    }

    /**
     * Java 降级检索只用 ACTIVE 记忆，并严格过滤 scope。
     */
    private List<Map<String, Object>> fallbackMemories(AgentTask task, String query, int topK, Map<String, Object> arguments) {
        String lowerQuery = defaultText(query, "").toLowerCase(Locale.ROOT);
        return memoryItemMapper.findActiveByUser(task.getUserId()).stream()
                .filter(item -> contextAllowed(item, task, arguments))
                .sorted(Comparator.comparing((AgentMemoryItem item) -> localRelevance(item, lowerQuery)).reversed()
                        .thenComparing(AgentMemoryItem::getUpdatedAt, Comparator.nullsLast(Comparator.reverseOrder())))
                .limit(topK)
                .map(item -> {
                    memoryItemMapper.markAccessed(item.getId(), item.getUserId());
                    return memoryContext(item, Map.of("score", localRelevance(item, lowerQuery)));
                })
                .toList();
    }

    /**
     * 构造注入上下文的短摘要。
     */
    private Map<String, Object> memoryContext(AgentMemoryItem item, Map<String, Object> raw) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("memoryId", item.getId());
        context.put("memoryType", item.getMemoryType());
        context.put("namespace", item.getNamespace());
        context.put("scope", item.getScopeType());
        context.put("scopeId", item.getScopeId());
        context.put("subjectKey", item.getSubjectKey());
        context.put("summary", item.getSummary());
        context.put("confidence", item.getConfidence());
        context.put("importance", item.getImportance());
        context.put("score", raw == null ? null : raw.get("score"));
        context.put("sourceRefs", sourceRefs(item));
        context.put("evidenceRefs", readList(item.getEvidenceRefsJson()));
        context.put("usagePolicy", "可作为个性化上下文，不可替代 RAG evidence。");
        return context;
    }

    /**
     * 判断 Python 返回记忆是否仍可用于当前任务。
     */
    private boolean contextAllowed(AgentMemoryItem item, AgentTask task, Map<String, Object> arguments) {
        if (item == null || !task.getUserId().equals(item.getUserId())) {
            return false;
        }
        if (!"ACTIVE".equals(item.getStatus()) || item.getDeletedAt() != null || "HIGH".equals(item.getSensitivityLevel())) {
            return false;
        }
        if (item.getValidUntil() != null && item.getValidUntil().isBefore(OffsetDateTime.now())) {
            return false;
        }
        return scopeAllowed(item, task, arguments == null ? Map.of() : arguments);
    }

    /**
     * 检查作用域是否命中当前任务授权范围。
     */
    private boolean scopeAllowed(AgentMemoryItem item, AgentTask task, Map<String, Object> arguments) {
        String scopeType = item.getScopeType();
        if ("USER".equals(scopeType)) {
            return item.getScopeId() == null || item.getScopeId().isBlank();
        }
        if ("TASK".equals(scopeType)) {
            return task.getId().equals(item.getScopeId());
        }
        if ("SESSION".equals(scopeType)) {
            return task.getId().equals(item.getScopeId()) || (task.getPythonThreadId() != null && task.getPythonThreadId().equals(item.getScopeId()));
        }
        Object allowedScopeIds = arguments.get("allowedScopeIds");
        if (allowedScopeIds instanceof List<?> list) {
            return list.stream().map(String::valueOf).anyMatch(value -> value.equals(item.getScopeId()));
        }
        return false;
    }

    /**
     * 生成允许传给 Python 的 scope 描述。
     */
    private List<Map<String, Object>> allowedScopes(AgentTask task, Map<String, Object> arguments) {
        List<Map<String, Object>> scopes = new ArrayList<>();
        scopes.add(Map.of("scopeType", "USER", "scopeId", ""));
        scopes.add(Map.of("scopeType", "TASK", "scopeId", task.getId()));
        scopes.add(Map.of("scopeType", "SESSION", "scopeId", defaultText(task.getPythonThreadId(), task.getId())));
        Object allowedScopeIds = arguments.get("allowedScopeIds");
        if (allowedScopeIds instanceof List<?> list) {
            for (Object item : list) {
                scopes.add(Map.of("scopeType", "PROJECT", "scopeId", String.valueOf(item)));
                scopes.add(Map.of("scopeType", "MATERIAL", "scopeId", String.valueOf(item)));
            }
        }
        return scopes;
    }

    /**
     * 构造本地降级候选。
     */
    private Map<String, Object> fallbackCandidatePayload(AgentTask task, Map<String, Object> payload) {
        Map<String, Object> input = ensureMap(payload.get("taskInput"));
        Map<String, Object> draft = ensureMap(payload.get("draft"));
        String goal = defaultText(text(input.get("goal")), "Agent 任务");
        String summary = defaultText(text(draft.get("matchSummary")), truncate(goal, 120));
        if (summary.length() < 8) {
            return Map.of("candidates", List.of(), "conflicts", List.of(), "provider", "java-fallback");
        }
        Map<String, Object> candidate = new LinkedHashMap<>();
        candidate.put("memoryType", "EPISODIC");
        candidate.put("namespace", "agent_task");
        candidate.put("scopeType", "USER");
        candidate.put("subjectKey", "recent_task_insight");
        candidate.put("content", "用户最近的 Agent 任务目标：" + truncate(goal, 180) + "；任务摘要：" + truncate(summary, 180));
        candidate.put("summary", truncate(summary, 160));
        candidate.put("confidence", 0.55);
        candidate.put("importance", 0.5);
        candidate.put("sensitivityLevel", "LOW");
        candidate.put("sourceTaskId", task.getId());
        return Map.of("candidates", List.of(candidate), "conflicts", List.of(), "provider", "java-fallback");
    }

    /**
     * 读取待确认候选列表。
     */
    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> pendingCandidates(Map<String, Object> body) {
        Object value = body.get("pendingMemoryCandidates");
        if (value instanceof List<?> list) {
            return list.stream()
                    .filter(Map.class::isInstance)
                    .map(item -> (Map<String, Object>) item)
                    .toList();
        }
        return List.of();
    }

    /**
     * 生成新版本实体。
     */
    private AgentMemoryItem copyForNewVersion(AgentMemoryItem oldItem) {
        AgentMemoryItem item = baseMemory(oldItem.getUserId());
        item.setMemoryType(oldItem.getMemoryType());
        item.setNamespace(oldItem.getNamespace());
        item.setScopeType(oldItem.getScopeType());
        item.setScopeId(oldItem.getScopeId());
        item.setSubjectKey(oldItem.getSubjectKey());
        item.setContent(oldItem.getContent());
        item.setSummary(oldItem.getSummary());
        item.setEvidenceRefsJson(oldItem.getEvidenceRefsJson());
        item.setSourceTaskId(oldItem.getSourceTaskId());
        item.setSourceToolCallId(oldItem.getSourceToolCallId());
        item.setSourceReviewId(oldItem.getSourceReviewId());
        item.setConfidence(oldItem.getConfidence());
        item.setImportance(oldItem.getImportance());
        item.setSensitivityLevel(oldItem.getSensitivityLevel());
        item.setConsentSource(oldItem.getConsentSource());
        item.setValidFrom(oldItem.getValidFrom());
        item.setValidUntil(oldItem.getValidUntil());
        item.setStatus("ACTIVE".equals(oldItem.getStatus()) ? "PENDING_INDEX" : "PENDING_REVIEW");
        return item;
    }

    /**
     * 写入版本关系。
     */
    private void insertVersion(AgentMemoryItem item,
                               String previousMemoryId,
                               String relationType,
                               String decision,
                               String reason,
                               String decidedBy) {
        AgentMemoryVersion version = new AgentMemoryVersion();
        version.setId("agent-memory-version-" + UUID.randomUUID().toString().replace("-", ""));
        version.setMemoryId(item.getId());
        version.setPreviousMemoryId(previousMemoryId);
        version.setRelationType(relationType);
        version.setDecision(decision);
        version.setReason(reason);
        version.setDecidedBy(decidedBy);
        version.setUserId(item.getUserId());
        memoryVersionMapper.insert(version);
    }

    /**
     * 写入脱敏审计。
     */
    private void insertAudit(AgentMemoryItem item,
                             String action,
                             String actorType,
                             String beforeHash,
                             String afterHash,
                             String summary) {
        AgentMemoryAudit audit = new AgentMemoryAudit();
        audit.setId("agent-memory-audit-" + UUID.randomUUID().toString().replace("-", ""));
        audit.setMemoryId(item.getId());
        audit.setUserId(item.getUserId());
        audit.setTaskId(item.getSourceTaskId());
        audit.setAction(action);
        audit.setActorType(actorType);
        audit.setBeforeHash(beforeHash);
        audit.setAfterHash(afterHash);
        audit.setSummary(truncate(defaultText(summary, "Agent 记忆状态变更"), 1000));
        memoryAuditMapper.insert(audit);
    }

    /**
     * 读取当前用户记忆，找不到时按统一错误码抛出。
     */
    private AgentMemoryItem requireOwnedMemory(String memoryId, String userId) {
        AgentMemoryItem item = memoryItemMapper.findByIdAndUserId(requireText(memoryId, "记忆 ID 不能为空"), userId);
        if (item == null) {
            throw new IllegalArgumentException("AGENT_MEMORY_NOT_FOUND：记忆不存在或不属于当前用户");
        }
        return item;
    }

    /**
     * 删除记忆不可再修改。
     */
    private void ensureNotDeleted(AgentMemoryItem item) {
        if ("DELETED".equals(item.getStatus()) || item.getDeletedAt() != null) {
            throw new IllegalArgumentException("AGENT_MEMORY_DELETED：记忆已删除");
        }
    }

    /**
     * PATCH scope 只能收窄，不能放大。
     */
    private void ensureScopeNarrowed(String oldScopeType, String oldScopeId, String newScopeType, String newScopeId) {
        int oldRank = scopeRank(oldScopeType);
        int newRank = scopeRank(newScopeType);
        if (newRank < oldRank) {
            throw new IllegalArgumentException("AGENT_MEMORY_SCOPE_ESCALATION：记忆作用域只能收窄，不能放大");
        }
        if (oldScopeType.equals(newScopeType)
                && oldScopeId != null
                && newScopeId != null
                && !oldScopeId.equals(newScopeId)) {
            throw new IllegalArgumentException("AGENT_MEMORY_SCOPE_ESCALATION：同级作用域不能切换到其他资源");
        }
    }

    /**
     * 作用域越大 rank 越低。
     */
    private int scopeRank(String scopeType) {
        return switch (scopeType) {
            case "SYSTEM" -> 0;
            case "USER" -> 1;
            case "PROJECT" -> 2;
            case "MATERIAL" -> 3;
            case "TASK", "SESSION" -> 4;
            default -> throw new IllegalArgumentException("AGENT_MEMORY_VALIDATION_FAILED：非法作用域");
        };
    }

    /**
     * 拒绝敏感内容进入记忆。
     */
    private void rejectSensitive(String text) {
        String value = defaultText(text, "");
        if (SECRET_PATTERN.matcher(value).find() || SIGNED_URL_PATTERN.matcher(value).find() || PHONE_PATTERN.matcher(value).find()) {
            throw new IllegalArgumentException("AGENT_MEMORY_SENSITIVE_REJECTED：记忆内容疑似包含敏感信息");
        }
    }

    /**
     * 转换为前端 VO。
     */
    private AgentMemoryVO toVO(AgentMemoryItem item) {
        return AgentMemoryVO.builder()
                .id(item.getId())
                .userId(item.getUserId())
                .memoryType(item.getMemoryType())
                .namespace(item.getNamespace())
                .scopeType(item.getScopeType())
                .scopeId(item.getScopeId())
                .subjectKey(item.getSubjectKey())
                .content(item.getContent())
                .summary(item.getSummary())
                .evidenceRefs(readList(item.getEvidenceRefsJson()))
                .sourceTaskId(item.getSourceTaskId())
                .sourceToolCallId(item.getSourceToolCallId())
                .sourceReviewId(item.getSourceReviewId())
                .status(item.getStatus())
                .confidence(item.getConfidence())
                .importance(item.getImportance())
                .sensitivityLevel(item.getSensitivityLevel())
                .consentSource(item.getConsentSource())
                .accessCount(item.getAccessCount())
                .lastAccessedAt(item.getLastAccessedAt())
                .validFrom(item.getValidFrom())
                .validUntil(item.getValidUntil())
                .deletedAt(item.getDeletedAt())
                .createdAt(item.getCreatedAt())
                .updatedAt(item.getUpdatedAt())
                .build();
    }

    /**
     * 转换版本 VO。
     */
    private AgentMemoryVersionVO toVersionVO(AgentMemoryVersion version) {
        return AgentMemoryVersionVO.builder()
                .id(version.getId())
                .memoryId(version.getMemoryId())
                .previousMemoryId(version.getPreviousMemoryId())
                .relationType(version.getRelationType())
                .decision(version.getDecision())
                .reason(version.getReason())
                .decidedBy(version.getDecidedBy())
                .createdAt(version.getCreatedAt())
                .build();
    }

    /**
     * 转换审计 VO，不返回哈希细节。
     */
    private AgentMemoryAuditVO toAuditVO(AgentMemoryAudit audit) {
        return AgentMemoryAuditVO.builder()
                .id(audit.getId())
                .memoryId(audit.getMemoryId())
                .taskId(audit.getTaskId())
                .action(audit.getAction())
                .actorType(audit.getActorType())
                .summary(audit.getSummary())
                .createdAt(audit.getCreatedAt())
                .build();
    }

    /**
     * 记忆检索文本只使用摘要、主题和短内容。
     */
    private String retrievalText(AgentMemoryItem item) {
        return String.join("\n",
                item.getNamespace(),
                item.getSubjectKey(),
                item.getSummary(),
                truncate(item.getContent(), 500)
        );
    }

    /**
     * 任务默认查询文本。
     */
    private String taskQuery(AgentTask task) {
        Map<String, Object> input = readMap(task.getInputJson());
        return defaultText(text(input.get("goal")), defaultText(task.getTitle(), task.getId()));
    }

    /**
     * 计算本地降级相关性。
     */
    private double localRelevance(AgentMemoryItem item, String lowerQuery) {
        String haystack = (item.getSummary() + "\n" + item.getContent() + "\n" + item.getSubjectKey()).toLowerCase(Locale.ROOT);
        double score = 0.0;
        for (String token : lowerQuery.split("\\s+")) {
            if (!token.isBlank() && haystack.contains(token)) {
                score += 1.0;
            }
        }
        score += safeDouble(item.getImportance()) * 0.4 + safeDouble(item.getConfidence()) * 0.3;
        return score;
    }

    /**
     * 构造来源引用。
     */
    private List<Map<String, Object>> sourceRefs(AgentMemoryItem item) {
        List<Map<String, Object>> refs = new ArrayList<>();
        if (item.getSourceTaskId() != null) {
            refs.add(Map.of("type", "agent_task", "id", item.getSourceTaskId()));
        }
        if (item.getSourceToolCallId() != null) {
            refs.add(Map.of("type", "agent_tool_call", "id", item.getSourceToolCallId()));
        }
        if (item.getSourceReviewId() != null) {
            refs.add(Map.of("type", "agent_review", "id", item.getSourceReviewId()));
        }
        return refs;
    }

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

    private List<Map<String, Object>> readList(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return objectMapper.readValue(json, LIST_MAP_TYPE);
        } catch (Exception e) {
            return List.of();
        }
    }

    private String toJson(Object value, String fallback) {
        try {
            return objectMapper.writeValueAsString(value == null ? List.of() : value);
        } catch (Exception e) {
            return fallback;
        }
    }

    private Object valueOr(Map<String, Object> map, String key, Object fallback) {
        return map != null && map.get(key) != null ? map.get(key) : fallback;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> ensureMap(Object value) {
        return value instanceof Map<?, ?> map ? (Map<String, Object>) map : new LinkedHashMap<>();
    }

    private String normalize(String value, Set<String> allowed, String message) {
        String normalized = requireText(value, message).toUpperCase(Locale.ROOT);
        if (!allowed.contains(normalized)) {
            throw new IllegalArgumentException("AGENT_MEMORY_VALIDATION_FAILED：" + message);
        }
        return normalized;
    }

    private BigDecimal decimalValue(Object value) {
        if (value instanceof BigDecimal decimal) {
            return decimal;
        }
        if (value instanceof Number number) {
            return BigDecimal.valueOf(number.doubleValue());
        }
        try {
            return new BigDecimal(String.valueOf(value));
        } catch (Exception e) {
            return null;
        }
    }

    private BigDecimal clampScore(BigDecimal value, BigDecimal fallback) {
        BigDecimal score = value == null ? fallback : value;
        if (score.compareTo(BigDecimal.ZERO) < 0) {
            score = BigDecimal.ZERO;
        }
        if (score.compareTo(BigDecimal.ONE) > 0) {
            score = BigDecimal.ONE;
        }
        return score.setScale(4, RoundingMode.HALF_UP);
    }

    private double safeDouble(BigDecimal value) {
        return value == null ? 0.0 : value.doubleValue();
    }

    private int intValue(Object value, int fallback) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(value));
        } catch (Exception e) {
            return fallback;
        }
    }

    private String requireText(String value, String message) {
        String text = emptyToNull(value);
        if (text == null) {
            throw new IllegalArgumentException(message);
        }
        return text;
    }

    private String text(Object value) {
        return value == null ? null : emptyToNull(String.valueOf(value));
    }

    private String emptyToNull(String value) {
        if (value == null) {
            return null;
        }
        String text = value.trim();
        return text.isEmpty() ? null : text;
    }

    private String defaultText(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }

    private String sourceHash(String userId, String sourceId, String namespace, String subjectKey, String content) {
        return sha256(String.join("|",
                defaultText(userId, ""),
                defaultText(sourceId, ""),
                defaultText(namespace, ""),
                defaultText(subjectKey, ""),
                defaultText(content, "")
        ));
    }

    private String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            return "hash-unavailable";
        }
    }
}
