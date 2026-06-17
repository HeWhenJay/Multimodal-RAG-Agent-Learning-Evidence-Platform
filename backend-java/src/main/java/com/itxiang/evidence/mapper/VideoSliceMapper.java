package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.VideoSlice;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface VideoSliceMapper {

    /**
     * 查询最近视频切片。
     */
    List<VideoSlice> findRecent(@Param("limit") Integer limit);

    /**
     * 统计视频切片总数。
     */
    Long countAll();

    /**
     * 统计指定时间后新增的视频切片数量。
     */
    Long countSince(@Param("startTime") LocalDateTime startTime);
}
