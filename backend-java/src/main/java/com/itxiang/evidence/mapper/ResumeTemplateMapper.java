package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.ResumeTemplate;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface ResumeTemplateMapper {

    /**
     * 新增简历模板主记录。
     */
    void insert(ResumeTemplate template);

    /**
     * 按模板 ID 和用户 ID 查询模板。
     */
    ResumeTemplate findByIdAndUserId(@Param("id") String id, @Param("userId") String userId);

    /**
     * 更新模板解析结果。
     */
    void updateParseResult(@Param("id") String id,
                           @Param("status") String status,
                           @Param("version") Integer version,
                           @Param("layoutFingerprintJson") String layoutFingerprintJson,
                           @Param("unsupportedRegionsJson") String unsupportedRegionsJson);

    /**
     * 更新模板导出后的当前版本文件信息。
     */
    void updateExportedVersion(@Param("id") String id,
                               @Param("version") Integer version,
                               @Param("status") String status,
                               @Param("currentFilename") String currentFilename,
                               @Param("currentFilePath") String currentFilePath,
                               @Param("currentStorageType") String currentStorageType,
                               @Param("currentObjectKey") String currentObjectKey,
                               @Param("currentPublicUrl") String currentPublicUrl,
                               @Param("layoutFingerprintJson") String layoutFingerprintJson);
}
