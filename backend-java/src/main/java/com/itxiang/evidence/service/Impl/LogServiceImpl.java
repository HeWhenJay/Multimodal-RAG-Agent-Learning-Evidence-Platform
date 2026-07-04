package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.common.RagOperationContext;
import com.itxiang.evidence.config.LogProperties;
import com.itxiang.evidence.dto.LogErrorCreateDTO;
import com.itxiang.evidence.dto.LogEventCreateDTO;
import com.itxiang.evidence.entity.LogError;
import com.itxiang.evidence.entity.LogEvent;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.LogErrorMapper;
import com.itxiang.evidence.mapper.LogEventMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.vo.LogErrorVO;
import com.itxiang.evidence.vo.LogEventVO;
import com.itxiang.evidence.vo.LogOverviewVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;

@Slf4j
@Service
@RequiredArgsConstructor
public class LogServiceImpl implements LogService {

    private static final String DEFAULT_USER_ID = "anonymous";
    private static final Pattern UUID_PATTERN = Pattern.compile(
            "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    );
    private static final Pattern LARGE_NUMBER_PATTERN = Pattern.compile("\\b\\d{4,}\\b");

    private final LogEventMapper logEventMapper;
    private final LogErrorMapper logErrorMapper;
    private final LearningMaterialMapper learningMaterialMapper;
    private final ObjectMapper objectMapper;
    private final LogProperties logProperties;

    /**
     * 写入单条业务事件日志，并补齐默认追踪信息。
     */
    @Override
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Long recordEvent(LogEventCreateDTO dto) {
        if (Boolean.FALSE.equals(logProperties.getEnabled())) {
            return null;
        }
        if (dto.getContext() != null) {
            enrichRagIds(dto, dto.getContext());
        }
        LogEvent event = new LogEvent();
        event.setTraceId(defaultText(dto.getTraceId(), newTraceId()));
        event.setSessionId(dto.getSessionId());
        event.setUserId(defaultText(dto.getUserId(), DEFAULT_USER_ID));
        event.setSource(defaultText(dto.getSource(), "java"));
        event.setDomain(defaultText(dto.getDomain(), "system"));
        event.setLevel(defaultText(dto.getLevel(), "INFO").toUpperCase(Locale.ROOT));
        event.setModule(truncate(dto.getModule(), 80));
        event.setStage(truncate(dto.getStage(), 80));
        event.setEventType(defaultText(dto.getEventType(), "business_state"));
        event.setAction(truncate(dto.getAction(), 120));
        event.setMessage(truncate(dto.getMessage(), 500));
        event.setRoute(truncate(dto.getRoute(), 255));
        event.setHttpMethod(truncate(dto.getHttpMethod(), 20));
        event.setRequestPath(truncate(dto.getRequestPath(), 500));
        event.setStatusCode(dto.getStatusCode());
        event.setSuccess(dto.getSuccess() == null || dto.getSuccess());
        event.setDurationMs(dto.getDurationMs());
        event.setMaterialId(dto.getMaterialId());
        event.setDocumentId(truncate(dto.getDocumentId(), 120));
        event.setParser(truncate(dto.getParser(), 80));
        event.setClientTime(toOffsetDateTime(dto.getClientTime()));
        event.setServerTime(OffsetDateTime.now());
        event.setContextJson(toContextJson(dto.getContext(), logProperties.getMaxContextBytes()));
        logEventMapper.insert(event);
        syncMaterialStatusFromRagProgress(event, dto.getContext());
        return event.getId();
    }

    /**
     * 按配置上限批量写入业务事件日志。
     */
    @Override
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Integer recordEvents(List<LogEventCreateDTO> dtoList) {
        if (dtoList == null || dtoList.isEmpty()) {
            return 0;
        }
        int maxBatchSize = safePositive(logProperties.getMaxBatchSize(), 50);
        int count = 0;
        for (LogEventCreateDTO dto : dtoList.stream().limit(maxBatchSize).toList()) {
            recordEvent(dto);
            count++;
        }
        return count;
    }

    /**
     * 写入错误日志；同类指纹已存在时只累加出现次数。
     */
    @Override
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Long recordError(LogErrorCreateDTO dto) {
        if (Boolean.FALSE.equals(logProperties.getEnabled())) {
            return null;
        }
        String stackTrace = truncate(defaultText(dto.getStackTrace(), ""), safePositive(logProperties.getMaxStackTraceBytes(), 20480));
        String fingerprint = defaultText(dto.getFingerprint(), fingerprint(dto, stackTrace));
        LogError existing = logErrorMapper.findByFingerprint(fingerprint);
        if (existing != null) {
            logErrorMapper.increaseOccurrence(fingerprint, LocalDateTime.now());
            return existing.getId();
        }

        LogError error = new LogError();
        error.setTraceId(defaultText(dto.getTraceId(), newTraceId()));
        error.setSessionId(dto.getSessionId());
        error.setUserId(defaultText(dto.getUserId(), DEFAULT_USER_ID));
        error.setSource(defaultText(dto.getSource(), "java"));
        error.setDomain(defaultText(dto.getDomain(), "system"));
        error.setSeverity(defaultText(dto.getSeverity(), "ERROR").toUpperCase(Locale.ROOT));
        error.setModule(truncate(dto.getModule(), 80));
        error.setStage(truncate(dto.getStage(), 80));
        error.setAction(truncate(dto.getAction(), 120));
        error.setErrorType(truncate(dto.getErrorType(), 120));
        error.setErrorCode(truncate(dto.getErrorCode(), 120));
        error.setMessage(truncate(dto.getMessage(), 1000));
        error.setStackTrace(stackTrace);
        error.setFingerprint(fingerprint);
        error.setRoute(truncate(dto.getRoute(), 255));
        error.setHttpMethod(truncate(dto.getHttpMethod(), 20));
        error.setRequestPath(truncate(dto.getRequestPath(), 500));
        error.setStatusCode(dto.getStatusCode());
        error.setDurationMs(dto.getDurationMs());
        error.setMaterialId(dto.getMaterialId());
        error.setDocumentId(truncate(dto.getDocumentId(), 120));
        error.setParser(truncate(dto.getParser(), 80));
        error.setClientTime(toOffsetDateTime(dto.getClientTime()));
        error.setServerTime(OffsetDateTime.now());
        error.setContextJson(toContextJson(dto.getContext(), logProperties.getMaxContextBytes()));
        error.setStatus("OPEN");
        logErrorMapper.insert(error);
        return error.getId();
    }

    /**
     * 记录 RAG 业务状态事件，失败时不影响主业务流程。
     */
    @Override
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void recordRagEvent(String module, String stage, String action, String message, Map<String, Object> context) {
        try {
            LogEventCreateDTO dto = new LogEventCreateDTO();
            dto.setSource("java");
            dto.setDomain("rag");
            dto.setModule(module);
            dto.setStage(stage);
            dto.setAction(action);
            dto.setMessage(message);
            dto.setContext(context == null ? new LinkedHashMap<>() : context);
            enrichRagIds(dto, dto.getContext());
            recordEvent(dto);
        } catch (Exception e) {
            log.warn("记录 RAG 事件失败: {}", e.getMessage());
        }
    }

    /**
     * 记录用户可见的 RAG 进度事件，供资料页轮询展示。
     */
    @Override
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void recordRagProgress(String module,
                                  String stage,
                                  String action,
                                  String message,
                                  Map<String, Object> context,
                                  Boolean success) {
        try {
            LogEventCreateDTO dto = new LogEventCreateDTO();
            dto.setSource("java");
            dto.setDomain("rag");
            dto.setModule(module);
            dto.setStage(stage);
            dto.setEventType("rag_progress");
            dto.setAction(action);
            dto.setMessage(message);
            dto.setSuccess(success == null || success);
            dto.setContext(context == null ? new LinkedHashMap<>() : context);
            enrichRagIds(dto, dto.getContext());
            recordEvent(dto);
        } catch (Exception e) {
            log.warn("记录 RAG 进度失败: {}", e.getMessage());
        }
    }

    /**
     * 记录 RAG 错误日志并标记异常，避免全局异常处理重复写入。
     */
    @Override
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void recordRagError(String module,
                               String stage,
                               String action,
                               String errorCode,
                               String message,
                               Throwable throwable,
                               Map<String, Object> context) {
        try {
            Map<String, Object> safeContext = context == null ? new LinkedHashMap<>() : new LinkedHashMap<>(context);
            safeContext.put("ragStage", stage);
            if (action != null) {
                safeContext.put("action", action);
            }
            LogErrorCreateDTO dto = new LogErrorCreateDTO();
            dto.setSource("java");
            dto.setDomain("rag");
            dto.setSeverity("ERROR");
            dto.setModule(module);
            dto.setStage(stage);
            dto.setAction(action);
            dto.setErrorType(throwable == null ? "RagBusinessError" : throwable.getClass().getSimpleName());
            dto.setErrorCode(errorCode);
            dto.setMessage(defaultText(message, throwable == null ? "RAG 业务错误" : throwable.getMessage()));
            dto.setStackTrace(throwable == null ? null : stackTrace(throwable));
            dto.setContext(safeContext);
            enrichRagIds(dto, safeContext);
            recordError(dto);
            RagOperationContext.markErrorLogged(throwable);
        } catch (Exception e) {
            log.warn("记录 RAG 错误失败: {}", e.getMessage());
        }
    }

    /**
     * 查询最近业务事件日志。
     */
    @Override
    public List<LogEventVO> listRecentEvents(Integer limit) {
        int safeLimit = safeLimit(limit);
        return logEventMapper.findRecent(safeLimit).stream()
                .map(this::toEventVO)
                .toList();
    }

    /**
     * 查询最近错误日志。
     */
    @Override
    public List<LogErrorVO> listRecentErrors(Integer limit) {
        int safeLimit = safeLimit(limit);
        return logErrorMapper.findRecent(safeLimit).stream()
                .map(this::toErrorVO)
                .toList();
    }

    /**
     * 统计日志概览并按来源拆分错误数量。
     */
    @Override
    public LogOverviewVO overview(Integer days) {
        int safeDays = days == null ? 7 : Math.max(1, Math.min(days, 90));
        LocalDateTime startTime = LocalDateTime.now().minusDays(safeDays);
        return LogOverviewVO.builder()
                .eventCount(defaultLong(logEventMapper.countSince(startTime)))
                .errorCount(defaultLong(logErrorMapper.countSince(startTime)))
                .openErrorCount(defaultLong(logErrorMapper.countOpenSince(startTime)))
                .frontendErrorCount(defaultLong(logErrorMapper.countBySourceSince("frontend", startTime)))
                .javaErrorCount(defaultLong(logErrorMapper.countBySourceSince("java", startTime)))
                .pythonErrorCount(defaultLong(logErrorMapper.countBySourceSince("python", startTime)))
                .build();
    }

    /**
     * 从 RAG 上下文中提取资料 ID、文档 ID 和解析器信息。
     */
    private void enrichRagIds(LogEventCreateDTO dto, Map<String, Object> context) {
        Object materialId = context.get("materialId");
        if (materialId instanceof Number number) {
            dto.setMaterialId(number.longValue());
        }
        Object documentId = context.get("documentId");
        if (documentId != null) {
            dto.setDocumentId(String.valueOf(documentId));
        }
        Object parser = context.get("parser");
        if (parser != null) {
            dto.setParser(String.valueOf(parser));
        }
    }

    /**
     * 从 RAG 错误上下文中提取资料 ID、文档 ID 和解析器信息。
     */
    private void enrichRagIds(LogErrorCreateDTO dto, Map<String, Object> context) {
        Object materialId = context.get("materialId");
        if (materialId instanceof Number number) {
            dto.setMaterialId(number.longValue());
        }
        Object documentId = context.get("documentId");
        if (documentId != null) {
            dto.setDocumentId(String.valueOf(documentId));
        }
        Object parser = context.get("parser");
        if (parser != null) {
            dto.setParser(String.valueOf(parser));
        }
    }

    /**
     * 根据 Python RAG 进度回调同步资料主状态，避免长任务 HTTP 等待超时后状态停留在失败。
     */
    private void syncMaterialStatusFromRagProgress(LogEvent event, Map<String, Object> context) {
        if (!isRagProgressEvent(event) || event.getMaterialId() == null) {
            return;
        }
        String stage = defaultText(event.getStage(), text(context, "stageCode"));
        String progressStatus = normalizeStatus(text(context, "status"));
        if ("index.completed".equals(stage) && context.get("stagingDocumentId") != null && context.get("promoteConfirmed") == null) {
            learningMaterialMapper.updateProgressStatus(
                    event.getMaterialId(),
                    "PARSING",
                    defaultText(event.getParser(), text(context, "parser")),
                    null
            );
            return;
        }
        if ("index.completed".equals(stage)) {
            String parseStatus = normalizeStatus(text(context, "parseStatus"));
            String finalStatus = isFinalSuccessStatus(parseStatus) ? parseStatus : "READY";
            learningMaterialMapper.updateProgressStatus(
                    event.getMaterialId(),
                    finalStatus,
                    defaultText(event.getParser(), text(context, "parser")),
                    completedChunkCount(context)
            );
            return;
        }
        if ("index.failed".equals(stage) || "FAILED".equals(progressStatus)) {
            learningMaterialMapper.updateProgressStatus(
                    event.getMaterialId(),
                    "FAILED",
                    defaultText(event.getParser(), text(context, "parser")),
                    integer(context, "chunkCount")
            );
            return;
        }
        if ("RUNNING".equals(progressStatus) || hasUnfinishedProgress(context)) {
            learningMaterialMapper.updateProgressStatus(
                    event.getMaterialId(),
                    "PARSING",
                    defaultText(event.getParser(), text(context, "parser")),
                    null
            );
        }
    }

    /**
     * 判断是否为用户可见的 RAG 进度事件。
     */
    private boolean isRagProgressEvent(LogEvent event) {
        return "rag".equals(event.getDomain()) && "rag_progress".equals(event.getEventType());
    }

    /**
     * 判断进度上下文是否明确显示仍未完成。
     */
    private boolean hasUnfinishedProgress(Map<String, Object> context) {
        Integer currentChunk = integer(context, "currentChunk");
        Integer totalChunks = integer(context, "totalChunks");
        if (currentChunk != null && totalChunks != null && currentChunk < totalChunks) {
            return true;
        }
        Integer percent = integer(context, "percent");
        return percent != null && percent > 0 && percent < 100;
    }

    /**
     * 判断 Python 索引成功终态是否可直接回写资料。
     */
    private boolean isFinalSuccessStatus(String status) {
        return "READY".equals(status) || "PARTIAL".equals(status);
    }

    /**
     * 读取索引完成后的切块数，兼容旧版 Python 终态回调只带 totalChunks 的情况。
     */
    private Integer completedChunkCount(Map<String, Object> context) {
        Integer chunkCount = integer(context, "chunkCount");
        if (chunkCount != null) {
            return chunkCount;
        }
        Integer totalChunks = integer(context, "totalChunks");
        if (totalChunks != null) {
            return totalChunks;
        }
        return integer(context, "currentChunk");
    }

    /**
     * 序列化上下文前执行脱敏和长度截断。
     */
    private String toContextJson(Map<String, Object> context, Integer maxBytes) {
        try {
            Map<String, Object> safe = sanitizeMap(context == null ? new LinkedHashMap<>() : context, 0);
            return truncate(objectMapper.writeValueAsString(safe), safePositive(maxBytes, 20480));
        } catch (JsonProcessingException e) {
            return "{\"serializationError\":true}";
        }
    }

    /**
     * 递归清理 Map，限制嵌套深度。
     */
    private Map<String, Object> sanitizeMap(Map<?, ?> source, int depth) {
        Map<String, Object> result = new LinkedHashMap<>();
        if (depth > 4) {
            result.put("truncatedDepth", true);
            return result;
        }
        source.forEach((key, value) -> {
            String safeKey = String.valueOf(key);
            result.put(safeKey, sanitizeValue(safeKey, value, depth + 1));
        });
        return result;
    }

    /**
     * 根据字段名脱敏敏感值，并限制列表和文本长度。
     */
    private Object sanitizeValue(String key, Object value, int depth) {
        if (value == null) {
            return null;
        }
        if (isSensitiveKey(key)) {
            return "***";
        }
        if (value instanceof Map<?, ?> map) {
            return sanitizeMap(map, depth);
        }
        if (value instanceof List<?> list) {
            List<Object> sanitized = new ArrayList<>();
            for (Object item : list.stream().limit(50).toList()) {
                sanitized.add(sanitizeValue(key, item, depth + 1));
            }
            return sanitized;
        }
        if (value instanceof CharSequence text) {
            return truncate(String.valueOf(text), 500);
        }
        return value;
    }

    /**
     * 判断上下文字段是否包含敏感信息。
     */
    private boolean isSensitiveKey(String key) {
        String lower = key.toLowerCase(Locale.ROOT);
        return lower.contains("password")
                || lower.contains("token")
                || lower.contains("authorization")
                || lower.contains("cookie")
                || lower.contains("secret")
                || lower.contains("apikey")
                || lower.contains("api_key")
                || lower.contains("dashscope")
                || lower.equals("content")
                || lower.equals("question")
                || lower.equals("answer")
                || lower.equals("resume")
                || lower.equals("jd");
    }

    /**
     * 根据错误来源、模块、类型、消息和堆栈生成聚合指纹。
     */
    private String fingerprint(LogErrorCreateDTO dto, String stackTrace) {
        String raw = String.join("|",
                defaultText(dto.getSource(), "java"),
                defaultText(dto.getDomain(), "system"),
                defaultText(dto.getModule(), "unknown"),
                defaultText(dto.getErrorType(), "UnknownError"),
                defaultText(dto.getErrorCode(), "UNKNOWN"),
                normalize(defaultText(dto.getMessage(), "")),
                normalize(topStackFrame(stackTrace))
        );
        return sha256(raw);
    }

    /**
     * 提取第一条业务相关堆栈用于错误聚合。
     */
    private String topStackFrame(String stackTrace) {
        if (stackTrace == null || stackTrace.isBlank()) {
            return "";
        }
        String[] lines = stackTrace.split("\\R");
        for (String line : lines) {
            String trimmed = line.trim();
            if (trimmed.startsWith("at com.itxiang")) {
                return trimmed;
            }
        }
        for (String line : lines) {
            String trimmed = line.trim();
            if (trimmed.startsWith("at ")) {
                return trimmed;
            }
        }
        return lines[0].trim();
    }

    /**
     * 归一化易变的 UUID 和数字，避免同类错误被拆散。
     */
    private String normalize(String value) {
        String noUuid = UUID_PATTERN.matcher(value).replaceAll("{uuid}");
        return LARGE_NUMBER_PATTERN.matcher(noUuid).replaceAll("{num}");
    }

    /**
     * 计算 SHA-256 十六进制摘要。
     */
    private String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder builder = new StringBuilder();
            for (byte item : hash) {
                builder.append(String.format("%02x", item));
            }
            return builder.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }

