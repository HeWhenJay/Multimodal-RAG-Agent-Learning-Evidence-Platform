package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.RagConsumedEvent;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface RagConsumedEventMapper {

    /**
     * 幂等记录已消费消息，重复消息返回 0。
     */
    int insertIgnore(RagConsumedEvent event);
}
