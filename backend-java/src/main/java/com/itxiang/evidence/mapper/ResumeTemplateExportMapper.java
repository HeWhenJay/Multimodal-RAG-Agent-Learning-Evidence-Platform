package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.ResumeTemplateExport;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface ResumeTemplateExportMapper {

    /**
     * 新增简历模板导出记录。
     */
    void insert(ResumeTemplateExport export);

    /**
     * 按幂等键查询导出记录。
     */
    ResumeTemplateExport findByIdempotencyKey(@Param("templateId") String templateId,
                                              @Param("userId") String userId,
                                              @Param("idempotencyKey") String idempotencyKey);
}
