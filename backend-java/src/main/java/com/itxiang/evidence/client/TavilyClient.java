package com.itxiang.evidence.client;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.TavilyProperties;
import lombok.Getter;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.net.URI;
import java.util.LinkedHashMap;
import java.util.Map;

@Component
public class TavilyClient {

    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };

    private final TavilyProperties tavilyProperties;
    private final ObjectMapper objectMapper;
    private final RestClient restClient;

    /**
     * 初始化 Tavily 联网搜索客户端。
     */
    public TavilyClient(TavilyProperties tavilyProperties, ObjectMapper objectMapper) {
        this.tavilyProperties = tavilyProperties;
        this.objectMapper = objectMapper;
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(tavilyProperties.getTimeoutSeconds() * 1000);
        requestFactory.setReadTimeout(tavilyProperties.getTimeoutSeconds() * 1000);
        this.restClient = RestClient.builder()
                .requestFactory(requestFactory)
                .build();
    }

    /**
     * 调用 Tavily Search API。
     */
    public Map<String, Object> search(Map<String, Object> payload) {
        String apiKey = tavilyProperties.getApiKey();
        if (apiKey == null || apiKey.isBlank()) {
            throw new TavilyClientException("search", "/search", null, null, "Tavily API Key 未配置", null);
        }
        try {
            JsonNode body = restClient.post()
                    .uri(resolve("/search"))
                    .header("Authorization", "Bearer " + apiKey)
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(payload == null ? Map.of() : payload)
                    .retrieve()
                    .body(JsonNode.class);
            return body == null ? new LinkedHashMap<>() : objectMapper.convertValue(body, MAP_TYPE);
        } catch (RestClientResponseException e) {
            throw new TavilyClientException("search", "/search", e.getStatusCode().value(), e.getResponseBodyAsString(), "Tavily 搜索调用失败", e);
        } catch (TavilyClientException e) {
            throw e;
        } catch (Exception e) {
            throw new TavilyClientException("search", "/search", null, null, "Tavily 搜索调用失败：" + e.getMessage(), e);
        }
    }

    /**
     * 拼接 Tavily API 地址。
     */
    private URI resolve(String endpoint) {
        return URI.create(tavilyProperties.getBaseUrl().replaceAll("/+$", "") + endpoint);
    }

    @Getter
    public static class TavilyClientException extends IllegalStateException {
        private final String operation;
        private final String endpoint;
        private final Integer statusCode;
        private final String responseBody;

        public TavilyClientException(String operation,
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
    }
}
