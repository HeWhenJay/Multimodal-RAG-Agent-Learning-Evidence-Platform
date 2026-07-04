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
     * 按 Python 进度回调同步学习资料索引状态。
     */
    void updateProgressStatus(@Param("id") Long id,
                              @Param("status") String status,
                              @Param("parser") String parser,
                              @Param("chunkCount") Integer chunkCount);

    /**
     * 更新学习资料解析状态。
     */
    void updateStatus(@Param("id") Long id, @Param("status") String status);

    /**
     * 设置当前生效索引任务和请求版本。
     */
    void updateActiveIndexJob(@Param("id") Long id,
                              @Param("activeIndexJobId") String activeIndexJobId,
                              @Param("indexRequestVersion") Integer indexRequestVersion,
                              @Param("status") String status);

    /**
     * 清理当前生效索引任务。
     */
    void clearActiveIndexJob(@Param("id") Long id,
                             @Param("activeIndexJobId") String activeIndexJobId);

    /**
     * 回写原始文件对象存储位置。
     */
    void updateStorageInfo(@Param("id") Long id,
                           @Param("originalFilePath") String originalFilePath,
                           @Param("storageType") String storageType,
                           @Param("objectKey") String objectKey,
                           @Param("publicUrl") String publicUrl);

    /**
     * 按 ID 查询学习资料。
     */
    LearningMaterial findByIdAndUserId(@Param("id") Long id, @Param("userId") String userId);

    /**
     * 内部任务按 ID 查询资料，不暴露给用户接口。
     */
    LearningMaterial findById(@Param("id") Long id);

    /**
     * 查询最近学习资料列表。
     */
    List<LearningMaterial> findRecentByUserId(@Param("userId") String userId, @Param("limit") Integer limit);

    /**
     * 查询指定时间范围内的最近学习资料列表。
     */
    List<LearningMaterial> findRecentByUserIdBetween(@Param("userId") String userId,
                                                     @Param("startTime") LocalDateTime startTime,
                                                     @Param("endTime") LocalDateTime endTime,
                                                     @Param("limit") Integer limit);

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
