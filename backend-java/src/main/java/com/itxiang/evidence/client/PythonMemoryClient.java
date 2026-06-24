package com.itxiang.evidence.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.config.PythonRagProperties;
import lombok.Getter;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.net.URI;
import java.util.Map;

@Slf4j
@Component
public class PythonMemoryClient {

    private final PythonRagProperties pythonRagProperties;
    private final AgentProperties agentProperties;
    private final RestClient restClient;

    /**
     * 初始化 Python Memory Service 客户端，复用 Agent 内部令牌。
     */
    public PythonMemoryClient(PythonRagProperties pythonRagProperties, AgentProperties agentProperties) {
        this.pythonRagProperties = pythonRagProperties;
        this.agentProperties = agentProperties;
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(5000);
        requestFactory.setReadTimeout(30000);
        this.restClient = RestClient.builder()
                .requestFactory(requestFactory)
                .build();
    }

    /**
     * 请求 Python 记忆检索。
     */
    public JsonNode query(Map<String, Object> payload) {
        return post("/internal/agent/memory/query", payload, "Python 记忆检索失败");
    }

    /**
     * 请求 Python 提炼记忆候选。
     */
    public JsonNode extract(Map<String, Object> payload) {
        return post("/internal/agent/memory/extract", payload, "Python 记忆候选提炼失败");
    }

    /**
     * 请求 Python 做候选冲突判断。
     */
    public JsonNode conflicts(Map<String, Object> payload) {
        return post("/internal/agent/memory/conflicts", payload, "Python 记忆冲突判断失败");
    }

    /**
     * 请求 Python 写入或更新检索索引。
     */
    public JsonNode upsertIndex(Map<String, Object> payload) {
        return post("/internal/agent/memory/index/upsert", payload, "Python 记忆索引写入失败");
    }

    /**
     * 请求 Python 删除或停用检索索引。
     */
    public JsonNode deleteIndex(Map<String, Object> payload) {
        return post("/internal/agent/memory/index/delete", payload, "Python 记忆索引删除失败");
    }

    /**
     * 发送内部 JSON 请求。
     */
    private JsonNode post(String endpoint, Map<String, Object> payload, String operation) {
        String token = agentProperties.getInternalToken();
        if (token == null || token.isBlank()) {
            throw new PythonMemoryClientException(null, null, operation + "：内部 Agent 令牌未配置", null);
        }
        try {
            return restClient.post()
                    .uri(resolve(endpoint))
                    .header("X-Agent-Internal-Token", token)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(payload == null ? Map.of() : payload)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (RestClientResponseException e) {
            throw new PythonMemoryClientException(e.getStatusCode().value(), e.getResponseBodyAsString(), operation, e);
        } catch (Exception e) {
            throw new PythonMemoryClientException(null, null, operation + "：" + e.getMessage(), e);
        }
    }

    /**
     * 拼接 Python 服务地址。
     */
    private URI resolve(String endpoint) {
        String base = pythonRagProperties.getPythonBaseUrl();
        return URI.create(base.replaceAll("/+$", "") + endpoint);
    }

    @Getter
    public static class PythonMemoryClientException extends IllegalStateException {
        private final Integer statusCode;
        private final String responseBody;

        public PythonMemoryClientException(Integer statusCode, String responseBody, String message, Throwable cause) {
            super(message, cause);
            this.statusCode = statusCode;
            this.responseBody = responseBody;
        }
    }
}
