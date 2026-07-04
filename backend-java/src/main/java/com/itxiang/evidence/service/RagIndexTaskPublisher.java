package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.service.command.RagUploadFinalizeCommand;

public interface RagIndexTaskPublisher {

    /**
     * 当前发布器是否使用 Kafka/Outbox。
     */
    boolean kafkaEnabled();

    /**
     * 发布已保存资料的索引请求。
     */
    void publishStoredMaterialIndex(LearningMaterial material, String userId, Boolean highPrecision, String operation);

    /**
     * 发布文本资料索引请求。
     */
    void publishTextIndex(LearningMaterial material, String userId, RagIndexTextDTO dto);

    /**
     * 发布分片上传收尾请求。
     */
    void publishUploadFinalize(RagUploadFinalizeCommand command);
}
