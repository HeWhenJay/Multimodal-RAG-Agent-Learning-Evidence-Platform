package com.itxiang.evidence.service.Impl;

import com.aliyun.oss.ClientException;
import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.aliyun.oss.OSSException;
import com.aliyun.oss.model.ObjectMetadata;
import com.itxiang.evidence.config.ObjectStorageProperties;
import com.itxiang.evidence.service.ObjectStorageService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class ObjectStorageServiceImpl implements ObjectStorageService {

    private static final DateTimeFormatter DATE_PATH_FORMATTER = DateTimeFormatter.BASIC_ISO_DATE;

    private final ObjectStorageProperties properties;

    /**
     * 根据配置将文件写入本地或阿里 OSS。
     */
    @Override
    public StoredObject store(MultipartFile file, String filename, String userId, String documentType) {
        if (file == null || file.isEmpty()) {
            throw new IllegalArgumentException("上传文件不能为空");
        }
        String provider = properties.getProvider() == null ? "local" : properties.getProvider().trim().toLowerCase(Locale.ROOT);
        if ("oss".equals(provider)) {
            return storeToOss(file, filename, userId, documentType);
        }
        if (!"local".equals(provider)) {
            throw new IllegalArgumentException("不支持的文件存储模式: " + properties.getProvider());
        }
        return storeToLocal(file, filename);
    }

    /**
     * 本地模式保存文件，主要用于开发和测试。
     */
    private StoredObject storeToLocal(MultipartFile file, String filename) {
        String datePath = LocalDate.now().format(DATE_PATH_FORMATTER);
        Path directory = Path.of(properties.getLocalRoot()).resolve(datePath);
        Path target = directory.resolve(UUID.randomUUID() + "-" + sanitizeFilename(filename));
        try {
            Files.createDirectories(directory);
            try (InputStream inputStream = file.getInputStream()) {
                Files.copy(inputStream, target);
            }
            return new StoredObject("local", target.toString(), target.toString(), null);
        } catch (IOException e) {
            throw new IllegalStateException("保存上传文件失败: " + e.getMessage(), e);
        }
    }

    /**
     * OSS 模式上传原始文件，并返回公开 URL 或 oss:// 来源地址。
     */
    private StoredObject storeToOss(MultipartFile file, String filename, String userId, String documentType) {
        ObjectStorageProperties.Oss ossProperties = properties.getOss();
        validateOssProperties(ossProperties);
        String objectKey = buildObjectKey(ossProperties, filename, userId, documentType);
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(file.getSize());
        if (file.getContentType() != null && !file.getContentType().isBlank()) {
            metadata.setContentType(file.getContentType());
        }
        OSS ossClient = new OSSClientBuilder().build(
                ossProperties.getEndpoint(),
                ossProperties.getAccessKeyId(),
                ossProperties.getAccessKeySecret()
        );
        try (InputStream inputStream = file.getInputStream()) {
            ossClient.putObject(ossProperties.getBucket(), objectKey, inputStream, metadata);
        } catch (OSSException | ClientException e) {
            throw new IllegalStateException("上传文件到阿里 OSS 失败: " + e.getMessage(), e);
        } catch (IOException e) {
            throw new IllegalStateException("读取上传文件失败: " + e.getMessage(), e);
        } finally {
            ossClient.shutdown();
        }
        String publicUrl = buildPublicUrl(ossProperties, objectKey);
        String sourcePath = publicUrl == null ? "oss://" + ossProperties.getBucket() + "/" + objectKey : publicUrl;
        log.info("文件已上传到阿里 OSS: bucket={}, objectKey={}", ossProperties.getBucket(), objectKey);
        return new StoredObject("oss", sourcePath, objectKey, publicUrl);
    }

    /**
     * 校验 OSS 必填配置，避免运行时上传到未知位置。
     */
    private void validateOssProperties(ObjectStorageProperties.Oss ossProperties) {
        if (isBlank(ossProperties.getEndpoint())
                || isBlank(ossProperties.getBucket())
                || isBlank(ossProperties.getAccessKeyId())
                || isBlank(ossProperties.getAccessKeySecret())) {
            throw new IllegalStateException("OSS 存储已启用，但 endpoint、bucket、accessKeyId 或 accessKeySecret 未完整配置");
        }
    }

    /**
     * 构造按用户和日期隔离的 OSS object key。
     */
    private String buildObjectKey(ObjectStorageProperties.Oss ossProperties, String filename, String userId, String documentType) {
        String prefix = trimSlashes(blankToDefault(ossProperties.getObjectPrefix(), "learning-evidence"));
        String datePath = LocalDate.now().format(DATE_PATH_FORMATTER);
        String safeUserId = sanitizeFilename(blankToDefault(userId, "anonymous"));
        String safeType = sanitizeFilename(blankToDefault(documentType, "document"));
        String safeFilename = sanitizeFilename(filename);
        return prefix + "/" + safeUserId + "/" + safeType + "/" + datePath + "/" + UUID.randomUUID() + "-" + safeFilename;
    }

    /**
     * 优先使用公开域名，否则按标准 bucket endpoint 生成 URL。
     */
    private String buildPublicUrl(ObjectStorageProperties.Oss ossProperties, String objectKey) {
        if (!isBlank(ossProperties.getPublicBaseUrl())) {
            return trimRightSlash(ossProperties.getPublicBaseUrl()) + "/" + objectKey;
        }
        String endpoint = trimRightSlash(ossProperties.getEndpoint());
        String normalizedEndpoint = endpoint
                .replaceFirst("^https?://", "");
        if (normalizedEndpoint.isBlank()) {
            return null;
        }
        return "https://" + ossProperties.getBucket() + "." + normalizedEndpoint + "/" + objectKey;
    }

    /**
     * 清理文件名中的路径分隔符和空白字符。
     */
    private String sanitizeFilename(String filename) {
        String fallback = filename == null || filename.isBlank() ? "material" : filename;
        return fallback
                .replaceAll("[\\\\/:*?\"<>|]+", "_")
                .replaceAll("\\s+", "_")
                .toLowerCase(Locale.ROOT);
    }

    private String blankToDefault(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private String trimSlashes(String value) {
        return value.replaceAll("^/+", "").replaceAll("/+$", "");
    }

    private String trimRightSlash(String value) {
        return value == null ? "" : value.replaceAll("/+$", "");
    }
}
