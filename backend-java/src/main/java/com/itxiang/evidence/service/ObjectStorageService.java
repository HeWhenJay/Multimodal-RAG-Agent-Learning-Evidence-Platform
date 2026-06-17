package com.itxiang.evidence.service;

import org.springframework.web.multipart.MultipartFile;

public interface ObjectStorageService {

    /**
     * 保存上传文件并返回可记录到资料表的来源路径。
     */
    StoredObject store(MultipartFile file, String filename, String userId, String documentType);

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
}
