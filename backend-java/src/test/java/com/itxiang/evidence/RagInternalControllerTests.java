package com.itxiang.evidence;

import com.itxiang.evidence.config.RagKafkaProperties;
import com.itxiang.evidence.controller.RagInternalController;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagIndexJob;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.RagIndexJobMapper;
import com.itxiang.evidence.service.ObjectStorageService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;

import static org.hamcrest.Matchers.containsString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class RagInternalControllerTests {

    private LearningMaterialMapper materialMapper;
    private RagIndexJobMapper jobMapper;
    private ObjectStorageService objectStorageService;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        RagKafkaProperties properties = new RagKafkaProperties();
        properties.setInternalToken("rag-secret");
        materialMapper = mock(LearningMaterialMapper.class);
        jobMapper = mock(RagIndexJobMapper.class);
        objectStorageService = mock(ObjectStorageService.class);
        mockMvc = MockMvcBuilders.standaloneSetup(new RagInternalController(properties, materialMapper, jobMapper, objectStorageService)).build();
    }

    @Test
    void sourceApiRejectsInvalidToken() throws Exception {
        mockMvc.perform(get("/api/internal/rag/materials/1/source")
                        .param("jobId", "job-1")
                        .param("requestVersion", "1")
                        .header("X-RAG-Internal-Token", "bad"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void sourceApiStreamsMaterialBytesForActiveJob() throws Exception {
        LearningMaterial material = new LearningMaterial();
        material.setId(1L);
        material.setTitle("note.md");
        material.setUserId("7");
        material.setOriginalFilename("note.md");
        material.setStorageType("local");
        material.setOriginalFilePath("uploads/note.md");
        material.setActiveIndexJobId("job-1");
        material.setIndexRequestVersion(2);
        RagIndexJob job = new RagIndexJob();
        job.setId("job-1");
        job.setMaterialId(1L);
        job.setRequestVersion(2);
        job.setStatus("REQUESTED");
        when(materialMapper.findById(eq(1L))).thenReturn(material);
        when(jobMapper.findById(eq("job-1"))).thenReturn(job);
        when(objectStorageService.open(eq("local"), eq("uploads/note.md"), eq(null), eq("note.md")))
                .thenReturn(new ObjectStorageService.OpenedObject(
                        new ByteArrayInputStream("Kafka Source API".getBytes(StandardCharsets.UTF_8)),
                        "note.md",
                        "text/markdown",
                        16L,
                        null
                ));

        MvcResult asyncResult = mockMvc.perform(get("/api/internal/rag/materials/1/source")
                        .param("jobId", "job-1")
                        .param("requestVersion", "2")
                        .header("X-RAG-Internal-Token", "rag-secret"))
                .andExpect(status().isOk())
                .andExpect(request().asyncStarted())
                .andReturn();

        mockMvc.perform(asyncDispatch(asyncResult))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Disposition", containsString("note.md")))
                .andExpect(content().string("Kafka Source API"));
    }

    @Test
    void activeCheckReturnsActiveForCurrentJob() throws Exception {
        LearningMaterial material = new LearningMaterial();
        material.setId(1L);
        material.setActiveIndexJobId("job-1");
        material.setIndexRequestVersion(2);
        RagIndexJob job = new RagIndexJob();
        job.setId("job-1");
        job.setMaterialId(1L);
        job.setRequestVersion(2);
        job.setStatus("INDEXED");
        when(materialMapper.findById(eq(1L))).thenReturn(material);
        when(jobMapper.findById(eq("job-1"))).thenReturn(job);

        mockMvc.perform(get("/api/internal/rag/materials/1/index-jobs/job-1/active")
                        .param("requestVersion", "2")
                        .header("X-RAG-Internal-Token", "rag-secret"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.active").value(true))
                .andExpect(jsonPath("$.data.jobId").value("job-1"));
    }
}
