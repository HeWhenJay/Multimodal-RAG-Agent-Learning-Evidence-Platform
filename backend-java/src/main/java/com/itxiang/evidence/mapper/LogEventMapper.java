package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.LogEvent;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface LogEventMapper {

    /**
     * 新增业务事件日志。
     */
    void insert(LogEvent event);

    /**
     * 查询最近业务事件日志。
     */
    List<LogEvent> findRecent(@Param("limit") Integer limit);

    /**
     * 统计指定时间后的业务事件数量。
     */
    Long countSince(@Param("startTime") LocalDateTime startTime);
}
