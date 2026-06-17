package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.ResumeEvidenceAlignment;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface ResumeEvidenceAlignmentMapper {

    /**
     * 查询最近简历证据对齐记录。
     */
    List<ResumeEvidenceAlignment> findRecentByUserId(@Param("userId") String userId, @Param("limit") Integer limit);

    /**
     * 删除用户最近一次 JD 分析生成的旧对齐记录。
     */
    void deleteByUserId(@Param("userId") String userId);

    /**
     * 新增简历证据对齐记录。
     */
    void insert(ResumeEvidenceAlignment alignment);
}
