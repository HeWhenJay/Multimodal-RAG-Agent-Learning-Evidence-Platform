package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.ResumeTemplateRegionAnnotation;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface ResumeTemplateRegionAnnotationMapper {

    /**
     * 新增区域标注。
     */
    void insert(ResumeTemplateRegionAnnotation annotation);

    /**
     * 更新区域标注的用户约束。
     */
    void update(ResumeTemplateRegionAnnotation annotation);

    /**
     * 查询模板版本的全部区域标注。
     */
    List<ResumeTemplateRegionAnnotation> findByTemplateIdAndVersion(@Param("templateId") String templateId,
                                                                    @Param("templateVersion") Integer templateVersion);

    /**
     * 查询当前用户确认可编辑的绑定字段标注。
     */
    List<ResumeTemplateRegionAnnotation> findEditableBoundActive(@Param("templateId") String templateId,
                                                                 @Param("templateVersion") Integer templateVersion,
                                                                 @Param("userId") String userId);

    /**
     * 查询单条标注并校验用户归属。
     */
    ResumeTemplateRegionAnnotation findByIdAndUserId(@Param("id") String id, @Param("userId") String userId);

    /**
     * 清理系统自动标注，刷新预览时重新写入。
     */
    void deleteAutoByTemplateIdAndVersion(@Param("templateId") String templateId,
                                          @Param("templateVersion") Integer templateVersion);

    /**
     * 获取模板版本当前最大标注修订号。
     */
    Integer maxRevision(@Param("templateId") String templateId,
                        @Param("templateVersion") Integer templateVersion);
}
