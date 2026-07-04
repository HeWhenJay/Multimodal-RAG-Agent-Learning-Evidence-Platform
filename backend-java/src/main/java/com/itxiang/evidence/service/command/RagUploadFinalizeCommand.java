package com.itxiang.evidence.service.command;

import java.nio.file.Path;

/**
 * 分片上传收尾任务参数，供本地异步和 Kafka 路径共用。
 */
public record RagUploadFinalizeCommand(
        Long materialId,
        String userId,
        Path directory,
        Path chunkRoot,
        String uploadId,
        String filename,
        String contentType,
        Integer totalChunks,
        Long totalSize,
        Boolean highPrecision
) {
}
