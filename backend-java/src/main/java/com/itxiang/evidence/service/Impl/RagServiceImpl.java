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
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
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
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.LocalDateTime;
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
    private final RagIndexWorker ragIndexWorker;
    private final RagUploadWorker ragUploadWorker;
    private final TransactionTemplate transactionTemplate;
    private final ObjectMapper objectMapper;
    private Path chunkRootOverride;

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
            scheduleAfterCommit(() -> ragUploadWorker.completeChunkedUpload(
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
            scheduleAfterCommit(() -> ragUploadWorker.completeChunkedUpload(
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
        history.setDiagnosticsJson(toJson(result.getDiagnostics(), "{}"));
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
        history.setDiagnosticsJson(result == null ? existing.getDiagnosticsJson() : toJson(result.getDiagnostics(), "{}"));
        history.setProgressEventsJson(toJson(task.getProgressEvents(), "[]"));
        history.setErrorMessage(task.getErrorMessage());
        history.setDurationMs(calculateDurationMs(existing, task));
        ragQueryHistoryMapper.updateByTaskId(history);
    }

    /**
     * 将历史实体转换为前端可直接回填的 VO。
     */
    private RagQueryHistoryVO convertQueryHistoryToVO(RagQueryHistory history) {
        return RagQueryHistoryVO.builder()
                .id(history.getId())
                .taskId(history.getTaskId())
                .question(history.getQuestion())
                .answer(history.getAnswer())
                .status(history.getStatus())
                .topK(history.getTopK())
                .evidenceCount(history.getEvidenceCount())
                .expandedQueries(fromJson(history.getExpandedQueriesJson(), new TypeReference<List<String>>() {}, List.of()))
                .evidences(fromJson(history.getEvidencesJson(), new TypeReference<List<RagEvidenceVO>>() {}, List.of()))
                .diagnostics(fromJson(history.getDiagnosticsJson(), new TypeReference<Map<String, Object>>() {}, Map.of()))
                .progressEvents(fromJson(history.getProgressEventsJson(), new TypeReference<List<RagProgressVO>>() {}, List.of()))
                .errorMessage(history.getErrorMessage())
                .durationMs(history.getDurationMs())
                .createdAt(history.getCreatedAt())
                .updatedAt(history.getUpdatedAt())
                .build();
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
     * 将近期历史日期范围限制在最近 7 天。
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
        long duration = java.time.Duration.between(existing.getCreatedAt(), task.getUpdatedAt()).toMillis();
        return safeDuration(duration);
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
