package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.LogProperties;
import com.itxiang.evidence.dto.LogErrorCreateDTO;
import com.itxiang.evidence.dto.LogEventCreateDTO;
import com.itxiang.evidence.entity.LogError;
import com.itxiang.evidence.entity.LogEvent;
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

    private static final String DEFAULT_USER_ID = "demo-user";
    private static final Pattern UUID_PATTERN = Pattern.compile(
            "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    );
    private static final Pattern LARGE_NUMBER_PATTERN = Pattern.compile("\\b\\d{4,}\\b");

    private final LogEventMapper logEventMapper;
    private final LogErrorMapper logErrorMapper;
    private final ObjectMapper objectMapper;
    private final LogProperties logProperties;

    @Override
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Long recordEvent(LogEventCreateDTO dto) {
        if (Boolean.FALSE.equals(logProperties.getEnabled())) {
            return null;
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
        event.setClientTime(dto.getClientTime());
        event.setServerTime(LocalDateTime.now());
        event.setContextJson(toContextJson(dto.getContext(), logProperties.getMaxContextBytes()));
        logEventMapper.insert(event);
        return event.getId();
    }

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
        error.setClientTime(dto.getClientTime());
        error.setServerTime(LocalDateTime.now());
        error.setContextJson(toContextJson(dto.getContext(), logProperties.getMaxContextBytes()));
        error.setStatus("OPEN");
        logErrorMapper.insert(error);
        return error.getId();
    }

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
            log.warn("record rag event failed: {}", e.getMessage());
        }
    }

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
            dto.setMessage(defaultText(message, throwable == null ? "RAG business error" : throwable.getMessage()));
            dto.setStackTrace(throwable == null ? null : stackTrace(throwable));
            dto.setContext(safeContext);
            enrichRagIds(dto, safeContext);
            recordError(dto);
        } catch (Exception e) {
            log.warn("record rag error failed: {}", e.getMessage());
        }
    }

    @Override
    public List<LogEventVO> listRecentEvents(Integer limit) {
        int safeLimit = safeLimit(limit);
        return logEventMapper.findRecent(safeLimit).stream()
                .map(this::toEventVO)
                .toList();
    }

    @Override
    public List<LogErrorVO> listRecentErrors(Integer limit) {
        int safeLimit = safeLimit(limit);
        return logErrorMapper.findRecent(safeLimit).stream()
                .map(this::toErrorVO)
                .toList();
    }

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

    private String toContextJson(Map<String, Object> context, Integer maxBytes) {
        try {
            Map<String, Object> safe = sanitizeMap(context == null ? new LinkedHashMap<>() : context, 0);
            return truncate(objectMapper.writeValueAsString(safe), safePositive(maxBytes, 20480));
        } catch (JsonProcessingException e) {
            return "{\"serializationError\":true}";
        }
    }

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

    private String normalize(String value) {
        String noUuid = UUID_PATTERN.matcher(value).replaceAll("{uuid}");
        return LARGE_NUMBER_PATTERN.matcher(noUuid).replaceAll("{num}");
    }

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
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }

    private String stackTrace(Throwable throwable) {
        StringWriter writer = new StringWriter();
        throwable.printStackTrace(new PrintWriter(writer));
        return writer.toString();
    }

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
                .createdAt(event.getCreatedAt())
                .build();
    }

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
                .firstSeenAt(error.getFirstSeenAt())
                .lastSeenAt(error.getLastSeenAt())
                .createdAt(error.getCreatedAt())
                .build();
    }

    private String newTraceId() {
        return "tr_" + UUID.randomUUID().toString().replace("-", "");
    }

    private String defaultText(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }

    private int safePositive(Integer value, int defaultValue) {
        return value == null || value <= 0 ? defaultValue : value;
    }

    private int safeLimit(Integer limit) {
        return limit == null ? 50 : Math.max(1, Math.min(limit, 200));
    }

    private Long defaultLong(Long value) {
        return value == null ? 0L : value;
    }

    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
