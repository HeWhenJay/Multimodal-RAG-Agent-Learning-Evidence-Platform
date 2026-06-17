package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

@Slf4j
@Service
@RequiredArgsConstructor
public class RagIndexWorker {

    private final LearningMaterialMapper learningMaterialMapper;
    private final PythonRagClient pythonRagClient;
    private final LogService logService;
    private final ObjectStorageService objectStorageService;
    private final TransactionTemplate transactionTemplate;

    /**
     * 后台索引已保存的上传资料，避免长文件阻塞上传请求。
     */
    @Async
    public void indexStoredMaterial(Long materialId, String userId, Boolean highPrecision) {
        executeIndex(materialId, userId, highPrecision, "index", "material_index_async_result", "学习资料后台索引完成");
    }

    /**
     * 后台重建已保存资料索引。
     */
    @Async
    public void reindexStoredMaterial(Long materialId, String userId, Boolean highPrecision) {
        executeIndex(materialId, userId, highPrecision, "reindex", "material_reindex_async_result", "学习资料后台重建索引完成");
    }

    /**
     * 根据资料类型选择 Python 索引入口，并回写最终状态。
     */
    private void executeIndex(Long materialId,
                              String userId,
                              Boolean highPrecision,
                              String stage,
                              String successAction,
                              String successMessage) {
        LearningMaterial material = learningMaterialMapper.findByIdAndUserId(materialId, userId);
        if (material == null) {
            log.warn("后台索引跳过，资料不存在或用户不匹配: materialId={}, userId={}", materialId, userId);
            return;
        }

        try {
            PythonRagClient.IndexResult result;
            if (isVideoDocumentType(material.getDocumentType())) {
                result = pythonRagClient.indexVideoSource(
                        material.getId(),
                        userId,
                        material,
                        material.getOriginalFilename(),
                        null,
                        Boolean.TRUE.equals(highPrecision)
                );
            } else {
                ObjectStorageService.LoadedObject loadedObject = objectStorageService.load(
                        material.getStorageType(),
                        material.getOriginalFilePath(),
                        material.getObjectKey(),
                        material.getOriginalFilename()
                );
                result = pythonRagClient.indexFileBytes(
                        material.getId(),
                        userId,
                        material,
                        loadedObject.content(),
                        loadedObject.filename(),
                        loadedObject.contentType(),
                        Boolean.TRUE.equals(highPrecision)
                );
            }
            transactionTemplate.executeWithoutResult(status -> {
                recordIndexResultAnomalies(material, result);
                applyIndexResult(material, result);
                logService.recordRagEvent(
                        "material",
                        stage,
                        successAction,
                        successMessage,
                        indexResultContext(material, result)
                );
            });
        } catch (Exception e) {
            log.warn("后台资料索引失败: materialId={}, reason={}", material.getId(), e.getMessage());
            recordFailureProgress(material, e);
            logService.recordRagError(
                    "material",
                    stage,
                    "reindex".equals(stage) ? "material_reindex_async_failed" : "material_index_async_failed",
                    resolveRagErrorCode(e),
                    "后台学习资料索引失败",
                    e,
                    errorContext(material, e)
            );
            transactionTemplate.executeWithoutResult(status -> markFailed(material, e.getMessage()));
        }
    }

    /**
     * 失败时补写终态进度，避免前端刷新后仍显示旧的运行中阶段。
     */
    private void recordFailureProgress(LearningMaterial material, Exception e) {
        String reason = e.getMessage() == null ? "Python RAG 解析失败" : e.getMessage();
        Map<String, Object> context = materialContext(material);
        context.putAll(pythonExceptionContext(e));
        context.put("stageCode", "index.failed");
        context.put("stageLabel", "索引失败");
        context.put("message", "索引失败：" + truncate(reason, 180));
        context.put("status", "FAILED");
        context.put("currentStep", 8);
        context.put("totalSteps", 8);
        context.put("currentChunk", 0);
        context.put("totalChunks", 0);
        context.put("percent", 0);
        context.put("detail", truncate(reason, 500));
        logService.recordRagProgress(
                "material",
                "index.failed",
                "rag_progress_index_failed",
                "索引失败：" + truncate(reason, 180),
                context,
                false
        );
    }

    /**
     * 将 Python 索引结果回写到资料记录。
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
     * 记录 Python 索引响应中的异常状态和阶段告警。
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
     * 将资料状态标记为失败。
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
     * 判断资料类型是否为视频。
     */
    private boolean isVideoDocumentType(String documentType) {
        if (documentType == null) {
            return false;
        }
        return List.of("mp4", "mov", "m4v", "webm", "mkv", "avi").contains(documentType.toLowerCase(Locale.ROOT));
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
        context.put("progressEventCount", result.progressEvents() == null ? 0 : result.progressEvents().size());
        if (result.parseQualityMessages() != null && !result.parseQualityMessages().isEmpty()) {
            context.put("errorLocation", result.parseQualityMessages());
        }
        return context;
    }

    /**
     * 构造异常日志上下文。
     */
    private Map<String, Object> errorContext(LearningMaterial material, Exception e) {
        Map<String, Object> context = materialContext(material);
        context.putAll(pythonExceptionContext(e));
        return context;
    }

    /**
     * 提取 Python 调用异常信息。
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
