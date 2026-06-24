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
            return restClient.post()
                    .uri(resolve("/internal/agent/tasks"))
                    .header("X-Agent-Internal-Token", agentProperties.getInternalToken())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(payload)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (RestClientResponseException e) {
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
            return restClient.post()
                    .uri(resolve("/internal/agent/tasks/" + task.getId() + "/resume"))
                    .header("X-Agent-Internal-Token", agentProperties.getInternalToken())
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(payload)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (RestClientResponseException e) {
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
