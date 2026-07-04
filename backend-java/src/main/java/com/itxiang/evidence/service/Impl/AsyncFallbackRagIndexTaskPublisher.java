package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.service.RagIndexTaskPublisher;
import com.itxiang.evidence.service.command.RagUploadFinalizeCommand;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "evidence.rag.kafka", name = "enabled", havingValue = "false", matchIfMissing = true)
public class AsyncFallbackRagIndexTaskPublisher implements RagIndexTaskPublisher {

    private final RagIndexWorker ragIndexWorker;
    private final RagUploadWorker ragUploadWorker;

    @Override
    public boolean kafkaEnabled() {
        return false;
    }

    /**
     * Kafka 关闭时沿用旧的本机 @Async + HTTP 索引路径。
     */
    @Override
    public void publishStoredMaterialIndex(LearningMaterial material, String userId, Boolean highPrecision, String operation) {
        if ("REINDEX".equals(operation)) {
            ragIndexWorker.reindexStoredMaterial(material.getId(), userId, Boolean.TRUE.equals(highPrecision));
            return;
        }
        ragIndexWorker.indexStoredMaterial(material.getId(), userId, Boolean.TRUE.equals(highPrecision));
    }

    /**
     * 文本资料在 fallback 模式下仍由 RagServiceImpl 同步调用旧 HTTP 接口。
     */
    @Override
    public void publishTextIndex(LearningMaterial material, String userId, RagIndexTextDTO dto) {
        log.debug("Kafka 关闭时文本索引保持同步 HTTP 路径: materialId={}", material.getId());
    }

    /**
     * Kafka 关闭时沿用旧的本机 @Async 分片收尾路径。
     */
    @Override
    public void publishUploadFinalize(RagUploadFinalizeCommand command) {
        ragUploadWorker.completeChunkedUpload(
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
        );
    }
}
