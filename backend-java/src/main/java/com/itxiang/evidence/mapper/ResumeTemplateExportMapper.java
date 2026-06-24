package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.ResumeTemplateExport;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

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

    /**
     * 查询当前模板的全部导出记录，便于删除时清理派生文件。
     */
    List<ResumeTemplateExport> findAllByTemplateId(@Param("templateId") String templateId,
                                                   @Param("userId") String userId);
}
