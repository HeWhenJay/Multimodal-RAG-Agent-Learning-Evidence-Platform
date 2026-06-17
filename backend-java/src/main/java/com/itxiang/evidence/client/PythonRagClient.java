package com.itxiang.evidence.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.PythonRagProperties;
import com.itxiang.evidence.dto.JdAnalysisRequestDTO;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.vo.RagProgressVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagQueryVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.http.client.SimpleClientHttpRequestFactory;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Slf4j
@Component
public class PythonRagClient {

    private final PythonRagProperties properties;
    private final ObjectMapper objectMapper;
    private final RestClient restClient;

    /**
     * 初始化 Python RAG HTTP 客户端并设置索引超时。
     */
    public PythonRagClient(PythonRagProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(5000);
        requestFactory.setReadTimeout(properties.getIndexTimeoutSeconds() * 1000);
        this.restClient = RestClient.builder()
                .requestFactory(requestFactory)
                .build();
    }

    /**
     * 调用 Python 文本索引接口。
     */
    public IndexResult indexText(Long materialId, String userId, RagIndexTextDTO dto) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("documentId", "material-" + materialId);
        payload.put("title", dto.getTitle());
        payload.put("documentType", dto.getDocumentType());
        payload.put("source", dto.getSource());
        payload.put("userId", userId);
        payload.put("visibilityScope", dto.getVisibilityScope());
        payload.put("content", dto.getContent());
        payload.put("parser", "java-manual-text");

