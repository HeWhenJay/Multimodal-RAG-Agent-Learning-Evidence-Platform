package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.ResumeTemplatePreviewPage;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface ResumeTemplatePreviewPageMapper {

    /**
     * 保存页面预览图片记录。
     */
    void insert(ResumeTemplatePreviewPage page);

    /**
     * 查询模板版本的全部预览页面。
     */
    List<ResumeTemplatePreviewPage> findByTemplateIdAndVersion(@Param("templateId") String templateId,
                                                               @Param("templateVersion") Integer templateVersion);

    /**
     * 查询当前用户可访问的单页预览图片记录。
     */
    ResumeTemplatePreviewPage findByTemplateIdVersionPageAndUserId(@Param("templateId") String templateId,
                                                                   @Param("templateVersion") Integer templateVersion,
                                                                   @Param("pageIndex") Integer pageIndex,
                                                                   @Param("userId") String userId);

    /**
     * 清理指定模板版本的页面缓存。
     */
    void deleteByTemplateIdAndVersion(@Param("templateId") String templateId,
                                      @Param("templateVersion") Integer templateVersion);

    /**
     * 查询当前模板的全部预览页，便于删除时清理对象文件。
     */
    List<ResumeTemplatePreviewPage> findAllByTemplateId(@Param("templateId") String templateId);
}
