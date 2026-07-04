package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagIndexJob;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.RagIndexJobMapper;
import com.itxiang.evidence.service.ObjectStorageService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.springframework.http.HttpStatus.FORBIDDEN;
import static org.springframework.http.HttpStatus.GONE;
import static org.springframework.http.HttpStatus.NOT_FOUND;
import static org.springframework.http.HttpStatus.UNAUTHORIZED;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/internal/rag")
public class RagInternalController {

    private final RagKafkaProperties properties;
    private final LearningMaterialMapper learningMaterialMapper;
    private final RagIndexJobMapper ragIndexJobMapper;
    private final ObjectStorageService objectStorageService;

    /**
     * 为 Python Kafka worker 流式读取资料原始内容。
     */
    @GetMapping("/materials/{materialId}/source")
    public ResponseEntity<StreamingResponseBody> materialSource(@PathVariable Long materialId,
                                                                @RequestParam String jobId,
                                                                @RequestParam Integer requestVersion,
                                                                @RequestHeader(value = "X-RAG-Internal-Token", required = false) String token) {
        validateInternalToken(token);
        LearningMaterial material = learningMaterialMapper.findById(materialId);
        if (material == null) {
            throw new ResponseStatusException(NOT_FOUND, "资料不存在");
        }
        RagIndexJob job = ragIndexJobMapper.findById(jobId);
        validateActiveJob(material, job, requestVersion);
        ObjectStorageService.OpenedObject opened = objectStorageService.open(
                material.getStorageType(),
                material.getOriginalFilePath(),
                material.getObjectKey(),
                material.getOriginalFilename()
        );
        MediaType mediaType = parseMediaType(opened.contentType());
        ContentDisposition disposition = ContentDisposition.attachment()
                .filename(defaultText(opened.filename(), material.getOriginalFilename()), StandardCharsets.UTF_8)
                .build();
        StreamingResponseBody body = outputStream -> {
            try (opened) {
                opened.inputStream().transferTo(outputStream);
            }
        };
        ResponseEntity.BodyBuilder builder = ResponseEntity.ok()
                .contentType(mediaType)
                .header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString());
        if (opened.contentLength() != null && opened.contentLength() >= 0) {
            builder.contentLength(opened.contentLength());
        }
        return builder.body(body);
    }

    /**
     * 供 Python Promote Worker 在覆盖 canonical 前二次校验 active job。
     */
    @GetMapping("/materials/{materialId}/index-jobs/{jobId}/active")
    public Result<Map<String, Object>> activeIndexJob(@PathVariable Long materialId,
                                                      @PathVariable String jobId,
                                                      @RequestParam Integer requestVersion,
                                                      @RequestHeader(value = "X-RAG-Internal-Token", required = false) String token) {
        validateInternalToken(token);
        LearningMaterial material = learningMaterialMapper.findById(materialId);
        if (material == null) {
            throw new ResponseStatusException(NOT_FOUND, "资料不存在");
        }
        RagIndexJob job = ragIndexJobMapper.findById(jobId);
        validateActiveJob(material, job, requestVersion);
        return Result.success(Map.of(
                "active", true,
                "materialId", materialId,
                "jobId", jobId,
                "requestVersion", requestVersion
        ));
    }

    /**
     * 校验内部共享 token，避免外部绕过 Java 资料权限边界。
     */
    private void validateInternalToken(String token) {
        String expected = resolveInternalToken();
        if (expected == null || expected.isBlank()) {
            throw new ResponseStatusException(UNAUTHORIZED, "内部 RAG token 未配置");
        }
        if (token == null || !expected.equals(token.trim())) {
            throw new ResponseStatusException(UNAUTHORIZED, "内部 RAG token 无效");
        }
    }

    /**
     * 校验 job 与资料当前 active job 和 requestVersion 一致。
     */
    private void validateActiveJob(LearningMaterial material, RagIndexJob job, Integer requestVersion) {
        if (job == null || !material.getId().equals(job.getMaterialId())) {
            throw new ResponseStatusException(NOT_FOUND, "索引任务不存在");
        }
        if (!job.getId().equals(material.getActiveIndexJobId())) {
            throw new ResponseStatusException(FORBIDDEN, "索引任务不是当前资料的 active job");
        }
        if (!requestVersion.equals(material.getIndexRequestVersion()) || !requestVersion.equals(job.getRequestVersion())) {
            throw new ResponseStatusException(GONE, "索引任务版本已过期");
        }
        if (isTerminalJob(job.getStatus())) {
            throw new ResponseStatusException(GONE, "索引任务已结束");
        }
    }

    private boolean isTerminalJob(String status) {
        return "SUCCEEDED".equals(status) || "FAILED".equals(status) || "DLQ".equals(status) || "CANCELLED".equals(status) || "STALE_IGNORED".equals(status);
    }

    private String resolveInternalToken() {
        if (properties.getInternalToken() != null && !properties.getInternalToken().isBlank()) {
            return properties.getInternalToken().trim();
        }
        String tokenFile = properties.getInternalTokenFile();
        if (tokenFile == null || tokenFile.isBlank()) {
            return "";
        }
        try {
            return Files.readString(Path.of(tokenFile), StandardCharsets.UTF_8).trim();
        } catch (Exception e) {
            return "";
        }
    }

    private MediaType parseMediaType(String value) {
        if (value == null || value.isBlank()) {
            return MediaType.APPLICATION_OCTET_STREAM;
        }
        try {
            return MediaType.parseMediaType(value);
        } catch (Exception e) {
            return MediaType.APPLICATION_OCTET_STREAM;
        }
    }

    private String defaultText(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }
}
