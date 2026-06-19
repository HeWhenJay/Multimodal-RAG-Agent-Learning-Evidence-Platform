package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.stream.Stream;

@Slf4j
@Service
@RequiredArgsConstructor
public class RagUploadWorker {

    private final LearningMaterialMapper learningMaterialMapper;
    private final ObjectStorageService objectStorageService;
    private final RagIndexWorker ragIndexWorker;
    private final LogService logService;
    private final TransactionTemplate transactionTemplate;

    /**
     * 后台完成分片合并、对象存储上传和 Python 索引触发，避免最后一个分片请求阻塞前端。
     */
    @Async
    public void completeChunkedUpload(Long materialId,
                                      String userId,
                                      Path directory,
                                      Path chunkRoot,
                                      String uploadId,
                                      String filename,
                                      String contentType,
                                      Integer totalChunks,
                                      Long totalSize,
                                      Boolean highPrecision) {
        LearningMaterial material = learningMaterialMapper.findByIdAndUserId(materialId, userId);
        if (material == null) {
            log.warn("分片上传后台收尾跳过，资料不存在或用户不匹配: materialId={}, userId={}", materialId, userId);
            return;
        }
        try {
            log.info("RAG分片上传后台收尾开始: materialId={}, uploadId={}, filename={}", materialId, uploadId, filename);
            recordUploadProgress(material, uploadId, "upload.merge", "合并分片", "正在合并视频分片", totalChunks, 10, true);
            Path mergedPath = mergeChunks(directory, filename, totalChunks, totalSize);

            recordUploadProgress(material, uploadId, "upload.oss", "上传对象存储", "正在上传视频到对象存储", totalChunks, 12, true);
            ObjectStorageService.StoredObject storedObject = objectStorageService.store(
                    mergedPath,
                    filename,
                    userId,
                    material.getDocumentType(),
                    contentType
            );

            transactionTemplate.executeWithoutResult(status -> {
                learningMaterialMapper.updateStorageInfo(
                        material.getId(),
                        storedObject.sourcePath(),
                        storedObject.storageType(),
                        storedObject.objectKey(),
                        storedObject.publicUrl()
                );
                learningMaterialMapper.updateStatus(material.getId(), "PARSING");
                material.setOriginalFilePath(storedObject.sourcePath());
                material.setStorageType(storedObject.storageType());
                material.setObjectKey(storedObject.objectKey());
                material.setPublicUrl(storedObject.publicUrl());
                material.setStatus("PARSING");
                Map<String, Object> context = materialContext(material);
                context.put("uploadId", uploadId);
                context.put("storageType", storedObject.storageType());
                context.put("objectKey", storedObject.objectKey());
                logService.recordRagEvent(
                        "material",
                        "upload",
                        "material_chunk_upload_stored",
                        "视频分片已合并并保存到对象存储",
                        context
                );
                recordUploadProgress(material, uploadId, "upload.stored", "对象存储完成", "视频已保存，准备进入 RAG 解析", totalChunks, 15, true);
            });

            cleanupChunkDirectory(directory, chunkRoot);
            ragIndexWorker.indexStoredMaterial(material.getId(), userId, Boolean.TRUE.equals(highPrecision));
            log.info("RAG分片上传后台收尾完成: materialId={}, uploadId={}", materialId, uploadId);
        } catch (Exception e) {
            log.warn("RAG分片上传后台收尾失败: materialId={}, uploadId={}, reason={}", materialId, uploadId, e.getMessage());
            recordUploadProgress(material, uploadId, "upload.failed", "上传失败", "视频分片后台处理失败：" + truncate(e.getMessage(), 160), totalChunks, 0, false);
            logService.recordRagError(
                    "material",
                    "upload",
                    "material_chunk_upload_async_failed",
                    "RAG_UPLOAD_CHUNK_ASYNC_FAILED",
                    "视频分片后台处理失败",
                    e,
                    materialContext(material)
            );
            transactionTemplate.executeWithoutResult(status -> markFailed(material, e.getMessage()));
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
     * 清理分片临时目录，先校验目标目录真实存在。
     */
    private void cleanupChunkDirectory(Path directory, Path chunkRoot) {
        if (directory == null || !Files.exists(directory)) {
            return;
        }
        Path target = directory.toAbsolutePath().normalize();
        Path root = chunkRoot == null ? null : chunkRoot.toAbsolutePath().normalize();
        if (root == null || !target.startsWith(root)) {
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
            log.debug("清理分片临时目录失败: path={}, reason={}", directory, e.getMessage());
        }
    }

    /**
     * 记录后台上传收尾进度，供前端轮询展示。
     */
    private void recordUploadProgress(LearningMaterial material,
                                      String uploadId,
                                      String stageCode,
                                      String stageLabel,
                                      String message,
                                      Integer totalChunks,
                                      Integer percent,
                                      Boolean success) {
        Map<String, Object> context = materialContext(material);
        context.put("uploadId", uploadId);
        context.put("stageCode", stageCode);
        context.put("stageLabel", stageLabel);
        context.put("message", message);
        context.put("status", Boolean.FALSE.equals(success) ? "FAILED" : "RUNNING");
        context.put("currentStep", Boolean.FALSE.equals(success) ? 1 : 2);
        context.put("totalSteps", 8);
        context.put("currentChunk", totalChunks);
        context.put("totalChunks", totalChunks);
        context.put("percent", percent);
        logService.recordRagProgress(
                "material",
                stageCode,
                "material_chunk_" + sanitizeAction(stageCode),
                message,
                context,
                success
        );
    }

    /**
     * 将资料状态标记为失败并保存失败摘要。
     */
    private void markFailed(LearningMaterial material, String reason) {
        String message = reason == null ? "视频分片后台处理失败" : reason;
        learningMaterialMapper.updateIndexResult(
                material.getId(),
                "FAILED",
                "upload-chunk-error",
                truncate(message, 500),
                0
        );
        material.setStatus("FAILED");
        material.setParser("upload-chunk-error");
        material.setDocumentSummary(truncate(message, 500));
        material.setChunkCount(0);
    }

    /**
     * 构造资料日志上下文。
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

    private String sanitizeFilenameToken(String value) {
        String fallback = value == null || value.isBlank() ? "material" : value;
        return fallback
                .replaceAll("[\\\\/:*?\"<>|]+", "_")
                .replaceAll("\\s+", "_")
                .toLowerCase(Locale.ROOT);
    }

    private String sanitizeAction(String value) {
        return value == null ? "unknown" : value.replaceAll("[^A-Za-z0-9_]+", "_");
    }

    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