        JsonNode root = postJson("/internal/rag/documents/index-text", payload);
        return readIndexResult(root);
    }

    /**
     * 调用 Python 文件索引接口。
     */
    public IndexResult indexFile(Long materialId,
                                 String userId,
                                 LearningMaterial material,
                                 MultipartFile file,
                                 Boolean highPrecision) {
        try {
            return indexFileBytes(
                    materialId,
                    userId,
                    material,
                    file.getBytes(),
                    material.getTitle(),
                    file.getContentType(),
                    highPrecision
            );
        } catch (PythonRagClientException e) {
            throw e;
        } catch (Exception e) {
            throw pythonException("index-file", "/internal/rag/documents/index-file", e);
        }
    }

    /**
     * 调用 Python 文件索引接口，使用已保存的原始文件字节重建索引。
     */
    public IndexResult indexFileBytes(Long materialId,
                                      String userId,
                                      LearningMaterial material,
                                      byte[] content,
                                      String filename,
                                      String contentType,
                                      Boolean highPrecision) {
        try {
            MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
            body.add("document_id", "material-" + materialId);
            body.add("title", material.getTitle());
            body.add("document_type", material.getDocumentType());
            body.add("source", material.getSource());
            body.add("user_id", userId);
            body.add("visibility_scope", "private");
            body.add("source_path", material.getOriginalFilePath());
            body.add("high_precision", Boolean.TRUE.equals(highPrecision));
            body.add("file", new NamedByteArrayResource(content, filename == null ? material.getTitle() : filename));

            byte[] response = restClient.post()
                    .uri(resolve("/internal/rag/documents/index-file"))
                    .contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(body)
                    .retrieve()
                    .body(byte[].class);
            return readIndexResult(readJsonResponse("index-file", "/internal/rag/documents/index-file", response));
        } catch (RestClientResponseException e) {
            throw pythonException("index-file", "/internal/rag/documents/index-file", e);
        } catch (PythonRagClientException e) {
            throw e;
        } catch (Exception e) {
            throw pythonException("index-file", "/internal/rag/documents/index-file", e);
        }
    }

    /**
     * 调用 Python 视频源索引接口，避免长视频再次通过 multipart 转发。
     */
    public IndexResult indexVideoSource(Long materialId,
                                        String userId,
                                        LearningMaterial material,
                                        String filename,
                                        String contentType,
                                        Boolean highPrecision) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("documentId", "material-" + materialId);
        payload.put("title", material.getTitle());
        payload.put("documentType", material.getDocumentType());
        payload.put("source", material.getSource());
        payload.put("userId", userId);
        payload.put("visibilityScope", "private");
        payload.put("sourcePath", material.getOriginalFilePath());
        payload.put("filename", filename == null ? material.getTitle() : filename);
        payload.put("contentType", contentType);
        payload.put("highPrecision", Boolean.TRUE.equals(highPrecision));

        JsonNode root = postJson("/internal/rag/documents/index-video-source", payload);
        return readIndexResult(root);
    }

    /**
     * 调用 Python RAG 查询接口。
     */
    public RagQueryVO query(RagQueryDTO dto) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("question", dto.getQuestion());
        payload.put("topK", dto.getTopK() == null ? 5 : dto.getTopK());
        payload.put("metadataFilter", dto.getMetadataFilter());

        JsonNode root = postJson("/internal/rag/query", payload);
        List<RagEvidenceVO> evidences = new ArrayList<>();
        JsonNode evidenceNodes = root.get("evidences");
        if (evidenceNodes != null && evidenceNodes.isArray()) {
            for (JsonNode item : evidenceNodes) {
                evidences.add(readEvidence(item));
            }
        }
        return RagQueryVO.builder()
                .answer(text(root, "answer"))
                .expandedQueries(readTextArray(root.get("expandedQueries")))
                .evidences(evidences)
                .diagnostics(readObjectMap(root.get("diagnostics")))
                .progressEvents(readProgressVOs(root.get("progressEvents")))
                .build();
    }

    /**
     * 调用 Python JD 分析接口，基于当前用户知识库生成岗位匹配结果。
     */
    public JdAnalysisResult analyzeJd(String userId, JdAnalysisRequestDTO dto) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("userId", userId);
        payload.put("jobDescription", dto.getJobDescription());
        payload.put("resumeText", dto.getResumeText());
        payload.put("topK", 3);

        JsonNode root = postJson("/internal/rag/jd-analysis", payload);
        return new JdAnalysisResult(
                text(root, "jobDescription"),
                root.path("matchScore").asInt(0),
                root.path("masteredPercent").asInt(0),
                root.path("partialPercent").asInt(0),
                root.path("gapPercent").asInt(0),
                readSkillResults(root.get("skills")),
                readPlanResults(root.get("learningPlan")),
                readAlignmentResults(root.get("resumeAlignments"))
        );
    }

    /**
     * 查询指定文档的 evidence 列表。
     */
    public List<RagEvidenceVO> listDocumentEvidences(String documentId, Integer limit) {
        try {
            byte[] response = restClient.get()
                    .uri(resolve("/internal/rag/documents/" + documentId + "/evidences?limit=" + limit))
                    .retrieve()
                    .body(byte[].class);
            JsonNode root = readJsonResponse("list-evidences", "/internal/rag/documents/{document_id}/evidences", response);
            List<RagEvidenceVO> evidences = new ArrayList<>();
            JsonNode evidenceNodes = root.get("evidences");
            if (evidenceNodes != null && evidenceNodes.isArray()) {
                for (JsonNode item : evidenceNodes) {
                    evidences.add(readEvidence(item));
                }
            }
            return evidences;
        } catch (RestClientResponseException e) {
            throw pythonException("list-evidences", "/internal/rag/documents/{document_id}/evidences", e);
        } catch (PythonRagClientException e) {
            throw e;
        } catch (Exception e) {
            throw pythonException("list-evidences", "/internal/rag/documents/{document_id}/evidences", e);
        }
    }

    /**
     * 安全获取 Python RAG 概览；失败时返回空概览。
     */
    public PythonOverview fetchOverviewSafely() {
        try {
            byte[] response = restClient.get()
                    .uri(resolve("/internal/rag/overview"))
                    .retrieve()
                    .body(byte[].class);
            JsonNode root = readJsonResponse("overview", "/internal/rag/overview", response);
            return new PythonOverview(
                    root.path("documentCount").asInt(0),
                    root.path("chunkCount").asInt(0),
                    root.path("evidenceCount").asInt(0),
                    text(root, "lastIndexedTitle")
            );
        } catch (Exception e) {
            log.debug("Python RAG 概览暂不可用: {}", e.getMessage());
            return new PythonOverview(0, 0, 0, null);
        }
    }

    /**
     * 发送 JSON POST 请求并解析响应。
     */
    private JsonNode postJson(String path, Map<String, Object> payload) {
        try {
            String requestBody = objectMapper.writeValueAsString(payload);
            byte[] response = restClient.post()
                    .uri(resolve(path))
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(requestBody)
                    .retrieve()
                    .body(byte[].class);
            return readJsonResponse("post-json", path, response);
        } catch (RestClientResponseException e) {
            throw pythonException("post-json", path, e);
        } catch (PythonRagClientException e) {
            throw e;
        } catch (Exception e) {
            throw pythonException("post-json", path, e);
        }
    }

    /**
     * 按 UTF-8 读取 Python 响应，兼容 FastAPI 返回 application/octet-stream 的 JSON。
     */
    private JsonNode readJsonResponse(String operation, String endpoint, byte[] body) {
        String response = body == null ? "" : new String(body, StandardCharsets.UTF_8);
        try {
            return objectMapper.readTree(response);
        } catch (Exception e) {
            throw new PythonRagClientException(
                    operation,
                    endpoint,
                    null,
                    truncate(response, 500),
                    "Python RAG 响应不是合法 JSON: " + e.getMessage(),
                    e
            );
        }
    }

    /**
     * 拼接 Python 服务完整接口地址。
     */
    private URI resolve(String path) {
        return URI.create(properties.getPythonBaseUrl().replaceAll("/$", "") + path);
    }

    /**
     * 读取并校验 Python 索引响应结构。
     */
    private IndexResult readIndexResult(JsonNode root) {
        if (root == null || !root.hasNonNull("documentId") || !root.hasNonNull("status")) {
            throw new PythonRagClientException(
                    "read-index-result",
                    "python-response",
                    null,
                    null,
                    "Python RAG 响应结构不符合预期",
                    null
            );
        }
        return new IndexResult(
                text(root, "documentId"),
                text(root, "title"),
                text(root, "status"),
                text(root, "parser"),
                text(root, "documentSummary"),
                root.path("chunkCount").asInt(0),
                readParseQualityMessages(root),
                readProgressEvents(root.get("progressEvents"))
        );
    }

    /**
     * 读取 Python 解析质量中的阶段告警，供 Java 侧日志定位。
     */
    private List<String> readParseQualityMessages(JsonNode root) {
        JsonNode messages = root == null ? null : root.path("parseQuality").path("messages");
        return readTextArray(messages);
    }

    /**
     * 读取 Python 返回的 RAG 进度事件。
     */
    private List<ProgressResult> readProgressEvents(JsonNode node) {
        List<ProgressResult> result = new ArrayList<>();
        if (node == null || !node.isArray()) {
            return result;
        }
        for (JsonNode item : node) {
            result.add(new ProgressResult(
                    text(item, "stageCode"),
                    text(item, "stageLabel"),
                    text(item, "message"),
                    text(item, "status"),
                    nullableInt(item, "currentStep"),
                    nullableInt(item, "totalSteps"),
                    nullableInt(item, "currentChunk"),
                    nullableInt(item, "totalChunks"),
                    text(item, "chunkId"),
                    text(item, "blockId"),
                    nullableInt(item, "percent"),
                    text(item, "detail")
            ));
        }
        return result;
    }

    /**
     * 读取查询接口返回的 RAG 阶段事件。
     */
    private List<RagProgressVO> readProgressVOs(JsonNode node) {
        return readProgressEvents(node).stream()
                .map(item -> RagProgressVO.builder()
                        .stageCode(item.stageCode())
                        .stageLabel(item.stageLabel())
                        .message(item.message())
                        .status(item.status())
                        .currentStep(item.currentStep())
                        .totalSteps(item.totalSteps())
                        .currentChunk(item.currentChunk())
                        .totalChunks(item.totalChunks())
                        .chunkId(item.chunkId())
                        .blockId(item.blockId())
                        .percent(item.percent())
                        .detail(item.detail())
                        .build())
                .toList();
    }

    /**
     * 将 Python evidence JSON 转换为前端展示对象。
     */
    private RagEvidenceVO readEvidence(JsonNode item) {
        return RagEvidenceVO.builder()
                .evidenceId(text(item, "evidenceId"))
                .documentId(text(item, "documentId"))
                .documentTitle(text(item, "documentTitle"))
                .blockId(text(item, "blockId"))
                .blockType(text(item, "blockType"))
                .pageIndex(nullableInt(item, "pageIndex"))
                .slideIndex(nullableInt(item, "slideIndex"))
                .startTime(text(item, "startTime"))
                .endTime(text(item, "endTime"))
                .sheetName(text(item, "sheetName"))
                .cellRange(text(item, "cellRange"))
                .sectionTitle(text(item, "sectionTitle"))
                .title(text(item, "title"))
                .snippet(text(item, "snippet"))
                .source(text(item, "source"))
                .sourcePath(text(item, "sourcePath"))
                .assetPath(text(item, "assetPath"))
                .playbackUrl(text(item, "playbackUrl"))
                .sectionName(text(item, "sectionName"))
                .documentType(text(item, "documentType"))
                .score(item.path("score").asDouble())
                .retrievalSource(text(item, "retrievalSource"))
                .parseEngine(text(item, "parseEngine"))
                .build();
    }

    /**
     * 读取 JSON 文本字段，空值返回 null。
     */
    private String text(JsonNode node, String fieldName) {
        JsonNode value = node == null ? null : node.get(fieldName);
        return value == null || value.isNull() ? null : value.asText();
    }

    /**
     * 读取 JSON 整数字段，空值返回 null。
     */
    private Integer nullableInt(JsonNode node, String fieldName) {
        JsonNode value = node == null ? null : node.get(fieldName);
        return value == null || value.isNull() ? null : value.asInt();
    }

    /**
     * 读取 JSON 字符串数组。
     */
    private List<String> readTextArray(JsonNode node) {
        List<String> result = new ArrayList<>();
        if (node == null || !node.isArray()) {
            return result;
        }
        for (JsonNode item : node) {
            result.add(item.asText());
        }
        return result;
    }

    /**
     * 读取 Python 返回的诊断信息。
     */
    private Map<String, Object> readObjectMap(JsonNode node) {
        if (node == null || !node.isObject()) {
            return Map.of();
        }
        return objectMapper.convertValue(node, Map.class);
    }

    /**
     * 读取 Python JD 技能分析数组。
     */
    private List<JdSkillResult> readSkillResults(JsonNode node) {
        List<JdSkillResult> result = new ArrayList<>();
        if (node == null || !node.isArray()) {
            return result;
        }
        for (JsonNode item : node) {
            result.add(new JdSkillResult(text(item, "skillName"), text(item, "status")));
        }
        return result;
    }

    /**
     * 读取 Python JD 学习计划数组。
     */
    private List<JdPlanResult> readPlanResults(JsonNode node) {
        List<JdPlanResult> result = new ArrayList<>();
        if (node == null || !node.isArray()) {
            return result;
        }
        for (JsonNode item : node) {
            result.add(new JdPlanResult(
                    item.path("stepNo").asInt(result.size() + 1),
                    text(item, "title"),
                    text(item, "description")
            ));
        }
        return result;
    }

    /**
     * 读取 Python 简历证据对齐数组。
     */
    private List<ResumeAlignmentResult> readAlignmentResults(JsonNode node) {
        List<ResumeAlignmentResult> result = new ArrayList<>();
        if (node == null || !node.isArray()) {
            return result;
        }
        for (JsonNode item : node) {
            result.add(new ResumeAlignmentResult(
                    text(item, "requirement"),
                    text(item, "evidence"),
                    text(item, "status")
            ));
        }
        return result;
    }

    /**
     * 将 Python HTTP 错误转换为统一客户端异常。
     */
    private PythonRagClientException pythonException(String operation, String endpoint, RestClientResponseException e) {
        return new PythonRagClientException(
                operation,
                endpoint,
                e.getStatusCode().value(),
                e.getResponseBodyAsString(StandardCharsets.UTF_8),
                "Python RAG 调用失败: " + e.getStatusCode(),
                e
        );
    }

    /**
     * 将 Python 调用过程中的非 HTTP 异常转换为统一客户端异常。
     */
    private PythonRagClientException pythonException(String operation, String endpoint, Exception e) {
        return new PythonRagClientException(
                operation,
                endpoint,
                null,
                null,
                "Python RAG 调用失败: " + e.getMessage(),
                e
        );
    }

    /**
     * 截断过长响应摘要，避免日志上下文过大。
     */
    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }

    /**
     * Python 索引响应摘要。
     */
    public record IndexResult(
            String documentId,
            String title,
            String status,
            String parser,
            String documentSummary,
            Integer chunkCount,
            List<String> parseQualityMessages,
            List<ProgressResult> progressEvents
    ) {
    }

    /**
     * Python 返回的单条 RAG 进度事件。
     */
    public record ProgressResult(
            String stageCode,
            String stageLabel,
            String message,
            String status,
            Integer currentStep,
            Integer totalSteps,
            Integer currentChunk,
            Integer totalChunks,
            String chunkId,
            String blockId,
            Integer percent,
            String detail
    ) {
    }

    /**
     * Python 概览响应摘要。
     */
    public record PythonOverview(
            Integer documentCount,
            Integer chunkCount,
            Integer evidenceCount,
            String lastIndexedTitle
    ) {
    }

    /**
     * Python JD 分析响应。
     */
    public record JdAnalysisResult(
            String jobDescription,
            Integer matchScore,
            Integer masteredPercent,
            Integer partialPercent,
            Integer gapPercent,
            List<JdSkillResult> skills,
            List<JdPlanResult> learningPlan,
            List<ResumeAlignmentResult> resumeAlignments
    ) {
    }

    /**
     * Python JD 技能匹配结果。
     */
    public record JdSkillResult(String skillName, String status) {
    }

    /**
     * Python JD 学习计划结果。
     */
    public record JdPlanResult(Integer stepNo, String title, String description) {
    }

    /**
     * Python 简历证据对齐结果。
     */
    public record ResumeAlignmentResult(String requirement, String evidence, String status) {
    }

    /**
     * 携带 Python 接口上下文的客户端异常。
     */
    public static class PythonRagClientException extends IllegalStateException {
        private final String operation;
        private final String endpoint;
        private final Integer statusCode;
        private final String responseBody;

        public PythonRagClientException(String operation,
                                        String endpoint,
                                        Integer statusCode,
                                        String responseBody,
                                        String message,
                                        Throwable cause) {
            super(message, cause);
            this.operation = operation;
            this.endpoint = endpoint;
            this.statusCode = statusCode;
            this.responseBody = responseBody;
        }

        /**
         * 获取调用操作名。
         */
        public String getOperation() {
            return operation;
        }

        /**
         * 获取 Python 接口路径。
         */
        public String getEndpoint() {
            return endpoint;
        }

        /**
         * 获取 HTTP 状态码。
         */
        public Integer getStatusCode() {
            return statusCode;
        }

        /**
         * 获取 Python 响应体摘要。
         */
        public String getResponseBody() {
            return responseBody;
        }
    }

    /**
     * 为 multipart 文件上传补充原始文件名。
     */
    private static class NamedByteArrayResource extends ByteArrayResource {
        private final String filename;

        /**
         * 保存文件字节和文件名。
         */
        NamedByteArrayResource(byte[] byteArray, String filename) {
            super(byteArray);
            this.filename = filename;
        }

        /**
         * 返回 multipart 中使用的文件名。
         */
        @Override
        public String getFilename() {
            return filename;
        }
    }
}
