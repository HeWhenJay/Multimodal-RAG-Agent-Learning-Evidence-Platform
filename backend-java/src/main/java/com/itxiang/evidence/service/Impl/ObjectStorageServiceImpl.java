package com.itxiang.evidence.service.Impl;

import com.aliyun.oss.ClientException;
import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.aliyun.oss.OSSException;
import com.aliyun.oss.model.OSSObject;
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
     * 读取已保存原始文件，供资料重建索引或高精度补跑使用。
     */
    @Override
    public LoadedObject load(String storageType, String sourcePath, String objectKey, String filename) {
        String provider = storageType == null ? "local" : storageType.trim().toLowerCase(Locale.ROOT);
        if ("oss".equals(provider)) {
            return loadFromOss(sourcePath, objectKey, filename);
        }
        if ("local".equals(provider)) {
            return loadFromLocal(sourcePath, filename);
        }
        throw new IllegalArgumentException("当前资料没有可读取的原始上传文件，无法重建索引");
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
     * 从本地上传目录读取原始文件。
     */
    private LoadedObject loadFromLocal(String sourcePath, String filename) {
        if (isBlank(sourcePath)) {
            throw new IllegalArgumentException("本地资料缺少原始文件路径，无法重建索引");
        }
        Path target = resolveLocalPath(sourcePath);
        if (!Files.exists(target) || !Files.isRegularFile(target)) {
            throw new IllegalStateException("本地原始文件不存在，无法重建索引: " + sourcePath);
        }
        try {
            String contentType = Files.probeContentType(target);
            return new LoadedObject(Files.readAllBytes(target), blankToDefault(filename, target.getFileName().toString()), contentType);
        } catch (IOException e) {
            throw new IllegalStateException("读取本地原始文件失败: " + e.getMessage(), e);
        }
    }

    /**
     * 从阿里 OSS 读取原始文件。
     */
    private LoadedObject loadFromOss(String sourcePath, String objectKey, String filename) {
        ObjectStorageProperties.Oss ossProperties = properties.getOss();
        validateOssProperties(ossProperties);
        String key = blankToDefault(objectKey, parseObjectKeyFromOssUri(sourcePath, ossProperties.getBucket()));
        if (isBlank(key)) {
            throw new IllegalArgumentException("OSS 资料缺少 objectKey，无法重建索引");
        }
        OSS ossClient = new OSSClientBuilder().build(
                ossProperties.getEndpoint(),
                ossProperties.getAccessKeyId(),
                ossProperties.getAccessKeySecret()
        );
        try {
            OSSObject ossObject = ossClient.getObject(ossProperties.getBucket(), key);
            String contentType = ossObject.getObjectMetadata() == null ? null : ossObject.getObjectMetadata().getContentType();
            try (InputStream inputStream = ossObject.getObjectContent()) {
                return new LoadedObject(inputStream.readAllBytes(), blankToDefault(filename, Path.of(key).getFileName().toString()), contentType);
            }
        } catch (OSSException | ClientException e) {
            throw new IllegalStateException("从阿里 OSS 读取原始文件失败: " + e.getMessage(), e);
        } catch (IOException e) {
            throw new IllegalStateException("读取 OSS 文件流失败: " + e.getMessage(), e);
        } finally {
            ossClient.shutdown();
        }
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
     * 仅在配置公开域名时返回可播放 URL，私有桶默认只保留 oss:// 来源地址。
     */
    private String buildPublicUrl(ObjectStorageProperties.Oss ossProperties, String objectKey) {
        if (!isBlank(ossProperties.getPublicBaseUrl())) {
            return trimRightSlash(ossProperties.getPublicBaseUrl()) + "/" + objectKey;
        }
        return null;
    }

    /**
     * 解析并校验本地上传文件路径，避免读取上传目录之外的文件。
     */
    private Path resolveLocalPath(String sourcePath) {
        Path root = Path.of(properties.getLocalRoot()).toAbsolutePath().normalize();
        Path rawPath = Path.of(sourcePath);
        Path target = rawPath.isAbsolute()
                ? rawPath.normalize()
                : Path.of("").toAbsolutePath().resolve(rawPath).normalize();
        if (!target.startsWith(root)) {
            throw new IllegalArgumentException("本地原始文件路径不在上传目录内，拒绝重建索引");
        }
        return target;
    }

    /**
     * 从 oss://bucket/key 形式的来源地址中提取 objectKey。
     */
    private String parseObjectKeyFromOssUri(String sourcePath, String bucket) {
        if (isBlank(sourcePath) || !sourcePath.startsWith("oss://")) {
            return null;
        }
        String prefix = "oss://" + bucket + "/";
        if (!sourcePath.startsWith(prefix)) {
            return null;
        }
        return sourcePath.substring(prefix.length());
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
