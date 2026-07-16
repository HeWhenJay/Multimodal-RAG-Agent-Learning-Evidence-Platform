package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.RagOutboxEvent;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.OffsetDateTime;
import java.util.List;

@Mapper
public interface RagOutboxEventMapper {

    /**
     * 新增待发布 Outbox 事件。
     */
    int insert(RagOutboxEvent event);

    /**
     * 多实例安全查询到期待发布事件。
     */
    List<RagOutboxEvent> findDueForPublish(@Param("now") OffsetDateTime now,
                                           @Param("limit") Integer limit);

    /**
     * 抢占待发布事件并设置租约。
     */
    int lease(@Param("id") Long id,
              @Param("lockedBy") String lockedBy,
              @Param("leaseUntil") OffsetDateTime leaseUntil);

    /**
     * 标记事件已成功发布。
     */
    int markPublished(@Param("id") Long id);

    /**
     * 标记发布失败并设置下次重试时间。
     */
    int markFailed(@Param("id") Long id,
                   @Param("errorMessage") String errorMessage,
                   @Param("nextAttemptAt") OffsetDateTime nextAttemptAt);
}
