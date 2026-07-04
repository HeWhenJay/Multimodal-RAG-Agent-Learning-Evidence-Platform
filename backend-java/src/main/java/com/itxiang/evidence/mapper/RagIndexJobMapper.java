package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.RagIndexJob;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface RagIndexJobMapper {

    /**
     * 新增 RAG 索引任务。
     */
    void insert(RagIndexJob job);

    /**
     * 按任务 ID 查询索引任务。
     */
    RagIndexJob findById(@Param("id") String id);

    /**
     * 保存 Python staging 索引结果。
     */
    int markIndexed(@Param("id") String id,
                    @Param("status") String status,
                    @Param("resultJson") String resultJson,
                    @Param("errorCode") String errorCode,
                    @Param("errorMessage") String errorMessage);

    /**
     * 保存 promote 终态。
     */
    int markFinished(@Param("id") String id,
                     @Param("status") String status,
                     @Param("resultJson") String resultJson,
                     @Param("errorCode") String errorCode,
                     @Param("errorMessage") String errorMessage);

    /**
     * 标记过期结果已忽略。
     */
    int markIgnored(@Param("id") String id,
                    @Param("status") String status,
                    @Param("errorMessage") String errorMessage);
}
