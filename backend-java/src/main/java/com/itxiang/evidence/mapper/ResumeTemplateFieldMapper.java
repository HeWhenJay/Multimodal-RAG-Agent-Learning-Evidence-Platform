package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.ResumeTemplateField;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface ResumeTemplateFieldMapper {

    /**
     * 新增简历模板字段绑定。
     */
    void insert(ResumeTemplateField field);

    /**
     * 删除指定模板版本的字段绑定，便于解析失败重试后重写。
     */
    void deleteByTemplateIdAndVersion(@Param("templateId") String templateId, @Param("templateVersion") Integer templateVersion);

    /**
     * 查询指定模板版本的字段绑定。
     */
    List<ResumeTemplateField> findByTemplateIdAndVersion(@Param("templateId") String templateId,
                                                         @Param("templateVersion") Integer templateVersion);
}
