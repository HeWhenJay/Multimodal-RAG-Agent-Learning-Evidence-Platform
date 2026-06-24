package com.itxiang.evidence;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonAgentClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.blankOrNullString;
import static org.hamcrest.Matchers.hasItem;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@EvidenceSpringBootTest
@AutoConfigureMockMvc
class AgentControllerTests {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @MockBean
    private PythonAgentClient pythonAgentClient;

    @Test
    void createTaskAndReadDetailUseAgentTables() throws Exception {
        String token = loginToken();
        MvcResult createResult = mockMvc.perform(post("/api/agent/tasks")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "taskType": "pure_read_query",
                                  "title": "查询 Redis 学习证据",
                                  "input": {
                                    "goal": "我的知识库里 Redis 学到了什么？",
                                    "topK": 5
                                  }
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(1))
                .andExpect(jsonPath("$.data.id", not(blankOrNullString())))
                .andExpect(jsonPath("$.data.status").value("CREATED"))
                .andExpect(jsonPath("$.data.input.goal").value("我的知识库里 Redis 学到了什么？"))
                .andReturn();

        JsonNode root = objectMapper.readTree(createResult.getResponse().getContentAsString());
        String taskId = root.path("data").path("id").asText();

        mockMvc.perform(get("/api/agent/tasks/" + taskId)
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(1))
                .andExpect(jsonPath("$.data.id").value(taskId))
                .andExpect(jsonPath("$.data.toolCalls").isArray())
                .andExpect(jsonPath("$.data.reviews").isArray())
                .andExpect(jsonPath("$.data.operations").isArray());

        mockMvc.perform(get("/api/agent/tools")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(1))
                .andExpect(jsonPath("$.data[*].toolName", hasItem("material_status_reader")))
                .andExpect(jsonPath("$.data[*].toolName", hasItem("rag_query_probe_non_persistent")));
    }

    @Test
    void decideReviewRequiresOwnerTaskAndUpdatesPendingReview() throws Exception {
        String token = loginToken();
        MvcResult createResult = mockMvc.perform(post("/api/agent/tasks")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "taskType": "planning_task",
                                  "title": "后端实习 JD 适配分析",
                                  "input": {
                                    "goal": "分析后端实习 JD 适配度"
                                  }
                                }
                                """))
                .andExpect(status().isOk())
                .andReturn();

        JsonNode root = objectMapper.readTree(createResult.getResponse().getContentAsString());
        String taskId = root.path("data").path("id").asText();

        mockMvc.perform(post("/api/internal/agent/tasks/" + taskId + "/events")
                        .header("X-Agent-Internal-Token", "agent-secret")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "eventType": "REVIEW_REQUESTED",
                                  "status": "WAITING_PLAN_REVIEW",
                                  "pythonThreadId": "%s",
                                  "reviewRequest": {
                                    "id": "review-plan-test",
                                    "reviewType": "PLAN",
                                    "proposal": {
                                      "title": "后端实习 JD 适配分析计划"
                                    }
                                  }
                                }
                                """.formatted(taskId)))
                .andExpect(status().isOk());

        mockMvc.perform(post("/api/agent/tasks/" + taskId + "/reviews/review-plan-test/decide")
                        .header("Authorization", "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "decision": "CHANGES_REQUESTED",
                                  "comment": "请补充 Redis 证据检查",
                                  "changes": {
                                    "focus": "Redis"
                                  }
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(1))
                .andExpect(jsonPath("$.data.status").value("WAITING_PLAN_REVIEW"))
                .andExpect(jsonPath("$.data.reviews[0].status").value("CHANGES_REQUESTED"))
                .andExpect(jsonPath("$.data.reviews[0].decision.comment").value("请补充 Redis 证据检查"));
    }

    /**
     * 登录测试管理员并返回 Bearer Token。
     */
    private String loginToken() throws Exception {
        MvcResult loginResult = mockMvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "account": "admin",
                                  "password": "123456",
                                  "remember": false
                                }
                                """))
                .andReturn();
        JsonNode root = objectMapper.readTree(loginResult.getResponse().getContentAsString());
        return root.path("data").path("token").asText();
    }
}
