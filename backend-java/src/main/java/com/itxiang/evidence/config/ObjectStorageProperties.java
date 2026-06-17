package com.itxiang.evidence.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Data
@ConfigurationProperties(prefix = "evidence.storage")
public class ObjectStorageProperties {

    /**
     * 上传存储模式：local 或 oss。
     */
    private String provider = "local";

    /**
     * 本地开发和测试时的上传目录。
     */
    private String localRoot = "uploads/rag";

    private Oss oss = new Oss();

    @Data
    public static class Oss {

        /**
         * 阿里 OSS Endpoint，例如 https://oss-cn-hangzhou.aliyuncs.com。
         */
        private String endpoint;

        /**
         * 阿里 OSS Bucket 名称。
         */
        private String bucket;

        /**
         * 阿里云 AccessKey ID。
         */
        private String accessKeyId;

        /**
         * 阿里云 AccessKey Secret。
         */
        private String accessKeySecret;

        /**
         * OSS 对象前缀，便于按项目隔离上传文件。
         */
        private String objectPrefix = "learning-evidence";

        /**
         * 可选公开域名或 CDN 域名，用于 evidence 跳转和视频播放。
         */
        private String publicBaseUrl;
    }
}
