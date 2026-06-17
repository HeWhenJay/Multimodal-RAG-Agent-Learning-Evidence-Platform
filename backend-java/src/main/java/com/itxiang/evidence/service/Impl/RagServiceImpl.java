package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.RagQueryVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class RagServiceImpl implements RagService {

    private static final Path UPLOAD_ROOT = Path.of("uploads", "rag");
    private static final DateTimeFormatter DATE_PATH_FORMATTER = DateTimeFormatter.BASIC_ISO_DATE;

    private final LearningMaterialMapper learningMaterialMapper;
    private final PythonRagClient pythonRagClient;
    private final LogService logService;

    /**
     * 汇总 Java 资料记录和 Python 向量仓库概览。
     */
    @Override
    public RagOverviewVO overview(String userId) {
        String scopedUserId = requireUserId(userId);
        Long materialCount = learningMaterialMapper.countAllByUserId(scopedUserId);
        Integer chunkCount = learningMaterialMapper.sumChunkCountByUserId(scopedUserId);
        List<LearningMaterial> recent = learningMaterialMapper.findRecentByUserId(scopedUserId, 1);
        int safeChunkCount = chunkCount == null ? 0 : chunkCount;
        return RagOverviewVO.builder()
                .materialCount(materialCount == null ? 0 : materialCount)
                .chunkCount(safeChunkCount)
                .evidenceCount(safeChunkCount)
                .lastIndexedTitle(recent.isEmpty() ? null : recent.get(0).getTitle())
                .build();
    }

    /**
     * 查询最近学习资料，用于前端资料列表。
     */
    @Override
    public List<LearningMaterialVO> listRecentMaterials(String userId) {
        return learningMaterialMapper.findRecentByUserId(requireUserId(userId), 20).stream()
                .map(this::convertToVO)
                .toList();
    }

    /**
     * 查询单个学习资料记录。
     */
    @Override
    public LearningMaterialVO getMaterial(Long id, String userId) {
        LearningMaterial material = learningMaterialMapper.findByIdAndUserId(id, requireUserId(userId));
        if (material == null) {
            throw new IllegalArgumentException("资料不存在");
        }
        return convertToVO(material);
    }

    /**
     * 查询单个资料已入库的 evidence 片段。
     */
    @Override
    public List<RagEvidenceVO> listMaterialEvidences(Long id, String userId, Integer limit) {
        LearningMaterial material = learningMaterialMapper.findByIdAndUserId(id, requireUserId(userId));
        if (material == null) {
            throw new IllegalArgumentException("资料不存在");
        }
        int safeLimit = limit == null ? 20 : Math.max(1, Math.min(limit, 100));
        try {
            return pythonRagClient.listDocumentEvidences("material-" + id, safeLimit);
        } catch (Exception e) {
            Map<String, Object> context = materialContext(material);
            context.put("limit", safeLimit);
            context.putAll(pythonExceptionContext(e));
            logService.recordRagError(
                    "evidence",
                    "evidence",
                    "material_evidence_query_failed",
                    resolveRagErrorCode(e),
                    "查询学习资料证据失败",
                    e,
                    context
            );
            throw e;
        }
    }

    /**
     * 创建文本资料记录并调用 Python RAG 索引。
     */
    @Override
    @Transactional
    public LearningMaterialVO indexText(RagIndexTextDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        LearningMaterial material = new LearningMaterial();
        material.setTitle(dto.getTitle());
        material.setUserId(scopedUserId);
        material.setDocumentType(blankToDefault(dto.getDocumentType(), "markdown"));
        material.setSource(blankToDefault(dto.getSource(), "manual"));
        material.setStatus("PENDING");
        material.setChunkCount(0);
        learningMaterialMapper.insert(material);
        logService.recordRagEvent(
                "material",
                "index",
                "material_index_text_start",
                "开始索引文本学习资料",
                materialContext(material)
        );

        learningMaterialMapper.updateStatus(material.getId(), "PARSING");
        material.setStatus("PARSING");
        try {
            PythonRagClient.IndexResult result = pythonRagClient.indexText(material.getId(), scopedUserId, dto);
            recordIndexResultAnomalies(material, result);
            applyIndexResult(material, result);
            logService.recordRagEvent(
                    "material",
                    "index",
                    "material_index_text_result",
                    "文本学习资料索引完成",
                    indexResultContext(material, result)
            );
        } catch (Exception e) {
            log.warn("文本资料解析入库失败: materialId={}, reason={}", material.getId(), e.getMessage());
            logService.recordRagError(
                    "material",
                    "index",
                    "material_index_text_failed",
                    resolveRagErrorCode(e),
                    "文本学习资料索引失败",
                    e,
                    errorContext(material, e)
            );
            markFailed(material, e.getMessage());
        }
        return convertToVO(material);
    }

    /**
     * 保存上传文件、创建资料记录并调用 Python RAG 索引。
     */
    @Override
    @Transactional
    public LearningMaterialVO uploadMaterial(MultipartFile file, Boolean highPrecision, String userId) {
        String scopedUserId = requireUserId(userId);
        String filename = file.getOriginalFilename() == null ? "未命名资料" : file.getOriginalFilename();
        Path savedPath;
        try {
            savedPath = saveUploadFile(file, filename);
        } catch (Exception e) {
            logService.recordRagError(
                    "material",
                    "upload",
                    "material_file_save_failed",
                    file.isEmpty() ? "RAG_FILE_EMPTY" : "RAG_FILE_SAVE_FAILED",
                    "学习资料文件保存失败",
                    e,
                    uploadContext(file, filename, highPrecision)
            );
            throw e;
        }
        LearningMaterial material = new LearningMaterial();
        material.setTitle(filename);
        material.setUserId(scopedUserId);
        material.setDocumentType(detectDocumentType(filename));
        material.setSource("upload");
        material.setStatus("PENDING");
        material.setChunkCount(0);
        material.setOriginalFilename(filename);
        material.setOriginalFilePath(savedPath.toString());
        learningMaterialMapper.insert(material);
        Map<String, Object> startContext = materialContext(material);
        startContext.put("highPrecision", Boolean.TRUE.equals(highPrecision));
        logService.recordRagEvent(
                "material",
                "upload",
                "material_upload_saved",
                "学习资料文件已保存并创建记录",
                startContext
        );

        learningMaterialMapper.updateStatus(material.getId(), "PARSING");
        material.setStatus("PARSING");
        try {
            PythonRagClient.IndexResult result = pythonRagClient.indexFile(
                    material.getId(),
                    scopedUserId,
                    material,
                    file,
                    Boolean.TRUE.equals(highPrecision)
            );
            recordIndexResultAnomalies(material, result);
            applyIndexResult(material, result);
            logService.recordRagEvent(
                    "material",
                    "index",
                    "material_index_file_result",
                    "文件学习资料索引完成",
                    indexResultContext(material, result)
            );
        } catch (Exception e) {
            log.warn("文件资料解析入库失败: materialId={}, reason={}", material.getId(), e.getMessage());
            logService.recordRagError(
                    "material",
                    "index",
                    "material_index_file_failed",
                    resolveRagErrorCode(e),
                    "文件学习资料索引失败",
                    e,
                    errorContext(material, e)
            );
            markFailed(material, e.getMessage());
        }
        return convertToVO(material);
    }

    /**
     * 调用 Python RAG 执行检索问答，并记录查询耗时和结果状态。
     */
    @Override
    public RagQueryVO query(RagQueryDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        RagQueryDTO scopedDto = scopedQuery(dto, scopedUserId);
        long start = System.currentTimeMillis();
        logService.recordRagEvent(
                "rag_query",
                "retrieve",
                "rag_query_start",
                "开始 RAG 查询",
                queryContext(scopedDto, null, null)
        );
        try {
            RagQueryVO result = pythonRagClient.query(scopedDto);
            Map<String, Object> context = queryContext(scopedDto, result, System.currentTimeMillis() - start);
            String action = result.getEvidences() == null || result.getEvidences().isEmpty()
                    ? "rag_query_no_evidence"
                    : "rag_query_success";
            logService.recordRagEvent(
                    "rag_query",
                    "retrieve",
                    action,
                    "RAG 查询完成",
                    context
            );
            return result;
        } catch (Exception e) {
            Map<String, Object> context = queryContext(scopedDto, null, System.currentTimeMillis() - start);
            context.putAll(pythonExceptionContext(e));
            logService.recordRagError(
                    "rag_query",
                    "retrieve",
                    "rag_query_failed",
                    resolveRagErrorCode(e),
                    "RAG 查询失败",
                    e,
                    context
            );
            throw e;
        }
    }

    /**
     * 将资料实体转换为前端展示对象。
     */
    private LearningMaterialVO convertToVO(LearningMaterial material) {
        return LearningMaterialVO.builder()
                .id(material.getId())
                .title(material.getTitle())
                .userId(material.getUserId())
                .documentType(material.getDocumentType())
                .source(material.getSource())
                .status(material.getStatus())
                .parser(material.getParser())
                .documentSummary(material.getDocumentSummary())
                .chunkCount(material.getChunkCount() == null ? 0 : material.getChunkCount())
                .originalFilename(material.getOriginalFilename())
                .originalFilePath(material.getOriginalFilePath())
                .createdAt(material.getCreatedAt())
                .updatedAt(material.getUpdatedAt())
                .build();
    }

    /**
     * 根据文件名推断资料类型。
     */
    private String detectDocumentType(String filename) {
        String lower = filename.toLowerCase();
        if (lower.endsWith(".md")) {
            return "markdown";
        }
        if (lower.endsWith(".pdf")) {
            return "pdf";
        }
        if (lower.endsWith(".docx")) {
            return "docx";
        }
        if (lower.endsWith(".pptx")) {
            return "pptx";
        }
        if (lower.endsWith(".ppt")) {
            return "ppt";
        }
        if (lower.endsWith(".doc")) {
            return "doc";
        }
        if (lower.endsWith(".xlsx")) {
            return "xlsx";
        }
        if (lower.endsWith(".xls")) {
            return "xls";
        }
        if (lower.endsWith(".txt")) {
            return "txt";
        }
        if (lower.endsWith(".srt")) {
            return "srt";
        }
        if (lower.endsWith(".vtt")) {
            return "vtt";
        }
        if (lower.endsWith(".png")) {
            return "png";
        }
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) {
            return "jpg";
        }
        if (lower.endsWith(".webp")) {
            return "webp";
        }
        return "text";
    }

    /**
     * 为空文本提供默认值。
     */
    private String blankToDefault(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }

    /**
     * 校验并标准化当前登录用户 ID。
     */
    private String requireUserId(String userId) {
        if (userId == null || userId.isBlank()) {
            throw new IllegalArgumentException("登录状态已失效");
        }
        return userId.trim();
    }

    /**
     * 将查询强制限定在当前登录用户资料范围内。
     */
    private RagQueryDTO scopedQuery(RagQueryDTO dto, String userId) {
        RagQueryDTO scoped = new RagQueryDTO();
        scoped.setQuestion(dto.getQuestion());
        scoped.setTopK(dto.getTopK());
        Map<String, Object> metadataFilter = new LinkedHashMap<>();
        if (dto.getMetadataFilter() != null) {
            metadataFilter.putAll(dto.getMetadataFilter());
        }
        metadataFilter.put("userId", userId);
        metadataFilter.putIfAbsent("visibilityScope", "private");
        scoped.setMetadataFilter(metadataFilter);
        return scoped;
    }

    /**
     * 将 Python 索引结果回写到 Java 资料记录。
     */
    private void applyIndexResult(LearningMaterial material, PythonRagClient.IndexResult result) {
        learningMaterialMapper.updateIndexResult(
                material.getId(),
                result.status(),
                result.parser(),
                result.documentSummary(),
                result.chunkCount()
        );
        material.setStatus(result.status());
        material.setParser(result.parser());
        material.setDocumentSummary(result.documentSummary());
        material.setChunkCount(result.chunkCount());
    }

    /**
     * 校验 Python 索引结果中可能影响状态一致性的异常情况。
     */
    private void recordIndexResultAnomalies(LearningMaterial material, PythonRagClient.IndexResult result) {
        if (result == null) {
            logService.recordRagError(
                    "material",
                    "sync",
                    "material_index_response_invalid",
                    "RAG_RESPONSE_SCHEMA_INVALID",
                    "Python 索引响应为空",
                    null,
                    materialContext(material)
            );
            return;
        }
        String expectedDocumentId = "material-" + material.getId();
        if (result.documentId() != null && !expectedDocumentId.equals(result.documentId())) {
            Map<String, Object> context = indexResultContext(material, result);
            context.put("requestDocumentId", expectedDocumentId);
            context.put("responseDocumentId", result.documentId());
            logService.recordRagError(
                    "material",
                    "sync",
                    "material_document_id_mismatch",
                    "RAG_DOCUMENT_ID_MISMATCH",
                    "Python 文档 ID 与 Java 资料 ID 不一致",
                    null,
                    context
            );
        }
        if ("FAILED".equals(result.status())) {
            logService.recordRagError(
                    "material",
                    "index",
                    "material_index_failed_status",
                    "RAG_INDEX_FAILED",
                    "Python 返回索引失败状态",
                    null,
                    indexResultContext(material, result)
            );
        }
        if ("READY".equals(result.status()) && (result.chunkCount() == null || result.chunkCount() <= 0)) {
            logService.recordRagError(
                    "material",
                    "sync",
                    "material_ready_with_zero_chunk",
                    "RAG_READY_WITH_ZERO_CHUNK",
                    "Python 返回 READY 但切块数为 0",
                    null,
                    indexResultContext(material, result)
            );
        }
    }

    /**
     * 将资料状态标记为索引失败并保留失败原因摘要。
     */
    private void markFailed(LearningMaterial material, String reason) {
        String message = reason == null ? "Python RAG 解析失败" : reason;
        learningMaterialMapper.updateIndexResult(
                material.getId(),
                "FAILED",
                "python-rag-error",
                truncate(message, 500),
                0
        );
        material.setStatus("FAILED");
        material.setParser("python-rag-error");
        material.setDocumentSummary(truncate(message, 500));
        material.setChunkCount(0);
    }

    /**
     * 按日期目录保存上传文件。
     */
    private Path saveUploadFile(MultipartFile file, String filename) {
        if (file.isEmpty()) {
            throw new IllegalArgumentException("上传文件不能为空");
        }
        String datePath = LocalDate.now().format(DATE_PATH_FORMATTER);
        Path directory = UPLOAD_ROOT.resolve(datePath);
        String safeFilename = sanitizeFilename(filename);
        Path target = directory.resolve(UUID.randomUUID() + "-" + safeFilename);
        try {
            Files.createDirectories(directory);
            try (var inputStream = file.getInputStream()) {
                Files.copy(inputStream, target);
            }
            return target;
        } catch (IOException e) {
            throw new IllegalStateException("保存上传文件失败: " + e.getMessage(), e);
        }
    }

    /**
     * 清理文件名中的路径分隔符和空白字符。
     */
    private String sanitizeFilename(String filename) {
        String fallback = filename == null || filename.isBlank() ? "material" : filename;
        return fallback
                .replaceAll("[\\\\/:*?\"<>|]+", "_")
                .replaceAll("\\s+", "_")
                .toLowerCase(Locale.ROOT);
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
     * 构造资料相关日志上下文。
     */
    private Map<String, Object> materialContext(LearningMaterial material) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("materialId", material.getId());
        context.put("documentId", material.getId() == null ? null : "material-" + material.getId());
        context.put("userId", material.getUserId());
        context.put("title", material.getTitle());
        context.put("documentType", material.getDocumentType());
        context.put("source", material.getSource());
        context.put("parseStatus", material.getStatus());
        context.put("parser", material.getParser());
        context.put("chunkCount", material.getChunkCount());
        context.put("filename", material.getOriginalFilename());
        return context;
    }

    /**
     * 构造 Python 索引结果日志上下文。
     */
    private Map<String, Object> indexResultContext(LearningMaterial material, PythonRagClient.IndexResult result) {
        Map<String, Object> context = materialContext(material);
        context.put("documentId", result.documentId());
        context.put("parseStatus", result.status());
        context.put("parser", result.parser());
        context.put("chunkCount", result.chunkCount());
        context.put("documentSummaryLength", result.documentSummary() == null ? 0 : result.documentSummary().length());
        return context;
    }

    /**
     * 构造资料索引异常日志上下文。
     */
    private Map<String, Object> errorContext(LearningMaterial material, Exception e) {
        Map<String, Object> context = materialContext(material);
        context.putAll(pythonExceptionContext(e));
        return context;
    }

    /**
     * 构造文件上传异常日志上下文。
     */
    private Map<String, Object> uploadContext(MultipartFile file, String filename, Boolean highPrecision) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("filename", filename);
        context.put("fileSize", file == null ? 0 : file.getSize());
        context.put("documentType", detectDocumentType(filename));
        context.put("highPrecision", Boolean.TRUE.equals(highPrecision));
        return context;
    }

    /**
     * 构造 RAG 查询日志上下文，避免记录问题全文。
     */
    private Map<String, Object> queryContext(RagQueryDTO dto, RagQueryVO result, Long elapsedMs) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("questionLength", dto.getQuestion() == null ? 0 : dto.getQuestion().length());
        context.put("topK", dto.getTopK() == null ? 5 : dto.getTopK());
        context.put("hasMetadataFilter", dto.getMetadataFilter() != null && !dto.getMetadataFilter().isEmpty());
        context.put("metadataFilterKeys", dto.getMetadataFilter() == null
                ? List.of()
                : dto.getMetadataFilter().keySet().stream().toList());
        if (result != null) {
            context.put("expandedQueryCount", result.getExpandedQueries() == null ? 0 : result.getExpandedQueries().size());
            context.put("evidenceCount", result.getEvidences() == null ? 0 : result.getEvidences().size());
        }
        if (elapsedMs != null) {
            context.put("elapsedMs", elapsedMs);
        }
        return context;
    }

    /**
     * 提取 Python 调用异常中的接口、状态码和响应摘要。
     */
    private Map<String, Object> pythonExceptionContext(Throwable e) {
        Map<String, Object> context = new LinkedHashMap<>();
        if (e instanceof PythonRagClient.PythonRagClientException pythonException) {
            context.put("pythonOperation", pythonException.getOperation());
            context.put("pythonEndpoint", pythonException.getEndpoint());
            context.put("statusCode", pythonException.getStatusCode());
            context.put("responseBody", truncate(pythonException.getResponseBody(), 500));
        }
        return context;
    }

    /**
     * 将 Python 调用异常映射为 RAG 错误码。
     */
    private String resolveRagErrorCode(Throwable e) {
        if (e instanceof PythonRagClient.PythonRagClientException pythonException) {
            if ("read-index-result".equals(pythonException.getOperation())) {
                return "RAG_RESPONSE_SCHEMA_INVALID";
            }
            Integer statusCode = pythonException.getStatusCode();
            if (statusCode == null) {
                String message = e.getMessage() == null ? "" : e.getMessage().toLowerCase(Locale.ROOT);
                return message.contains("timed out") || message.contains("timeout")
                        ? "RAG_PYTHON_TIMEOUT"
                        : "RAG_PYTHON_UNAVAILABLE";
            }
            if (statusCode >= 500) {
                return "RAG_PYTHON_5XX";
            }
            if (statusCode >= 400) {
                return "RAG_PYTHON_4XX";
            }
            return "RAG_PYTHON_BAD_RESPONSE";
        }
        return "RAG_STATUS_SYNC_FAILED";
    }
}
