package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.LogError;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface LogErrorMapper {

    void insert(LogError error);

    LogError findByFingerprint(@Param("fingerprint") String fingerprint);

    void increaseOccurrence(@Param("fingerprint") String fingerprint,
                            @Param("lastSeenAt") LocalDateTime lastSeenAt);

    List<LogError> findRecent(@Param("limit") Integer limit);

    Long countSince(@Param("startTime") LocalDateTime startTime);

    Long countOpenSince(@Param("startTime") LocalDateTime startTime);

    Long countBySourceSince(@Param("source") String source,
                            @Param("startTime") LocalDateTime startTime);
}
