package com.itxiang.evidence.service;

import org.springframework.web.multipart.MultipartFile;

public interface ObjectStorageService {

    /**
     * 保存上传文件并返回可记录到资料表的来源路径。
     */
    StoredObject store(MultipartFile file, String filename, String userId, String documentType);

    /**
     * 读取已保存的原始文件，用于重新解析和补跑索引。
     */
    LoadedObject load(String storageType, String sourcePath, String objectKey, String filename);

    /**
     * 上传后的对象信息。
     */
    record StoredObject(
            String storageType,
            String sourcePath,
            String objectKey,
            String publicUrl
    ) {
    }

    /**
     * 从本地或 OSS 读取出的原始文件内容。
     */
    record LoadedObject(
            byte[] content,
            String filename,
            String contentType
    ) {
    }
}
