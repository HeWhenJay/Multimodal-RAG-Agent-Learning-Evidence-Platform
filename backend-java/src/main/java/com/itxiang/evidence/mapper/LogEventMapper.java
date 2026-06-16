package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.LogEvent;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface LogEventMapper {

    void insert(LogEvent event);

    List<LogEvent> findRecent(@Param("limit") Integer limit);

    Long countSince(@Param("startTime") LocalDateTime startTime);
}
