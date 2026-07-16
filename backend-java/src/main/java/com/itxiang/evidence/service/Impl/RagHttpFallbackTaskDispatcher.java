package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.service.command.RagUploadFinalizeCommand;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * Kafka 在接收任务前不可达时，复用原有 HTTP 与本机异步索引链路。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class RagHttpFallbackTaskDispatcher {

    private final RagIndexWorker ragIndexWorker;
    private final RagUploadWorker ragUploadWorker;

    /**
     * 回退已保存文件的后台索引。
     */
    public void publishStoredMaterialIndex(LearningMaterial material, String userId, Boolean highPrecision, String operation) {
        log.warn("RAG Kafka 不可达，切换 HTTP 索引: materialId={}, operation={}", material.getId(), operation);
        runAfterCommit(() -> {
            if ("REINDEX".equals(operation)) {
                ragIndexWorker.reindexStoredMaterial(material.getId(), userId, Boolean.TRUE.equals(highPrecision));
                return;
            }
            ragIndexWorker.indexStoredMaterial(material.getId(), userId, Boolean.TRUE.equals(highPrecision));
        });
    }

    /**
     * 回退手工文本的后台索引，避免 Kafka 不可用时资料长期停留在解析中。
     */
    public void publishTextIndex(LearningMaterial material, String userId, RagIndexTextDTO dto) {
        log.warn("RAG Kafka 不可达，切换 HTTP 文本索引: materialId={}", material.getId());
        runAfterCommit(() -> ragIndexWorker.indexTextMaterial(material.getId(), userId, dto));
    }

    /**
     * 回退长视频分片的本机收尾任务。
     */
    public void publishUploadFinalize(RagUploadFinalizeCommand command) {
        log.warn("RAG Kafka 不可达，切换本机分片收尾: materialId={}, uploadId={}",
                command.materialId(), command.uploadId());
        runAfterCommit(() -> ragUploadWorker.completeChunkedUpload(
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
     * 资料事务提交后再启动异步任务，避免 Worker 读取到尚未提交的资料记录。
     */
    private void runAfterCommit(Runnable task) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()
                || !TransactionSynchronizationManager.isActualTransactionActive()) {
            task.run();
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                task.run();
            }
        });
    }
}
