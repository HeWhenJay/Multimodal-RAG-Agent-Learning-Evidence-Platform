package com.itxiang.evidence.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.config.PythonRagProperties;
import com.itxiang.evidence.entity.AgentTask;
import lombok.Getter;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.net.URI;
import java.util.LinkedHashMap;
import java.util.Map;

@Slf4j
@Component
public class PythonAgentClient {

    private final PythonRagProperties pythonRagProperties;
    private final AgentProperties agentProperties;
    private final RestClient restClient;

    /**
     * 初始化 Python Agent HTTP 客户端，使用独立接收任务超时。
     */
    public PythonAgentClient(PythonRagProperties pythonRagProperties, AgentProperties agentProperties) {
        this.pythonRagProperties = pythonRagProperties;
        this.agentProperties = agentProperties;
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(5000);
        requestFactory.setReadTimeout(agentProperties.getStartTimeoutSeconds() * 1000);
        this.restClient = RestClient.builder()
                .requestFactory(requestFactory)
                .build();
    }

    /**
     * 请求 Python 启动只读 Agent 任务，任务执行结果由 Python 回调 Java events。
     */
    public JsonNode startTask(AgentTask task, Map<String, Object> input) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("taskId", task.getId());
        payload.put("taskType", task.getTaskType());
        payload.put("input", input == null ? Map.of() : input);
        payload.put("callbackUrl", agentProperties.getJavaBaseUrl().replaceAll("/+$", "")
                + "/api/internal/agent/tasks/" + task.getId() + "/events");
        payload.put("javaToolGatewayBaseUrl", agentProperties.getJavaBaseUrl().replaceAll("/+$", ""));
        payload.put("threadId", task.getId());
        try {
            URI endpoint = resolve("/internal/agent/tasks");
            log.info("请求 Python Agent 接收任务: taskId={}, taskType={}, endpoint={}, timeoutSeconds={}",
                    task.getId(), task.getTaskType(), endpoint, agentProperties.getStartTimeoutSeconds());
            JsonNode response = restClient.post()
                    .uri(endpoint)
                    .header("X-Agent-Internal-Token", agentProperties.getInternalToken())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(payload)
                    .retrieve()
                    .body(JsonNode.class);
            log.info("Python Agent 接收任务响应: taskId={}, accepted={}, status={}",
                    task.getId(), response == null ? null : response.path("accepted").asText(null), response == null ? null : response.path("status").asText(null));
            return response;
        } catch (RestClientResponseException e) {
            log.warn("Python Agent 接收任务 HTTP 失败: taskId={}, statusCode={}, responseBody={}",
                    task.getId(), e.getStatusCode().value(), truncate(e.getResponseBodyAsString(), 500));
            throw new PythonAgentClientException(e.getStatusCode().value(), e.getResponseBodyAsString(), "Python Agent 启动失败", e);
        } catch (Exception e) {
            throw new PythonAgentClientException(null, null, "Python Agent 启动失败：" + e.getMessage(), e);
        }
    }

    /**
     * 用户审批后恢复 Python Agent 任务。
     */
    public JsonNode resumeTask(AgentTask task,
                               String reviewType,
                               String decision,
                               Map<String, Object> decisionPayload,
                               Map<String, Object> input) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("taskId", task.getId());
        payload.put("taskType", task.getTaskType());
        payload.put("threadId", task.getPythonThreadId() == null || task.getPythonThreadId().isBlank() ? task.getId() : task.getPythonThreadId());
        payload.put("reviewType", reviewType);
        payload.put("decision", decision);
        payload.put("decisionPayload", decisionPayload == null ? Map.of() : decisionPayload);
        payload.put("input", input == null ? Map.of() : input);
        payload.put("callbackUrl", agentProperties.getJavaBaseUrl().replaceAll("/+$", "")
                + "/api/internal/agent/tasks/" + task.getId() + "/events");
        payload.put("javaToolGatewayBaseUrl", agentProperties.getJavaBaseUrl().replaceAll("/+$", ""));
        try {
            URI endpoint = resolve("/internal/agent/tasks/" + task.getId() + "/resume");
            log.info("请求 Python Agent 恢复任务: taskId={}, reviewType={}, decision={}, endpoint={}",
                    task.getId(), reviewType, decision, endpoint);
            JsonNode response = restClient.post()
                    .uri(endpoint)
                    .header("X-Agent-Internal-Token", agentProperties.getInternalToken())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(payload)
                    .retrieve()
                    .body(JsonNode.class);
            log.info("Python Agent 恢复任务响应: taskId={}, accepted={}, status={}",
                    task.getId(), response == null ? null : response.path("accepted").asText(null), response == null ? null : response.path("status").asText(null));
            return response;
        } catch (RestClientResponseException e) {
            log.warn("Python Agent 恢复任务 HTTP 失败: taskId={}, statusCode={}, responseBody={}",
                    task.getId(), e.getStatusCode().value(), truncate(e.getResponseBodyAsString(), 500));
            throw new PythonAgentClientException(e.getStatusCode().value(), e.getResponseBodyAsString(), "Python Agent 恢复失败", e);
        } catch (Exception e) {
            throw new PythonAgentClientException(null, null, "Python Agent 恢复失败：" + e.getMessage(), e);
        }
    }

    /**
     * 拼接 Python Agent 服务地址。
     */
    private URI resolve(String endpoint) {
        String base = pythonRagProperties.getPythonBaseUrl();
        return URI.create(base.replaceAll("/+$", "") + endpoint);
    }

    /**
     * 截断下游响应，避免控制台被长错误体刷屏。
     */
    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }

    @Getter
    public static class PythonAgentClientException extends IllegalStateException {
        private final Integer statusCode;
        private final String responseBody;

        public PythonAgentClientException(Integer statusCode, String responseBody, String message, Throwable cause) {
            super(message, cause);
            this.statusCode = statusCode;
            this.responseBody = responseBody;
        }
    }
}
