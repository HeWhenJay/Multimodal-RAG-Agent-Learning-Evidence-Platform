package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.ResumeTemplatePatchDraft;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface ResumeTemplatePatchDraftMapper {

    /**
     * 新增简历模板补丁草稿。
     */
    void insert(ResumeTemplatePatchDraft draft);

    /**
     * 按草稿 ID、模板 ID 和用户 ID 查询。
     */
    ResumeTemplatePatchDraft findByIdAndTemplateIdAndUserId(@Param("id") String id,
                                                            @Param("templateId") String templateId,
                                                            @Param("userId") String userId);

    /**
     * 更新补丁草稿状态和校验结果。
     */
    void updateValidation(@Param("id") String id,
                          @Param("status") String status,
                          @Param("patchesJson") String patchesJson,
                          @Param("validationErrorsJson") String validationErrorsJson);

    /**
     * 更新补丁草稿状态。
     */
    void updateStatus(@Param("id") String id, @Param("status") String status);
}
