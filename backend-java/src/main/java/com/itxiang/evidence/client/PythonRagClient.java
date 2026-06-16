package com.itxiang.evidence.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.PythonRagProperties;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.entity.LearningMaterial;
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

    public IndexResult indexFile(Long materialId,
                                 String userId,
                                 LearningMaterial material,
                                 MultipartFile file,
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
            body.add("file", new NamedByteArrayResource(file.getBytes(), material.getTitle()));

            String response = restClient.post()
                    .uri(resolve("/internal/rag/documents/index-file"))
                    .contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(body)
                    .retrieve()
                    .body(String.class);
            return readIndexResult(objectMapper.readTree(response));
        } catch (RestClientResponseException e) {
            throw pythonException("index-file", "/internal/rag/documents/index-file", e);
        } catch (Exception e) {
            throw pythonException("index-file", "/internal/rag/documents/index-file", e);
        }
    }

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
                .build();
    }

    public List<RagEvidenceVO> listDocumentEvidences(String documentId, Integer limit) {
        try {
            String response = restClient.get()
                    .uri(resolve("/internal/rag/documents/" + documentId + "/evidences?limit=" + limit))
                    .retrieve()
                    .body(String.class);
            JsonNode root = objectMapper.readTree(response);
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
        } catch (Exception e) {
            throw pythonException("list-evidences", "/internal/rag/documents/{document_id}/evidences", e);
        }
    }

    public PythonOverview fetchOverviewSafely() {
        try {
            String response = restClient.get()
                    .uri(resolve("/internal/rag/overview"))
                    .retrieve()
                    .body(String.class);
            JsonNode root = objectMapper.readTree(response);
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

    private JsonNode postJson(String path, Map<String, Object> payload) {
        try {
            String requestBody = objectMapper.writeValueAsString(payload);
            String response = restClient.post()
                    .uri(resolve(path))
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(requestBody)
                    .retrieve()
                    .body(String.class);
            return objectMapper.readTree(response);
        } catch (RestClientResponseException e) {
            throw pythonException("post-json", path, e);
        } catch (Exception e) {
            throw pythonException("post-json", path, e);
        }
    }

    private URI resolve(String path) {
        return URI.create(properties.getPythonBaseUrl().replaceAll("/$", "") + path);
    }

    private IndexResult readIndexResult(JsonNode root) {
        if (root == null || !root.hasNonNull("documentId") || !root.hasNonNull("status")) {
            throw new PythonRagClientException(
                    "read-index-result",
                    "python-response",
                    null,
                    null,
                    "Python RAG response schema is invalid",
                    null
            );
        }
        return new IndexResult(
                text(root, "documentId"),
                text(root, "title"),
                text(root, "status"),
                text(root, "parser"),
                text(root, "documentSummary"),
                root.path("chunkCount").asInt(0)
        );
    }

    private RagEvidenceVO readEvidence(JsonNode item) {
        return RagEvidenceVO.builder()
                .evidenceId(text(item, "evidenceId"))
                .documentId(text(item, "documentId"))
                .documentTitle(text(item, "documentTitle"))
                .blockId(text(item, "blockId"))
                .blockType(text(item, "blockType"))
                .pageIndex(nullableInt(item, "pageIndex"))
                .slideIndex(nullableInt(item, "slideIndex"))
                .sheetName(text(item, "sheetName"))
                .cellRange(text(item, "cellRange"))
                .sectionTitle(text(item, "sectionTitle"))
                .title(text(item, "title"))
                .snippet(text(item, "snippet"))
                .source(text(item, "source"))
                .sourcePath(text(item, "sourcePath"))
                .assetPath(text(item, "assetPath"))
                .sectionName(text(item, "sectionName"))
                .documentType(text(item, "documentType"))
                .score(item.path("score").asDouble())
                .retrievalSource(text(item, "retrievalSource"))
                .parseEngine(text(item, "parseEngine"))
                .build();
    }

    private String text(JsonNode node, String fieldName) {
        JsonNode value = node == null ? null : node.get(fieldName);
        return value == null || value.isNull() ? null : value.asText();
    }

    private Integer nullableInt(JsonNode node, String fieldName) {
        JsonNode value = node == null ? null : node.get(fieldName);
        return value == null || value.isNull() ? null : value.asInt();
    }

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

    private PythonRagClientException pythonException(String operation, String endpoint, RestClientResponseException e) {
        return new PythonRagClientException(
                operation,
                endpoint,
                e.getStatusCode().value(),
                e.getResponseBodyAsString(),
                "Python RAG call failed: " + e.getStatusCode(),
                e
        );
    }

    private PythonRagClientException pythonException(String operation, String endpoint, Exception e) {
        return new PythonRagClientException(
                operation,
                endpoint,
                null,
                null,
                "Python RAG call failed: " + e.getMessage(),
                e
        );
    }

    public record IndexResult(
            String documentId,
            String title,
            String status,
            String parser,
            String documentSummary,
            Integer chunkCount
    ) {
    }

    public record PythonOverview(
            Integer documentCount,
            Integer chunkCount,
            Integer evidenceCount,
            String lastIndexedTitle
    ) {
    }

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

        public String getOperation() {
            return operation;
        }

        public String getEndpoint() {
            return endpoint;
        }

        public Integer getStatusCode() {
            return statusCode;
        }

        public String getResponseBody() {
            return responseBody;
        }
    }

    private static class NamedByteArrayResource extends ByteArrayResource {
        private final String filename;

        NamedByteArrayResource(byte[] byteArray, String filename) {
            super(byteArray);
            this.filename = filename;
        }

        @Override
        public String getFilename() {
            return filename;
        }
    }
}
