package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.LearningMaterial;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface LearningMaterialMapper {

    /**
     * 新增学习资料记录。
     */
    void insert(LearningMaterial material);

    /**
     * 回写 Python RAG 索引结果。
     */
    void updateIndexResult(@Param("id") Long id,
                           @Param("status") String status,
                           @Param("parser") String parser,
                           @Param("documentSummary") String documentSummary,
                           @Param("chunkCount") Integer chunkCount);

    /**
     * 更新学习资料解析状态。
     */
    void updateStatus(@Param("id") Long id, @Param("status") String status);

    /**
     * 按 ID 查询学习资料。
     */
    LearningMaterial findByIdAndUserId(@Param("id") Long id, @Param("userId") String userId);

    /**
     * 查询最近学习资料列表。
     */
    List<LearningMaterial> findRecentByUserId(@Param("userId") String userId, @Param("limit") Integer limit);

    /**
     * 统计学习资料总数。
     */
    Long countAllByUserId(@Param("userId") String userId);

    /**
     * 统计指定时间后新增的学习资料数量。
     */
    Long countSinceByUserId(@Param("userId") String userId, @Param("startTime") LocalDateTime startTime);

    /**
     * 统计全部资料切块数量。
     */
    Integer sumChunkCountByUserId(@Param("userId") String userId);
}
