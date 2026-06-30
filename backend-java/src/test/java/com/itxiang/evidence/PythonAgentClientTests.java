package com.itxiang.evidence;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonAgentClient;
import com.itxiang.evidence.config.AgentProperties;
import com.itxiang.evidence.config.PythonRagProperties;
import com.itxiang.evidence.entity.AgentTask;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class PythonAgentClientTests {

    @Test
    void resumeTaskParsesOctetStreamJsonResponse() throws Exception {
        try (LocalJsonServer server = LocalJsonServer.start("{\"accepted\":true,\"status\":\"WAITING_OUTPUT_REVIEW\",\"threadId\":\"agent-task-1\"}")) {
            PythonAgentClient client = new PythonAgentClient(pythonProperties(server.baseUrl()), agentProperties(), new ObjectMapper());
            AgentTask task = new AgentTask();
            task.setId("agent-task-1");
            task.setTaskType("planning_task");
            task.setPythonThreadId("agent-task-1");

            JsonNode result = client.resumeTask(task, "PLAN", "APPROVED", Map.of("comment", "同意继续"), Map.of("goal", "自由探索"));

            assertThat(result.path("accepted").asBoolean()).isTrue();
            assertThat(result.path("status").asText()).isEqualTo("WAITING_OUTPUT_REVIEW");
            assertThat(server.lastPath()).isEqualTo("/internal/agent/tasks/agent-task-1/resume");
        }
    }

    @Test
    void startTaskParsesOctetStreamJsonResponse() throws Exception {
        try (LocalJsonServer server = LocalJsonServer.start("{\"accepted\":true,\"status\":\"WAITING_PLAN_REVIEW\",\"threadId\":\"agent-task-1\"}")) {
            PythonAgentClient client = new PythonAgentClient(pythonProperties(server.baseUrl()), agentProperties(), new ObjectMapper());
            AgentTask task = new AgentTask();
            task.setId("agent-task-1");
            task.setTaskType("planning_task");

            JsonNode result = client.startTask(task, Map.of("goal", "自由探索"));

            assertThat(result.path("accepted").asBoolean()).isTrue();
            assertThat(result.path("status").asText()).isEqualTo("WAITING_PLAN_REVIEW");
            assertThat(server.lastPath()).isEqualTo("/internal/agent/tasks");
        }
    }

    private static PythonRagProperties pythonProperties(String baseUrl) {
        PythonRagProperties properties = new PythonRagProperties();
        properties.setPythonBaseUrl(baseUrl);
        return properties;
    }

    private static AgentProperties agentProperties() {
        AgentProperties properties = new AgentProperties();
        properties.setInternalToken("agent-secret");
        properties.setJavaBaseUrl("http://java");
        properties.setStartTimeoutSeconds(5);
        return properties;
    }

    private static final class LocalJsonServer implements AutoCloseable {
        private final HttpServer server;
        private volatile String lastPath;

        private LocalJsonServer(HttpServer server) {
            this.server = server;
        }

        static LocalJsonServer start(String responseBody) throws IOException {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            LocalJsonServer local = new LocalJsonServer(server);
            server.createContext("/", exchange -> local.respond(exchange, responseBody));
            server.start();
            return local;
        }

        String baseUrl() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        String lastPath() {
            return lastPath;
        }

        private void respond(HttpExchange exchange, String responseBody) throws IOException {
            lastPath = exchange.getRequestURI().getPath();
            byte[] body = responseBody.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/octet-stream");
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }
}