    /**
     * 将异常堆栈转换为字符串。
     */
    private String stackTrace(Throwable throwable) {
        StringWriter writer = new StringWriter();
        throwable.printStackTrace(new PrintWriter(writer));
        return writer.toString();
    }

    /**
     * 将事件实体转换为前端展示对象。
     */
    private LogEventVO toEventVO(LogEvent event) {
        return LogEventVO.builder()
                .id(event.getId())
                .traceId(event.getTraceId())
                .source(event.getSource())
                .domain(event.getDomain())
                .level(event.getLevel())
                .module(event.getModule())
                .stage(event.getStage())
                .eventType(event.getEventType())
                .action(event.getAction())
                .message(event.getMessage())
                .success(event.getSuccess())
                .durationMs(event.getDurationMs())
                .materialId(event.getMaterialId())
                .documentId(event.getDocumentId())
                .parser(event.getParser())
                .contextJson(event.getContextJson())
                .createdAt(toLocalDateTime(event.getCreatedAt()))
                .build();
    }

    /**
     * 将错误实体转换为前端展示对象。
     */
    private LogErrorVO toErrorVO(LogError error) {
        return LogErrorVO.builder()
                .id(error.getId())
                .traceId(error.getTraceId())
                .source(error.getSource())
                .domain(error.getDomain())
                .severity(error.getSeverity())
                .module(error.getModule())
                .stage(error.getStage())
                .action(error.getAction())
                .errorType(error.getErrorType())
                .errorCode(error.getErrorCode())
                .message(error.getMessage())
                .fingerprint(error.getFingerprint())
                .statusCode(error.getStatusCode())
                .durationMs(error.getDurationMs())
                .materialId(error.getMaterialId())
                .documentId(error.getDocumentId())
                .parser(error.getParser())
                .contextJson(error.getContextJson())
                .occurrenceCount(error.getOccurrenceCount())
                .status(error.getStatus())
                .firstSeenAt(toLocalDateTime(error.getFirstSeenAt()))
                .lastSeenAt(toLocalDateTime(error.getLastSeenAt()))
                .createdAt(toLocalDateTime(error.getCreatedAt()))
                .build();
    }

