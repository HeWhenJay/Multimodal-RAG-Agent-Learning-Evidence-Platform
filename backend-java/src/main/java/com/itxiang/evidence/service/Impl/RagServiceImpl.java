package com.itxiang.evidence.service.Impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.LogEvent;
import com.itxiang.evidence.entity.RagQueryHistory;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.LogEventMapper;
import com.itxiang.evidence.mapper.RagQueryHistoryMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import com.itxiang.evidence.service.RagIndexTaskPublisher;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.service.command.RagUploadFinalizeCommand;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
import com.itxiang.evidence.vo.MaterialPreviewVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.RagProgressVO;
import com.itxiang.evidence.vo.RagQueryHistoryVO;
import com.itxiang.evidence.vo.RagQueryTaskVO;
import com.itxiang.evidence.vo.RagQueryVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.ArrayList;
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
    private final RagQueryHistoryMapper ragQueryHistoryMapper;
    private final PythonRagClient pythonRagClient;
    private final LogService logService;
    private final ObjectStorageService objectStorageService;
    private final RagIndexTaskPublisher ragIndexTaskPublisher;
    private final RagUploadWorker ragUploadWorker;
    private final TransactionTemplate transactionTemplate;
    private final ObjectMapper objectMapper;
    private Path chunkRootOverride;
    private static final int DEFAULT_TOP_K = 5;
    private static final int DEFAULT_CANDIDATE_MULTIPLIER = 4;
    private static final List<String> BUSINESS_METADATA_FILTER_KEYS = List.of(
            "documentId",
            "documentType",
            "source",
            "parser",
            "mediaType",
            "evidenceChannel",
            "blockType",
            "sectionName",
            "sectionKeyword",
            "pageIndex",
            "slideIndex"
    );
    private static final String IGNORED_METADATA_FILTER_KEYS = "__ignoredMetadataFilterKeys";

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
    public List<LearningMaterialVO> listRecentMaterials(String userId, LocalDate startDate, LocalDate endDate, Integer limit) {
        String scopedUserId = requireUserId(userId);
        int safeLimit = limit == null ? 5 : Math.max(1, Math.min(limit, 50));
        LocalDate today = LocalDate.now();
        LocalDate earliestDate = today.minusDays(6);
        LocalDate safeEndDate = endDate == null ? today : clampDate(endDate, earliestDate, today);
        LocalDate safeStartDate = startDate == null ? earliestDate : clampDate(startDate, earliestDate, today);
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
     * 读取文本类资料原文，供前端新标签页渲染预览。
     */
    @Override
    public MaterialPreviewVO previewMaterial(Long id, String source, String userId) {
        LearningMaterial material = learningMaterialMapper.findByIdAndUserId(id, requireUserId(userId));
        if (material == null) {
            throw new IllegalArgumentException("资料不存在");
        }
        if (!isPreviewableTextType(material.getDocumentType())) {
            throw new IllegalArgumentException("当前资料类型暂不支持文本预览");
        }
        validatePreviewSource(material, source);
        ObjectStorageService.LoadedObject loaded = objectStorageService.load(
                material.getStorageType(),
                material.getOriginalFilePath(),
                material.getObjectKey(),
                material.getOriginalFilename()
        );
        return MaterialPreviewVO.builder()
                .materialId(material.getId())
                .title(defaultText(material.getOriginalFilename(), material.getTitle()))
                .documentType(material.getDocumentType())
                .source(defaultText(firstNonBlank(source, material.getPublicUrl()), material.getOriginalFilePath()))
                .contentType(defaultText(loaded.contentType(), previewContentType(material.getDocumentType())))
                .content(stripUtf8Bom(new String(loaded.content(), StandardCharsets.UTF_8)))
                .build();
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
        if (ragIndexTaskPublisher.kafkaEnabled()) {
            ragIndexTaskPublisher.publishTextIndex(material, scopedUserId, dto);
            logService.recordRagEvent(
                    "material",
                    "index",
                    "material_index_text_queued",
                    "文本学习资料已提交 Kafka 索引队列",
                    materialContext(material)
            );
            return convertToVO(material);
        }
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
        dispatchStoredMaterialIndex(material, scopedUserId, Boolean.TRUE.equals(highPrecision), "INDEX_UPLOAD");
        return convertToVO(material);
    }

    /**
     * 接收学习资料分片，全部到齐后合并文件并触发索引。
     */
    @Override
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
            saveChunkAtomically(file, directory, chunkIndex);
            int receivedChunks = countReceivedChunks(directory);
            int nextChunkIndex = nextMissingChunkIndex(directory, totalChunks);
            if (receivedChunks < totalChunks) {
                return MaterialUploadChunkVO.builder()
                        .uploadId(safeUploadId)
                        .filename(filename)
                        .chunkIndex(chunkIndex)
                        .totalChunks(totalChunks)
                        .receivedChunks(receivedChunks)
                        .nextChunkIndex(nextChunkIndex)
                        .status("UPLOADING")
                        .message("已接收视频分片：" + receivedChunks + "/" + totalChunks + "，下次从第 " + (nextChunkIndex + 1) + " 片继续")
                        .completed(false)
                        .material(null)
                        .build();
            }
            LearningMaterial existingMaterial = findChunkUploadMaterial(directory, scopedUserId);
            if (existingMaterial != null) {
                rescheduleFailedChunkUploadIfNeeded(
                        existingMaterial,
                        scopedUserId,
                        directory,
                        safeUploadId,
                        filename,
                        file.getContentType(),
                        totalChunks,
                        totalSize,
                        Boolean.TRUE.equals(highPrecision)
                );
                return MaterialUploadChunkVO.builder()
                        .uploadId(safeUploadId)
                        .filename(filename)
                        .chunkIndex(chunkIndex)
                        .totalChunks(totalChunks)
                        .receivedChunks(totalChunks)
                        .nextChunkIndex(totalChunks)
                        .status("PROCESSING")
                        .message("视频分片已收齐，继续沿用已有后台处理任务")
                        .completed(true)
                        .material(convertToVO(existingMaterial))
                        .build();
            }
            LearningMaterial material = createPendingUploadMaterialAndScheduleChunkProcessing(
                    filename,
                    scopedUserId,
                    directory,
                    safeUploadId,
                    file.getContentType(),
                    totalChunks,
                    totalSize,
                    Boolean.TRUE.equals(highPrecision)
            );
            return MaterialUploadChunkVO.builder()
                    .uploadId(safeUploadId)
                    .filename(filename)
                    .chunkIndex(chunkIndex)
                    .totalChunks(totalChunks)
                    .receivedChunks(totalChunks)
                    .nextChunkIndex(totalChunks)
                    .status("PROCESSING")
                    .message("视频分片已收齐，正在后台合并并上传对象存储")
                    .completed(true)
                    .material(convertToVO(material))
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

        dispatchStoredMaterialIndex(material, scopedUserId, Boolean.TRUE.equals(highPrecision), "REINDEX");
        return convertToVO(material);
    }
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
     * 创建分片视频的待处理资料记录，后续由后台线程补写对象存储路径。
     */
    private LearningMaterial createPendingUploadMaterial(String filename, String userId) {
        String documentType = detectDocumentType(filename);
        LearningMaterial material = new LearningMaterial();
        material.setTitle(filename);
        material.setUserId(userId);
        material.setDocumentType(documentType);
        material.setSource("upload");
        material.setStatus("PENDING");
        material.setChunkCount(0);
        material.setOriginalFilename(filename);
        material.setStorageType("pending");
        learningMaterialMapper.insert(material);
        return material;
    }

    /**
     * 原子保存单个上传分片，避免半写入文件被后续合并误读。
     */
    private void saveChunkAtomically(MultipartFile file, Path directory, Integer chunkIndex) throws IOException {
        Files.createDirectories(directory);
        Path chunkPath = directory.resolve(chunkFilename(chunkIndex));
        Path tempPath = directory.resolve(chunkFilename(chunkIndex) + ".tmp");
        try (InputStream inputStream = file.getInputStream()) {
            Files.copy(inputStream, tempPath, StandardCopyOption.REPLACE_EXISTING);
        }
        try {
            Files.move(tempPath, chunkPath, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException atomicMoveError) {
            Files.move(tempPath, chunkPath, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    /**
     * 全部分片到齐后，用短事务创建资料记录并调度后台合并。
     */
    private LearningMaterial createPendingUploadMaterialAndScheduleChunkProcessing(String filename,
                                                                                  String userId,
                                                                                  Path directory,
                                                                                  String uploadId,
                                                                                  String contentType,
                                                                                  Integer totalChunks,
                                                                                  Long totalSize,
                                                                                  Boolean highPrecision) {
        LearningMaterial material = transactionTemplate.execute(status -> {
            LearningMaterial pendingMaterial = createPendingUploadMaterial(filename, userId);
            recordChunkProcessingProgress(pendingMaterial, uploadId, totalChunks);
            dispatchUploadFinalize(new RagUploadFinalizeCommand(
                    pendingMaterial.getId(),
                    userId,
                    directory,
                    chunkRoot(),
                    uploadId,
                    filename,
                    contentType,
                    totalChunks,
                    totalSize,
                    Boolean.TRUE.equals(highPrecision)
            ));
            return pendingMaterial;
        });
        if (material == null) {
            throw new IllegalStateException("创建分片上传资料记录失败");
        }
        writeChunkUploadMaterialMarker(directory, material.getId());
        return material;
    }

    /**
     * 后台合并或对象存储失败后，复用已上传分片重新调度收尾任务。
     */
    private void rescheduleFailedChunkUploadIfNeeded(LearningMaterial material,
                                                     String userId,
                                                     Path directory,
                                                     String uploadId,
                                                     String filename,
                                                     String contentType,
                                                     Integer totalChunks,
                                                     Long totalSize,
                                                     Boolean highPrecision) {
        if (!"FAILED".equals(material.getStatus()) || !"upload-chunk-error".equals(material.getParser())) {
            return;
        }
        transactionTemplate.executeWithoutResult(status -> {
            learningMaterialMapper.updateStatus(material.getId(), "PENDING");
            material.setStatus("PENDING");
            recordChunkProcessingProgress(material, uploadId, totalChunks);
            dispatchUploadFinalize(new RagUploadFinalizeCommand(
                    material.getId(),
                    userId,
                    directory,
                    chunkRoot(),
                    uploadId,
                    filename,
                    contentType,
                    totalChunks,
                    totalSize,
                    Boolean.TRUE.equals(highPrecision)
            ));
        });
    }

    /**
     * 记录分片收齐后的后台收尾进度，让前端立即看到“合并上传中”。
     */
    private void recordChunkProcessingProgress(LearningMaterial material, String uploadId, Integer totalChunks) {
        Map<String, Object> context = materialContext(material);
        context.put("uploadId", uploadId);
        context.put("stageCode", "upload.processing");
        context.put("stageLabel", "后台合并上传");
        context.put("message", "视频分片已收齐，正在后台合并并上传对象存储");
        context.put("status", "RUNNING");
        context.put("currentStep", 1);
        context.put("totalSteps", 8);
        context.put("currentChunk", totalChunks);
        context.put("totalChunks", totalChunks);
        context.put("percent", 8);
        logService.recordRagProgress(
                "material",
                "upload.processing",
                "material_upload_chunk_processing",
                "视频分片已收齐，正在后台合并并上传对象存储",
                context,
                true
        );
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
            saveSynchronousQueryHistory(scopedDto, scopedUserId, result, System.currentTimeMillis() - start);
            Map<String, Object> context = queryContext(scopedDto, result, System.currentTimeMillis() - start);
            String action = "REFUSED".equals(result.getAnswerStatus())
                    ? "rag_query_refused"
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
     * 执行 Agent Tool Gateway 专用非持久化查询，只记录脱敏观测日志，不写 rag_query_history。
     */
    @Override
    public RagQueryVO queryNonPersistent(RagQueryDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        RagQueryDTO scopedDto = scopedQuery(dto, scopedUserId);
        long start = System.currentTimeMillis();
        logService.recordRagEvent(
                "rag_query",
                "retrieve",
                "rag_query_probe_non_persistent_start",
                "开始 Agent 非持久化 RAG 探针",
                queryContext(scopedDto, null, null)
        );
        try {
            RagQueryVO result = pythonRagClient.query(scopedDto);
            logService.recordRagEvent(
                    "rag_query",
                    "retrieve",
                    "rag_query_probe_non_persistent_success",
                    "Agent 非持久化 RAG 探针完成",
                    queryContext(scopedDto, result, System.currentTimeMillis() - start)
            );
            return result;
        } catch (Exception e) {
            Map<String, Object> context = queryContext(scopedDto, null, System.currentTimeMillis() - start);
            context.putAll(pythonExceptionContext(e));
            logService.recordRagError(
                    "rag_query",
                    "retrieve",
                    "rag_query_probe_non_persistent_failed",
                    resolveRagErrorCode(e),
                    "Agent 非持久化 RAG 探针失败",
                    e,
                    context
            );
            throw e;
        }
    }

    /**
     * 查询当前用户最近几次 RAG 询问历史。
     */
    @Override
    public List<RagQueryHistoryVO> listQueryHistory(String userId, LocalDate startDate, LocalDate endDate, Integer limit) {
        String scopedUserId = requireUserId(userId);
        int safeLimit = safeRecentLimit(limit);
        DateRange range = normalizeRecentDateRange(startDate, endDate);
        return ragQueryHistoryMapper.findRecentByUserIdBetween(
                        scopedUserId,
                        range.startDate().atStartOfDay(),
                        range.endDate().plusDays(1).atStartOfDay(),
                        safeLimit
                ).stream()
                .map(this::convertQueryHistoryToVO)
                .toList();
    }

    /**
     * 创建 Python RAG 查询任务，前端通过任务 ID 轮询实时进度。
     */
    @Override
    public RagQueryTaskVO startQueryTask(RagQueryDTO dto, String userId) {
        String scopedUserId = requireUserId(userId);
        RagQueryDTO scopedDto = scopedQuery(dto, scopedUserId);
        logService.recordRagEvent(
                "rag_query",
                "retrieve",
                "rag_query_task_start",
                "开始 RAG 查询任务",
                queryContext(scopedDto, null, null)
        );
        try {
            RagQueryTaskVO task = pythonRagClient.startQueryTask(scopedDto);
            saveQueryTaskHistory(scopedDto, scopedUserId, task);
            Map<String, Object> context = queryContext(scopedDto, null, null);
            context.put("taskId", task.getTaskId());
            context.put("taskStatus", task.getStatus());
            logService.recordRagEvent(
                    "rag_query",
                    "retrieve",
                    "rag_query_task_created",
                    "RAG 查询任务已创建",
                    context
            );
            return task;
        } catch (Exception e) {
            Map<String, Object> context = queryContext(scopedDto, null, null);
            context.putAll(pythonExceptionContext(e));
            logService.recordRagError(
                    "rag_query",
                    "retrieve",
                    "rag_query_task_failed",
                    resolveRagErrorCode(e),
                    "RAG 查询任务创建失败",
                    e,
                    context
            );
            throw e;
        }
    }

    /**
     * 读取 Python RAG 查询任务状态。
     */
    @Override
    public RagQueryTaskVO getQueryTask(String taskId, String userId) {
        String scopedUserId = requireUserId(userId);
        if (taskId == null || taskId.isBlank()) {
            throw new IllegalArgumentException("查询任务 ID 不能为空");
        }
        RagQueryTaskVO task = pythonRagClient.getQueryTask(taskId.trim());
        updateQueryTaskHistory(scopedUserId, task);
        return task;
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
            List<RagProgressVO> recentProgress = logEventMapper.findRecentProgressByMaterialId(materialId, 40).stream()
                    .map(this::toProgressVO)
                    .toList();
            List<RagProgressVO> videoProgress = logEventMapper.findVideoProgressByMaterialId(materialId, 80).stream()
                    .map(this::toProgressVO)
                    .toList();
            List<RagProgressVO> merged = new java.util.ArrayList<>();
            merged.addAll(recentProgress);
            merged.addAll(videoProgress);
            return merged.stream()
                    .filter(progress -> seen.add(progressKey(progress)))
                    .limit(30)
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
     * 保存同步 RAG 查询历史，便于刷新后查看最近询问。
     */
    private void saveSynchronousQueryHistory(RagQueryDTO dto, String userId, RagQueryVO result, long durationMs) {
        RagQueryHistory history = new RagQueryHistory();
        history.setUserId(userId);
        history.setQuestion(dto.getQuestion());
        history.setAnswer(result.getAnswer());
        history.setStatus("COMPLETED");
        history.setTopK(dto.getTopK() == null ? 5 : dto.getTopK());
        history.setEvidenceCount(result.getEvidences() == null ? 0 : result.getEvidences().size());
        history.setExpandedQueriesJson(toJson(result.getExpandedQueries(), "[]"));
        history.setEvidencesJson(toJson(result.getEvidences(), "[]"));
        history.setDiagnosticsJson(toJson(diagnosticsWithAnswerGuard(result), "{}"));
        history.setProgressEventsJson(toJson(result.getProgressEvents(), "[]"));
        history.setDurationMs(safeDuration(durationMs));
        ragQueryHistoryMapper.insert(history);
    }

    /**
     * 创建任务时先保存一条运行中的查询历史。
     */
    private void saveQueryTaskHistory(RagQueryDTO dto, String userId, RagQueryTaskVO task) {
        RagQueryHistory history = new RagQueryHistory();
        history.setUserId(userId);
        history.setTaskId(task.getTaskId());
        history.setQuestion(dto.getQuestion());
        history.setAnswer(null);
        history.setStatus(defaultText(task.getStatus(), "RUNNING"));
        history.setTopK(dto.getTopK() == null ? 5 : dto.getTopK());
        history.setEvidenceCount(0);
        history.setExpandedQueriesJson("[]");
        history.setEvidencesJson("[]");
        history.setDiagnosticsJson("{}");
        history.setProgressEventsJson(toJson(task.getProgressEvents(), "[]"));
        history.setErrorMessage(task.getErrorMessage());
        ragQueryHistoryMapper.insert(history);
    }

    /**
     * 轮询到任务终态后回写查询历史。
     */
    private void updateQueryTaskHistory(String userId, RagQueryTaskVO task) {
        if (task == null || task.getTaskId() == null || task.getTaskId().isBlank()) {
            return;
        }
        RagQueryHistory existing = ragQueryHistoryMapper.findByTaskIdAndUserId(task.getTaskId(), userId);
        if (existing == null) {
            return;
        }
        RagQueryVO result = task.getResult();
        RagQueryHistory history = new RagQueryHistory();
        history.setUserId(userId);
        history.setTaskId(task.getTaskId());
        history.setAnswer(result == null ? existing.getAnswer() : result.getAnswer());
        history.setStatus(defaultText(task.getStatus(), existing.getStatus()));
        history.setEvidenceCount(result == null || result.getEvidences() == null ? existing.getEvidenceCount() : result.getEvidences().size());
        history.setExpandedQueriesJson(result == null ? existing.getExpandedQueriesJson() : toJson(result.getExpandedQueries(), "[]"));
        history.setEvidencesJson(result == null ? existing.getEvidencesJson() : toJson(result.getEvidences(), "[]"));
        history.setDiagnosticsJson(result == null ? existing.getDiagnosticsJson() : toJson(diagnosticsWithAnswerGuard(result), "{}"));
        history.setProgressEventsJson(toJson(task.getProgressEvents(), "[]"));
        history.setErrorMessage(task.getErrorMessage());
        history.setDurationMs(calculateDurationMs(existing, task));
        ragQueryHistoryMapper.updateByTaskId(history);
    }

    /**
     * 将历史实体转换为前端可直接回填的 VO。
     */
    private RagQueryHistoryVO convertQueryHistoryToVO(RagQueryHistory history) {
        Map<String, Object> diagnostics = fromJson(history.getDiagnosticsJson(), new TypeReference<Map<String, Object>>() {}, Map.of());
        Map<String, Object> answerGuard = answerGuardFromDiagnostics(diagnostics);
        return RagQueryHistoryVO.builder()
                .id(history.getId())
                .taskId(history.getTaskId())
                .question(history.getQuestion())
                .answer(history.getAnswer())
                .answerStatus(queryAnswerStatus(answerGuard, history.getEvidenceCount()))
                .refusalReason(stringValue(answerGuard.get("refusalReason")))
                .refusalPolicy(defaultText(stringValue(answerGuard.get("refusalPolicy")), "STRICT_EVIDENCE_GUARD_V1"))
                .confidence(numberValue(answerGuard.get("confidence"), 0.0))
                .supportingEvidenceIds(readStringList(answerGuard.get("supportingEvidenceIds")))
                .refusalMessage(stringValue(answerGuard.get("refusalMessage")))
                .status(history.getStatus())
                .topK(history.getTopK())
                .evidenceCount(history.getEvidenceCount())
                .expandedQueries(fromJson(history.getExpandedQueriesJson(), new TypeReference<List<String>>() {}, List.of()))
                .evidences(fromJson(history.getEvidencesJson(), new TypeReference<List<RagEvidenceVO>>() {}, List.of()))
                .diagnostics(diagnostics)
                .progressEvents(fromJson(history.getProgressEventsJson(), new TypeReference<List<RagProgressVO>>() {}, List.of()))
                .errorMessage(history.getErrorMessage())
                .durationMs(history.getDurationMs())
                .createdAt(toLocalDateTime(history.getCreatedAt()))
                .updatedAt(toLocalDateTime(history.getUpdatedAt()))
                .build();
    }

    /**
     * 将查询结果中的 guard 顶层字段同步写入 diagnostics.answerGuard，兼容旧 Python 响应。
     */
    private Map<String, Object> diagnosticsWithAnswerGuard(RagQueryVO result) {
        Map<String, Object> diagnostics = new LinkedHashMap<>();
        if (result != null && result.getDiagnostics() != null) {
            diagnostics.putAll(result.getDiagnostics());
        }
        Map<String, Object> answerGuard = answerGuardFromDiagnostics(diagnostics);
        String answerStatus = defaultText(
                result == null ? null : result.getAnswerStatus(),
                result == null || result.getEvidences() == null || result.getEvidences().isEmpty() ? "REFUSED" : "ANSWERED"
        );
        List<String> supportingEvidenceIds = result == null || result.getSupportingEvidenceIds() == null
                ? List.of()
                : result.getSupportingEvidenceIds();
        if (supportingEvidenceIds.isEmpty() && "ANSWERED".equals(answerStatus) && result != null && result.getEvidences() != null) {
            supportingEvidenceIds = result.getEvidences().stream().map(RagEvidenceVO::getEvidenceId).toList();
        }
        answerGuard.put("answerStatus", answerStatus);
        answerGuard.put("refusalReason", result == null ? null : result.getRefusalReason());
        answerGuard.put("refusalPolicy", defaultText(result == null ? null : result.getRefusalPolicy(), "STRICT_EVIDENCE_GUARD_V1"));
        answerGuard.put("confidence", result == null || result.getConfidence() == null ? 0.0 : result.getConfidence());
        answerGuard.put("supportingEvidenceIds", supportingEvidenceIds);
        answerGuard.put("refusalMessage", result == null ? null : result.getRefusalMessage());
        diagnostics.put("answerGuard", answerGuard);
        return diagnostics;
    }

    /**
     * 从 diagnostics 中读取 answerGuard，字段名统一转为 String。
     */
    private Map<String, Object> answerGuardFromDiagnostics(Map<String, Object> diagnostics) {
        Object value = diagnostics == null ? null : diagnostics.get("answerGuard");
        Map<String, Object> result = new LinkedHashMap<>();
        if (value instanceof Map<?, ?> map) {
            map.forEach((key, item) -> result.put(String.valueOf(key), item));
        }
        return result;
    }

    /**
     * 历史缺少 answerStatus 时按 evidenceCount 兼容旧数据。
     */
    private String queryAnswerStatus(Map<String, Object> answerGuard, Integer evidenceCount) {
        String status = stringValue(answerGuard.get("answerStatus"));
        if (status != null && !status.isBlank()) {
            return status;
        }
        return evidenceCount == null || evidenceCount == 0 ? "REFUSED" : "ANSWERED";
    }

    /**
     * 将对象序列化为 JSON 字符串，失败时返回安全默认值。
     */
    private String toJson(Object value, String fallback) {
        try {
            return value == null ? fallback : objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            log.debug("RAG 查询历史 JSON 序列化失败: {}", e.getMessage());
            return fallback;
        }
    }

    /**
     * 从历史 JSON 字符串读取结构化对象。
     */
    private <T> T fromJson(String value, TypeReference<T> type, T fallback) {
        if (value == null || value.isBlank()) {
            return fallback;
        }
        try {
            return objectMapper.readValue(value, type);
        } catch (Exception e) {
            log.debug("RAG 查询历史 JSON 解析失败: {}", e.getMessage());
            return fallback;
        }
    }

    /**
     * 将 Python 字段绑定转换为数据库字段记录。
     */
        private String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    /**
     * 读取 Map 中的整数字段。
     */
    private Integer intValue(Object value, Integer fallback) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value == null) {
            return fallback;
        }
        try {
            return Integer.parseInt(String.valueOf(value));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    /**
     * 读取 Map 中的小数字段，失败时返回默认值。
     */
    private Double numberValue(Object value, Double fallback) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        if (value == null) {
            return fallback;
        }
        try {
            return Double.parseDouble(String.valueOf(value));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    /**
     * 读取 Map 中的字符串数组字段。
     */
    private List<String> readStringList(Object value) {
        if (!(value instanceof List<?> items)) {
            return List.of();
        }
        return items.stream().map(String::valueOf).toList();
    }

    /**
     * 读取 Map 中的浮点数字段。
     */
        private DateRange normalizeRecentDateRange(LocalDate startDate, LocalDate endDate) {
        LocalDate today = LocalDate.now();
        LocalDate earliestDate = today.minusDays(6);
        LocalDate safeEndDate = endDate == null ? today : clampDate(endDate, earliestDate, today);
        LocalDate safeStartDate = startDate == null ? earliestDate : clampDate(startDate, earliestDate, today);
        if (safeStartDate.isAfter(safeEndDate)) {
            safeStartDate = safeEndDate;
        }
        return new DateRange(safeStartDate, safeEndDate);
    }

    /**
     * 约束近期历史返回条数。
     */
    private int safeRecentLimit(Integer limit) {
        return limit == null ? 5 : Math.max(1, Math.min(limit, 50));
    }

    /**
     * 计算任务从创建到最近更新时间的耗时。
     */
    private Integer calculateDurationMs(RagQueryHistory existing, RagQueryTaskVO task) {
        if (existing.getCreatedAt() == null || task.getUpdatedAt() == null) {
            return existing.getDurationMs();
        }
        long duration = java.time.Duration.between(existing.getCreatedAt().toLocalDateTime(), task.getUpdatedAt()).toMillis();
        return safeDuration(duration);
    }

    /**
     * 将数据库带时区时间转换为前端沿用的本地时间结构。
     */
    private LocalDateTime toLocalDateTime(OffsetDateTime value) {
        return value == null ? null : value.toLocalDateTime();
    }

    /**
     * 防止耗时超过 Integer 范围。
     */
    private Integer safeDuration(long durationMs) {
        if (durationMs < 0) {
            return 0;
        }
        return durationMs > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int) durationMs;
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
     * 当前预览页只渲染能按 UTF-8 文本读取的资料。
     */
    private boolean isPreviewableTextType(String documentType) {
        if (documentType == null) {
            return false;
        }
        return List.of("markdown", "md", "txt", "text", "srt", "vtt").contains(documentType.toLowerCase(Locale.ROOT));
    }

    /**
     * 校验 evidence 来源确实指向当前资料，避免预览接口成为任意来源代理。
     */
    private void validatePreviewSource(LearningMaterial material, String source) {
        String normalizedSource = normalizePreviewSource(source);
        if (normalizedSource == null) {
            return;
        }
        boolean matched = Stream.of(
                        material.getOriginalFilePath(),
                        material.getPublicUrl(),
                        material.getObjectKey(),
                        material.getObjectKey() == null ? null : "oss://" + material.getObjectKey()
                )
                .map(this::normalizePreviewSource)
                .anyMatch(allowed -> previewSourceMatches(allowed, normalizedSource));
        if (!matched) {
            throw new IllegalArgumentException("预览来源不属于当前资料");
        }
    }

    /**
     * 预览来源比较时去掉章节 hash 和尖括号包装。
     */
    private String normalizePreviewSource(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String text = value.trim().replaceAll("^<|>$", "");
        int hashIndex = text.indexOf('#');
        if (hashIndex >= 0) {
            text = text.substring(0, hashIndex);
        }
        return text.replace('\\', '/');
    }

    /**
     * 支持完整 URL、oss key 和文件名三种来源匹配。
     */
    private boolean previewSourceMatches(String allowedSource, String requestedSource) {
        if (allowedSource == null || requestedSource == null) {
            return false;
        }
        return requestedSource.equals(allowedSource)
                || requestedSource.endsWith("/" + allowedSource)
                || allowedSource.endsWith("/" + requestedSource);
    }

    /**
     * 根据资料类型返回预览内容类型。
     */
    private String previewContentType(String documentType) {
        String type = documentType == null ? "" : documentType.toLowerCase(Locale.ROOT);
        if ("markdown".equals(type) || "md".equals(type)) {
            return "text/markdown; charset=UTF-8";
        }
        if ("srt".equals(type) || "vtt".equals(type)) {
            return "text/plain; charset=UTF-8";
        }
        return "text/plain; charset=UTF-8";
    }

    /**
     * UTF-8 BOM 会影响 Markdown 首行标题识别，这里统一去掉。
     */
    private String stripUtf8Bom(String content) {
        if (content == null || content.isEmpty()) {
            return "";
        }
        return content.charAt(0) == '\uFEFF' ? content.substring(1) : content;
    }

    /**
     * 返回第一段非空文本。
     */
    private String firstNonBlank(String first, String second) {
        return first != null && !first.isBlank() ? first : second;
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
        if (chunkRootOverride != null) {
            return chunkRootOverride.toAbsolutePath().normalize();
        }
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
     * 查找最小缺失分片序号，供前端失败后从该分片继续上传。
     */
    private int nextMissingChunkIndex(Path directory, int totalChunks) {
        for (int index = 0; index < totalChunks; index++) {
            if (!Files.exists(directory.resolve(chunkFilename(index)))) {
                return index;
            }
        }
        return totalChunks;
    }

    /**
     * 生成分片文件名。
     */
    private String chunkFilename(int chunkIndex) {
        return String.format("chunk-%05d.part", chunkIndex);
    }

    /**
     * 从分片目录读取已创建的资料记录，避免最后一片重试时重复创建资料。
     */
    private LearningMaterial findChunkUploadMaterial(Path directory, String userId) {
        Path markerPath = directory.resolve("material.id");
        if (!Files.exists(markerPath)) {
            return null;
        }
        try {
            String value = Files.readString(markerPath, StandardCharsets.UTF_8).trim();
            if (value.isBlank()) {
                return null;
            }
            return learningMaterialMapper.findByIdAndUserId(Long.parseLong(value), userId);
        } catch (Exception e) {
            log.debug("读取分片上传资料标记失败: path={}, reason={}", markerPath, e.getMessage());
            return null;
        }
    }

    /**
     * 写入分片上传资料标记，用于最终分片重试时保持幂等。
     */
    private void writeChunkUploadMaterialMarker(Path directory, Long materialId) {
        if (materialId == null) {
            return;
        }
        try {
            Files.writeString(directory.resolve("material.id"), String.valueOf(materialId), StandardCharsets.UTF_8);
        } catch (IOException e) {
            log.debug("写入分片上传资料标记失败: materialId={}, reason={}", materialId, e.getMessage());
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
     * 最近日期查询范围。
     */
    private record DateRange(LocalDate startDate, LocalDate endDate) {
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
     * 按 Kafka 开关调度资料索引，Kafka 模式需在当前事务内写 Outbox。
     */
    private void dispatchStoredMaterialIndex(LearningMaterial material, String userId, Boolean highPrecision, String operation) {
        if (ragIndexTaskPublisher.kafkaEnabled()) {
            ragIndexTaskPublisher.publishStoredMaterialIndex(material, userId, highPrecision, operation);
            return;
        }
        scheduleAfterCommit(() -> ragIndexTaskPublisher.publishStoredMaterialIndex(material, userId, highPrecision, operation));
    }

    /**
     * 分片收尾依赖本机临时目录，始终在接收最后分片的 Java 实例本地异步执行。
     */
    private void dispatchUploadFinalize(RagUploadFinalizeCommand command) {
        scheduleAfterCommit(() -> ragUploadWorker.completeChunkedUpload(
                command.materialId(),
                command.userId(),
                command.directory(),
                command.chunkRoot(),
                command.uploadId(),
                command.filename(),
                command.contentType(),
                command.totalChunks(),
                command.totalSize(),
                command.highPrecision()
        ));
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
     * 归一化查询参数，并将查询强制限定在当前登录用户资料范围内。
     */
    private RagQueryDTO scopedQuery(RagQueryDTO dto, String userId) {
        RagQueryDTO scoped = new RagQueryDTO();
        scoped.setQuestion(dto.getQuestion());
        scoped.setTopK(clampNumber(dto.getTopK(), 1, 20, DEFAULT_TOP_K));
        scoped.setCandidateMultiplier(clampNumber(dto.getCandidateMultiplier(), 2, 10, DEFAULT_CANDIDATE_MULTIPLIER));
        Map<String, Object> metadataFilter = new LinkedHashMap<>();
        List<String> ignoredKeys = new java.util.ArrayList<>();
        if (dto.getMetadataFilter() != null) {
            dto.getMetadataFilter().forEach((key, value) -> {
                if (!BUSINESS_METADATA_FILTER_KEYS.contains(key)) {
                    ignoredKeys.add(key);
                    return;
                }
                Object normalizedValue = normalizeMetadataFilterValue(key, value);
                if (normalizedValue == null) {
                    ignoredKeys.add(key);
                    return;
                }
                metadataFilter.put(key, normalizedValue);
            });
        }
        metadataFilter.put("userId", userId);
        metadataFilter.put("visibilityScope", "private");
        if (!ignoredKeys.isEmpty()) {
            metadataFilter.put(IGNORED_METADATA_FILTER_KEYS, ignoredKeys.stream().distinct().toList());
        }
        scoped.setMetadataFilter(metadataFilter);
        return scoped;
    }

    /**
     * 将 topK 和候选倍率限制在接口契约范围内。
     */
    private int clampNumber(Integer value, int min, int max, int defaultValue) {
        int safeValue = value == null ? defaultValue : value;
        return Math.max(min, Math.min(max, safeValue));
    }

    /**
     * 清理业务元数据过滤值，页码和幻灯片序号统一按字符串传给 Python。
     */
    private Object normalizeMetadataFilterValue(String key, Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof String text) {
            String trimmed = text.trim();
            return trimmed.isEmpty() ? null : trimmed;
        }
        if (value instanceof Iterable<?> iterable) {
            List<String> items = new java.util.ArrayList<>();
            for (Object item : iterable) {
                String normalized = normalizeSingleFilterValue(item);
                if (normalized != null) {
                    items.add(normalized);
                }
            }
            return items.isEmpty() ? null : items;
        }
        if ("pageIndex".equals(key) || "slideIndex".equals(key)) {
            return normalizeSingleFilterValue(value);
        }
        return value;
    }

    /**
     * 将单个过滤值转换为非空字符串。
     */
    private String normalizeSingleFilterValue(Object value) {
        if (value == null) {
            return null;
        }
        String text = String.valueOf(value).trim();
        return text.isEmpty() ? null : text;
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
        context.put("topK", dto.getTopK() == null ? DEFAULT_TOP_K : dto.getTopK());
        context.put("candidateMultiplier", dto.getCandidateMultiplier() == null ? DEFAULT_CANDIDATE_MULTIPLIER : dto.getCandidateMultiplier());
        context.put("hasMetadataFilter", dto.getMetadataFilter() != null && !dto.getMetadataFilter().isEmpty());
        context.put("metadataFilterKeys", visibleMetadataFilterKeys(dto.getMetadataFilter()));
        context.put("ignoredMetadataFilterKeys", ignoredMetadataFilterKeys(dto.getMetadataFilter()));
        if (result != null) {
            context.put("expandedQueryCount", result.getExpandedQueries() == null ? 0 : result.getExpandedQueries().size());
            context.put("evidenceCount", result.getEvidences() == null ? 0 : result.getEvidences().size());
            context.put("answerStatus", result.getAnswerStatus());
            context.put("refusalReason", result.getRefusalReason());
            context.put("confidence", result.getConfidence());
            context.put("supportingEvidenceCount", result.getSupportingEvidenceIds() == null ? 0 : result.getSupportingEvidenceIds().size());
            context.put("diagnosticKeys", result.getDiagnostics() == null ? List.of() : result.getDiagnostics().keySet().stream().toList());
        }
        if (elapsedMs != null) {
            context.put("elapsedMs", elapsedMs);
        }
        return context;
    }

    /**
     * 返回可观测日志中的过滤字段名，排除内部诊断字段。
     */
    private List<String> visibleMetadataFilterKeys(Map<String, Object> metadataFilter) {
        if (metadataFilter == null || metadataFilter.isEmpty()) {
            return List.of();
        }
        return metadataFilter.keySet().stream()
                .filter(key -> !IGNORED_METADATA_FILTER_KEYS.equals(key))
                .toList();
    }

    /**
     * 读取被归一化流程忽略的过滤字段名。
     */
    @SuppressWarnings("unchecked")
    private List<String> ignoredMetadataFilterKeys(Map<String, Object> metadataFilter) {
        if (metadataFilter == null) {
            return List.of();
        }
        Object ignored = metadataFilter.get(IGNORED_METADATA_FILTER_KEYS);
        if (ignored instanceof List<?> items) {
            return items.stream().map(String::valueOf).toList();
        }
        return List.of();
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
