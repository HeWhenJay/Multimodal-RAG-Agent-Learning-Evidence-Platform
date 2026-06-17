package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.LogError;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface LogErrorMapper {

    /**
     * 新增错误日志。
     */
    void insert(LogError error);

    /**
     * 按错误指纹查询已存在的错误。
     */
    LogError findByFingerprint(@Param("fingerprint") String fingerprint);

    /**
     * 累加同类错误出现次数。
     */
    void increaseOccurrence(@Param("fingerprint") String fingerprint,
                            @Param("lastSeenAt") LocalDateTime lastSeenAt);

    /**
     * 查询最近错误日志。
     */
    List<LogError> findRecent(@Param("limit") Integer limit);

    /**
     * 统计指定时间后的错误数量。
     */
    Long countSince(@Param("startTime") LocalDateTime startTime);

    /**
     * 统计指定时间后仍处于打开状态的错误数量。
     */
    Long countOpenSince(@Param("startTime") LocalDateTime startTime);

    /**
     * 按来源统计指定时间后的错误数量。
     */
    Long countBySourceSince(@Param("source") String source,
                            @Param("startTime") LocalDateTime startTime);
}