    /**
     * 将请求中的本地时间转换为带时区时间，兼容 PostgreSQL TIMESTAMPTZ。
     */
    private OffsetDateTime toOffsetDateTime(LocalDateTime value) {
        return value == null ? null : value.atZone(ZoneId.systemDefault()).toOffsetDateTime();
    }

    /**
     * 将数据库带时区时间转换为前端沿用的本地时间。
     */
    private LocalDateTime toLocalDateTime(OffsetDateTime value) {
        return value == null ? null : value.toLocalDateTime();
    }

    /**
     * 生成本地追踪 ID。
     */
    private String newTraceId() {
        return "tr_" + UUID.randomUUID().toString().replace("-", "");
    }

    /**
     * 为空文本提供默认值。
     */
    private String defaultText(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }

    /**
     * 读取正整数配置，非法时回退默认值。
     */
    private int safePositive(Integer value, int defaultValue) {
        return value == null || value <= 0 ? defaultValue : value;
    }

    /**
     * 限制查询日志条数范围。
     */
    private int safeLimit(Integer limit) {
        return limit == null ? 50 : Math.max(1, Math.min(limit, 200));
    }

    /**
     * 为统计空值提供 0 默认值。
     */
    private Long defaultLong(Long value) {
        return value == null ? 0L : value;
    }

    /**
     * 从日志上下文中读取文本字段。
     */
    private String text(Map<String, Object> context, String key) {
        if (context == null || !context.containsKey(key) || context.get(key) == null) {
            return null;
        }
        return String.valueOf(context.get(key));
    }

    /**
     * 从日志上下文中读取整数字段。
     */
    private Integer integer(Map<String, Object> context, String key) {
        if (context == null || !context.containsKey(key) || context.get(key) == null) {
            return null;
        }
        Object value = context.get(key);
        if (value instanceof Number number) {
            return number.intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(value));
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /**
     * 统一状态大小写，兼容 Python 和 Java 侧不同来源。
     */
    private String normalizeStatus(String status) {
        return status == null ? "" : status.trim().toUpperCase(Locale.ROOT);
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
}
