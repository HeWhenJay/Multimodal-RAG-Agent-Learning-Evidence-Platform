package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.LogEvent;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.LogEventMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.RagProgressVO;
import com.itxiang.evidence.vo.RagQueryVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Stream;

@Slf4j
@Service
@RequiredArgsConstructor
public class RagServiceImpl implements RagService {

    private final LearningMaterialMapper learningMaterialMapper;
    private final LogEventMapper logEventMapper;
    private final PythonRagClient pythonRagClient;
    private final LogService logService;
    private final ObjectStorageService objectStorageService;
    private final RagIndexWorker ragIndexWorker;
    private final ObjectMapper objectMapper;

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
     * 按日期范围和条数查询学习资料，用于工作台近期处理任务。
     */
    @Override
    public List<LearningMaterialVO> listRecentMaterials(String userId, LocalDate startDate, LocalDate endDate, Integer recentDays, Integer limit) {
        String scopedUserId = requireUserId(userId);
        int safeDays = recentDays == null ? 7 : Math.max(1, Math.min(recentDays, 7));
        int safeLimit = limit == null ? 5 : Math.max(1, Math.min(limit, 50));
        LocalDate today = LocalDate.now();
        LocalDate earliestDate = today.minusDays(6);
        LocalDate safeEndDate = endDate == null ? today : clampDate(endDate, earliestDate, today);
        LocalDate safeStartDate = startDate == null ? safeEndDate.minusDays(safeDays - 1L) : startDate;
        if (safeStartDate.isBefore(earliestDate)) {
            safeStartDate = earliestDate;
        }
        if (safeStartDate.isAfter(safeEndDate)) {
            safeStartDate = safeEndDate;
        }
        LocalDateTime startTime = safeStartDate.atStartOfDay();
        LocalDateTime endTime = safeEndDate.plusDays(1).atStartOfDay();
        return learningMaterialMapper.findRecentByUserIdBetween(scopedUserId, startTime, endTime, safeLimit).stream()
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
        material.setStorageType("manual");
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
        String documentType = detectDocumentType(filename);
        ObjectStorageService.StoredObject storedObject;
        try {
            storedObject = objectStorageService.store(file, filename, scopedUserId, documentType);
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
        material.setDocumentType(documentType);
        material.setSource("upload");
        material.setStatus("PENDING");
        material.setChunkCount(0);
        material.setOriginalFilename(filename);
        material.setOriginalFilePath(storedObject.sourcePath());
        material.setStorageType(storedObject.storageType());
        material.setObjectKey(storedObject.objectKey());
        material.setPublicUrl(storedObject.publicUrl());
        learningMaterialMapper.insert(material);
        Map<String, Object> startContext = materialContext(material);
        startContext.put("highPrecision", Boolean.TRUE.equals(highPrecision));
        startContext.put("storageType", storedObject.storageType());
        startContext.put("objectKey", storedObject.objectKey());
        logService.recordRagEvent(
                "material",
                "upload",
                "material_upload_stored",
                "学习资料文件已保存到对象存储并创建记录",
                startContext
        );

        learningMaterialMapper.updateStatus(material.getId(), "PARSING");
        material.setStatus("PARSING");
        scheduleAfterCommit(() -> ragIndexWorker.indexStoredMaterial(
                material.getId(),
                scopedUserId,
                Boolean.TRUE.equals(highPrecision)
        ));
        return convertToVO(material);
    }

    /**
     * 接收学习资料分片，全部到齐后合并文件并触发索引。
     */
    @Override
    @Transactional
    public MaterialUploadChunkVO uploadMaterialChunk(MultipartFile file,
                                                    String uploadId,
                                                    String filename,
                                                    Integer chunkIndex,
                                                    Integer totalChunks,
                                                    Long totalSize,
                                                    Boolean highPrecision,
                                                    String userId) {
        String scopedUserId = requireUserId(userId);
        validateChunkRequest(file, filename, chunkIndex, totalChunks, totalSize);
        String safeUploadId = blankToDefault(sanitizeUploadToken(uploadId), UUID.randomUUID().toString().replace("-", ""));
        Path directory = chunkDirectory(scopedUserId, safeUploadId);
        try {
            Files.createDirectories(directory);
            Path chunkPath = directory.resolve(String.format("chunk-%05d.part", chunkIndex));
            try (InputStream inputStream = file.getInputStream()) {
                Files.copy(inputStream, chunkPath, StandardCopyOption.REPLACE_EXISTING);
            }
            int receivedChunks = countReceivedChunks(directory);
            if (receivedChunks < totalChunks) {
                return MaterialUploadChunkVO.builder()
                        .uploadId(safeUploadId)
                        .filename(filename)
                        .chunkIndex(chunkIndex)
                        .totalChunks(totalChunks)
                        .receivedChunks(receivedChunks)
                        .completed(false)
                        .material(null)
                        .build();
            }
            Path mergedPath = mergeChunks(directory, filename, totalChunks, totalSize);
            LearningMaterialVO material = storeAndIndexMergedFile(
                    mergedPath,
                    filename,
                    file.getContentType(),
                    scopedUserId,
                    highPrecision
            );
            cleanupChunkDirectory(directory);
            return MaterialUploadChunkVO.builder()
                    .uploadId(safeUploadId)
                    .filename(filename)
                    .chunkIndex(chunkIndex)
                    .totalChunks(totalChunks)
                    .receivedChunks(totalChunks)
                    .completed(true)
                    .material(material)
                    .build();
        } catch (IOException e) {
            throw new IllegalStateException("保存上传分片失败: " + e.getMessage(), e);
        }
    }

    /**
     * 重新读取原始文件并调用 Python RAG 重建索引，可用于低质量资料高精度补跑。
     */
    @Override
    @Transactional
    public LearningMaterialVO reindexMaterial(Long id, Boolean highPrecision, String userId) {
        String scopedUserId = requireUserId(userId);
        LearningMaterial material = learningMaterialMapper.findByIdAndUserId(id, scopedUserId);
        if (material == null) {
            throw new IllegalArgumentException("资料不存在");
        }
        if ("manual".equals(material.getStorageType())) {
            throw new IllegalArgumentException("手动文本资料没有原始上传文件，请重新提交文本内容");
        }
        learningMaterialMapper.updateStatus(material.getId(), "REINDEXING");
        material.setStatus("REINDEXING");
        Map<String, Object> startContext = materialContext(material);
        startContext.put("highPrecision", Boolean.TRUE.equals(highPrecision));
        startContext.put("storageType", material.getStorageType());
        startContext.put("objectKey", material.getObjectKey());
        logService.recordRagEvent(
                "material",
                "reindex",
                "material_reindex_start",
                "开始重建学习资料索引",
                startContext
        );

        scheduleAfterCommit(() -> ragIndexWorker.reindexStoredMaterial(
                material.getId(),
                scopedUserId,
                Boolean.TRUE.equals(highPrecision)
        ));
        return convertToVO(material);
    }

    /**
     * 对已保存的上传资料选择合适的 Python 索引入口。
     */
    private PythonRagClient.IndexResult indexStoredUpload(Long materialId,
                                                          String userId,
                                                          LearningMaterial material,
                                                          MultipartFile file,
                                                          Boolean highPrecision) {
        if (isVideoDocumentType(material.getDocumentType())) {
            return pythonRagClient.indexVideoSource(
                    materialId,
                    userId,
                    material,
                    file.getOriginalFilename(),
                    file.getContentType(),
                    highPrecision
            );
        }
        return pythonRagClient.indexFile(materialId, userId, material, file, highPrecision);
    }

    /**
     * 保存分片合并文件、创建资料记录并调用 Python 索引。
     */
    private LearningMaterialVO storeAndIndexMergedFile(Path mergedPath,
                                                       String filename,
                                                       String contentType,
                                                       String userId,
                                                       Boolean highPrecision) {
        String documentType = detectDocumentType(filename);
        ObjectStorageService.StoredObject storedObject = objectStorageService.store(mergedPath, filename, userId, documentType, contentType);
        LearningMaterial material = new LearningMaterial();
        material.setTitle(filename);
        material.setUserId(userId);
        material.setDocumentType(documentType);
        material.setSource("upload");
        material.setStatus("PENDING");
        material.setChunkCount(0);
        material.setOriginalFilename(filename);
        material.setOriginalFilePath(storedObject.sourcePath());
        material.setStorageType(storedObject.storageType());
        material.setObjectKey(storedObject.objectKey());
        material.setPublicUrl(storedObject.publicUrl());
        learningMaterialMapper.insert(material);
        learningMaterialMapper.updateStatus(material.getId(), "PARSING");
        material.setStatus("PARSING");
        scheduleAfterCommit(() -> ragIndexWorker.indexStoredMaterial(
                material.getId(),
                userId,
                Boolean.TRUE.equals(highPrecision)
        ));
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
        List<RagProgressVO> progressEvents = progressEvents(material.getId());
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
                .storageType(material.getStorageType())
                .objectKey(material.getObjectKey())
                .publicUrl(material.getPublicUrl())
                .latestProgress(progressEvents.isEmpty() ? null : progressEvents.get(0))
                .progressEvents(progressEvents)
                .createdAt(material.getCreatedAt())
                .updatedAt(material.getUpdatedAt())
                .build();
    }

    /**
     * 查询指定资料最近的 RAG 进度事件。
     */
    private List<RagProgressVO> progressEvents(Long materialId) {
        if (materialId == null) {
            return List.of();
        }
        try {
            LinkedHashSet<String> seen = new LinkedHashSet<>();
            return logEventMapper.findRecentProgressByMaterialId(materialId, 20).stream()
                    .map(this::toProgressVO)
                    .filter(progress -> seen.add(progressKey(progress)))
                    .limit(8)
                    .toList();
        } catch (Exception e) {
            log.debug("读取资料进度事件失败: materialId={}, reason={}", materialId, e.getMessage());
            return List.of();
        }
    }

    /**
     * 将日志事件转换为前端进度对象。
     */
    private RagProgressVO toProgressVO(LogEvent event) {
        JsonNode context = parseContext(event.getContextJson());
        return RagProgressVO.builder()
                .stageCode(defaultText(text(context, "stageCode"), event.getStage()))
                .stageLabel(text(context, "stageLabel"))
                .message(defaultText(text(context, "message"), event.getMessage()))
                .status(defaultText(text(context, "status"), Boolean.TRUE.equals(event.getSuccess()) ? "RUNNING" : "FAILED"))
                .currentStep(integer(context, "currentStep"))
                .totalSteps(integer(context, "totalSteps"))
                .currentChunk(integer(context, "currentChunk"))
                .totalChunks(integer(context, "totalChunks"))
                .chunkId(text(context, "chunkId"))
                .blockId(text(context, "blockId"))
                .percent(integer(context, "percent"))
                .detail(text(context, "detail"))
                .createdAt(event.getCreatedAt() == null ? null : event.getCreatedAt().toLocalDateTime())
                .build();
    }

    /**
     * 生成进度去重键，避免 Python 实时回调和 Java 返回补偿重复展示。
     */
    private String progressKey(RagProgressVO progress) {
        return String.join("|",
                defaultText(progress.getStageCode(), ""),
                defaultText(progress.getMessage(), ""),
                defaultText(progress.getChunkId(), ""),
                String.valueOf(progress.getCurrentChunk()),
                String.valueOf(progress.getTotalChunks())
        );
    }

    /**
     * 解析进度上下文 JSON。
     */
    private JsonNode parseContext(String contextJson) {
        if (contextJson == null || contextJson.isBlank()) {
            return objectMapper.createObjectNode();
        }
        try {
            return objectMapper.readTree(contextJson);
        } catch (Exception e) {
            return objectMapper.createObjectNode();
        }
    }

    /**
     * 读取 JSON 文本字段。
     */
    private String text(JsonNode node, String fieldName) {
        JsonNode value = node == null ? null : node.get(fieldName);
        return value == null || value.isNull() ? null : value.asText();
    }

    /**
     * 读取 JSON 整数字段。
     */
    private Integer integer(JsonNode node, String fieldName) {
        JsonNode value = node == null ? null : node.get(fieldName);
        return value == null || value.isNull() ? null : value.asInt();
    }

    /**
     * 为空文本提供默认值。
     */
    private String defaultText(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
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
        if (lower.endsWith(".mp4")) {
            return "mp4";
        }
        if (lower.endsWith(".mov")) {
            return "mov";
        }
        if (lower.endsWith(".m4v")) {
            return "m4v";
        }
        if (lower.endsWith(".webm")) {
            return "webm";
        }
        if (lower.endsWith(".mkv")) {
            return "mkv";
        }
        if (lower.endsWith(".avi")) {
            return "avi";
        }
        return "text";
    }

    /**
     * 校验上传分片基本参数，避免越界分片和空文件进入合并流程。
     */
    private void validateChunkRequest(MultipartFile file,
                                      String filename,
                                      Integer chunkIndex,
                                      Integer totalChunks,
                                      Long totalSize) {
        if (file == null || file.isEmpty()) {
            throw new IllegalArgumentException("上传分片不能为空");
        }
        if (filename == null || filename.isBlank()) {
            throw new IllegalArgumentException("上传文件名不能为空");
        }
        if (chunkIndex == null || totalChunks == null || chunkIndex < 0 || totalChunks <= 0 || chunkIndex >= totalChunks) {
            throw new IllegalArgumentException("分片参数不合法");
        }
        if (totalSize != null && totalSize <= 0) {
            throw new IllegalArgumentException("文件总大小不合法");
        }
    }

    /**
     * 构造当前用户的分片暂存目录。
     */
    private Path chunkDirectory(String userId, String uploadId) {
        return chunkRoot()
                .resolve(sanitizeFilenameToken(userId))
                .resolve(sanitizeUploadToken(uploadId))
                .toAbsolutePath()
                .normalize();
    }

    /**
     * 获取分片暂存根目录，可通过环境变量覆盖。
     */
    private Path chunkRoot() {
        return Path.of(System.getenv().getOrDefault("EVIDENCE_UPLOAD_CHUNK_ROOT", "uploads/chunks"))
                .toAbsolutePath()
                .normalize();
    }

    /**
     * 统计当前已收到的分片数量。
     */
    private int countReceivedChunks(Path directory) throws IOException {
        try (Stream<Path> files = Files.list(directory)) {
            return (int) files
                    .filter(path -> path.getFileName().toString().matches("chunk-\\d{5}\\.part"))
                    .count();
        }
    }

    /**
     * 按分片序号顺序合并文件，并校验合并后的总大小。
     */
    private Path mergeChunks(Path directory, String filename, int totalChunks, Long totalSize) throws IOException {
        Path mergedPath = directory.resolve("merged-" + sanitizeFilenameToken(filename));
        try (OutputStream outputStream = Files.newOutputStream(mergedPath)) {
            for (int index = 0; index < totalChunks; index++) {
                Path chunkPath = directory.resolve(String.format("chunk-%05d.part", index));
                if (!Files.exists(chunkPath)) {
                    throw new IllegalStateException("上传分片缺失: " + index);
                }
                Files.copy(chunkPath, outputStream);
            }
        }
        if (totalSize != null && Files.size(mergedPath) != totalSize) {
            throw new IllegalStateException("分片合并后的文件大小与前端声明不一致");
        }
        return mergedPath;
    }

    /**
     * 清理分片临时目录，先校验路径仍位于分片根目录内。
     */
    private void cleanupChunkDirectory(Path directory) {
        Path root = chunkRoot();
        Path target = directory.toAbsolutePath().normalize();
        if (!target.startsWith(root)) {
            log.warn("跳过分片目录清理，路径不在分片根目录内: {}", target);
            return;
        }
        try (Stream<Path> paths = Files.walk(target)) {
            paths.sorted(Comparator.reverseOrder()).forEach(path -> {
                try {
                    Files.deleteIfExists(path);
                } catch (IOException e) {
                    log.debug("清理分片临时文件失败: path={}, reason={}", path, e.getMessage());
                }
            });
        } catch (IOException e) {
            log.debug("清理分片临时目录失败: path={}, reason={}", target, e.getMessage());
        }
    }

    /**
     * 判断资料类型是否属于原始视频。
     */
    private boolean isVideoDocumentType(String documentType) {
        if (documentType == null) {
            return false;
        }
        return List.of("mp4", "mov", "m4v", "webm", "mkv", "avi").contains(documentType.toLowerCase(Locale.ROOT));
    }

    /**
     * 规整 uploadId，避免路径穿越。
     */
    private String sanitizeUploadToken(String value) {
        if (value == null || value.isBlank()) {
            return "";
        }
        return value.replaceAll("[^A-Za-z0-9_-]", "");
    }

    /**
     * 规整临时文件名片段。
     */
    private String sanitizeFilenameToken(String value) {
        String fallback = value == null || value.isBlank() ? "material" : value;
        return fallback
                .replaceAll("[\\\\/:*?\"<>|]+", "_")
                .replaceAll("\\s+", "_")
                .toLowerCase(Locale.ROOT);
    }

    /**
     * 为空文本提供默认值。
     */
    private String blankToDefault(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }

    /**
     * 将日期限制在允许查询的日期范围内。
     */
    private LocalDate clampDate(LocalDate value, LocalDate minDate, LocalDate maxDate) {
        if (value.isBefore(minDate)) {
            return minDate;
        }
        if (value.isAfter(maxDate)) {
            return maxDate;
        }
        return value;
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
     * 在当前事务提交后执行后台动作，确保异步线程能读取到资料记录。
     */
    private void scheduleAfterCommit(Runnable action) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    action.run();
                }
            });
            return;
        }
        action.run();
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
        if ("PARTIAL".equals(result.status()) && result.parseQualityMessages() != null && !result.parseQualityMessages().isEmpty()) {
            Map<String, Object> context = indexResultContext(material, result);
            context.put("errorLocation", result.parseQualityMessages());
            context.put("warningCount", result.parseQualityMessages().size());
            logService.recordRagError(
                    "material",
                    "index",
                    "material_index_partial_warning",
                    "RAG_INDEX_PARTIAL",
                    "Python 返回 PARTIAL 状态并携带阶段告警",
                    null,
                    context
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
        context.put("parseQualityMessages", result.parseQualityMessages());
        if (result.parseQualityMessages() != null && !result.parseQualityMessages().isEmpty()) {
            context.put("errorLocation", result.parseQualityMessages());
        }
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
            context.put("diagnosticKeys", result.getDiagnostics() == null ? List.of() : result.getDiagnostics().keySet().stream().toList());
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
