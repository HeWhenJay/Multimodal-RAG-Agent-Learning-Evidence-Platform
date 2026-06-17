package com.itxiang.evidence;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.LogErrorVO;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@EvidenceSpringBootTest
@AutoConfigureMockMvc
class RagErrorHandlingTests {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private LogService logService;

    @Autowired
    private ObjectMapper objectMapper;

    @MockBean
    private RagService ragService;

    @Test
    void unexpectedRagErrorReturnsStageAndWritesFallbackLog() throws Exception {
        when(ragService.listRecentMaterials(anyString())).thenThrow(new RuntimeException("boom-not-predefined"));

        mockMvc.perform(get("/api/rag/materials")
                        .header("Authorization", "Bearer " + loginToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.msg", containsString("获取近期学习资料 [material/list/material_list_query] 失败")))
                .andExpect(jsonPath("$.msg", containsString("boom-not-predefined")));

        List<LogErrorVO> errors = logService.listRecentErrors(20);
        assertThat(errors).anySatisfy(error -> {
            assertThat(error.getDomain()).isEqualTo("rag");
            assertThat(error.getModule()).isEqualTo("material");
            assertThat(error.getStage()).isEqualTo("list");
            assertThat(error.getAction()).isEqualTo("material_list_query");
            assertThat(error.getErrorCode()).isEqualTo("RAG_UNEXPECTED_ERROR");
            assertThat(error.getContextJson()).contains("failureStageLabel");
        });
    }

    @Test
    void reindexMaterialEndpointReturnsUpdatedMaterial() throws Exception {
        when(ragService.reindexMaterial(eq(7L), eq(true), anyString())).thenReturn(
                LearningMaterialVO.builder()
                        .id(7L)
                        .title("低质量课程视频.mp4")
                        .documentType("mp4")
                        .source("upload")
                        .status("PARTIAL")
                        .storageType("oss")
                        .chunkCount(3)
                        .build()
        );

        mockMvc.perform(post("/api/rag/materials/7/reindex?highPrecision=true")
                        .header("Authorization", "Bearer " + loginToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(1))
                .andExpect(jsonPath("$.data.id").value(7))
                .andExpect(jsonPath("$.data.status").value("PARTIAL"))
                .andExpect(jsonPath("$.data.storageType").value("oss"));
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
