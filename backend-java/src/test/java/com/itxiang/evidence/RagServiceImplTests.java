package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.config.PythonRagProperties;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.dto.ResumePatchGenerateDTO;
import com.itxiang.evidence.dto.ResumeTemplateAnnotationSaveDTO;
import com.itxiang.evidence.dto.ResumeTemplateExportDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagQueryHistory;
import com.itxiang.evidence.entity.ResumeTemplate;
import com.itxiang.evidence.entity.ResumeTemplateExport;
import com.itxiang.evidence.entity.ResumeTemplateField;
import com.itxiang.evidence.entity.ResumeTemplatePatchDraft;
import com.itxiang.evidence.entity.ResumeTemplatePreviewPage;
import com.itxiang.evidence.entity.ResumeTemplateRegionAnnotation;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.LogEventMapper;
import com.itxiang.evidence.mapper.RagQueryHistoryMapper;
import com.itxiang.evidence.mapper.ResumeTemplateExportMapper;
import com.itxiang.evidence.mapper.ResumeTemplateFieldMapper;
import com.itxiang.evidence.mapper.ResumeTemplateMapper;
import com.itxiang.evidence.mapper.ResumeTemplatePatchDraftMapper;
import com.itxiang.evidence.mapper.ResumeTemplatePreviewPageMapper;
import com.itxiang.evidence.mapper.ResumeTemplateRegionAnnotationMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import com.itxiang.evidence.service.Impl.RagIndexWorker;
import com.itxiang.evidence.service.Impl.RagServiceImpl;
import com.itxiang.evidence.service.Impl.RagUploadWorker;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
import com.itxiang.evidence.vo.MaterialPreviewVO;
import com.itxiang.evidence.vo.RagQueryTaskVO;
import com.itxiang.evidence.vo.RagQueryVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.ResumePatchDraftVO;
import com.itxiang.evidence.vo.ResumeTemplateExportVO;
import com.itxiang.evidence.vo.ResumeTemplateVO;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class RagServiceImplTests {

    @Mock
    private LearningMaterialMapper learningMaterialMapper;

    @Mock
    private LogEventMapper logEventMapper;

    @Mock
    private RagQueryHistoryMapper ragQueryHistoryMapper;

    @Mock
    private ResumeTemplateMapper resumeTemplateMapper;

    @Mock
    private ResumeTemplateFieldMapper resumeTemplateFieldMapper;

    @Mock
    private ResumeTemplatePatchDraftMapper resumeTemplatePatchDraftMapper;

    @Mock
    private ResumeTemplateExportMapper resumeTemplateExportMapper;

    @Mock
    private ResumeTemplatePreviewPageMapper resumeTemplatePreviewPageMapper;

    @Mock
    private ResumeTemplateRegionAnnotationMapper resumeTemplateRegionAnnotationMapper;

    @Mock
    private PythonRagClient pythonRagClient;

    @Mock
    private LogService logService;

    @Mock
    private ObjectStorageService objectStorageService;

    @Mock
    private RagIndexWorker ragIndexWorker;

    @Mock
    private RagUploadWorker ragUploadWorker;

    @Mock
    private TransactionTemplate transactionTemplate;

    @Mock
    private ObjectMapper objectMapper;

    @InjectMocks
    private RagServiceImpl ragService;

    @TempDir
    private Path tempDir;

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(ragService, "chunkRootOverride", tempDir.resolve("chunks"));
        ReflectionTestUtils.setField(ragService, "objectMapper", new ObjectMapper());
        lenient().when(transactionTemplate.execute(any())).thenAnswer(invocation -> {
            TransactionCallback<?> callback = invocation.getArgument(0);
            return callback.doInTransaction(new SimpleTransactionStatus());
        });
        lenient().doAnswer(invocation -> {
            Consumer<?> callback = invocation.getArgument(0);
            @SuppressWarnings("unchecked")
            Consumer<SimpleTransactionStatus> statusConsumer = (Consumer<SimpleTransactionStatus>) callback;
            statusConsumer.accept(new SimpleTransactionStatus());
            return null;
        }).when(transactionTemplate).executeWithoutResult(any());
    }

    @Test
    void indexTextPartialResultWritesErrorLocationLog() {
        RagIndexTextDTO dto = new RagIndexTextDTO();
        dto.setTitle("视频处理记录");
        dto.setDocumentType("markdown");
        dto.setSource("manual");
        dto.setContent("视频处理失败位置测试");

        doAnswer(invocation -> {
            LearningMaterial material = invocation.getArgument(0);
            material.setId(42L);
            return null;
        }).when(learningMaterialMapper).insert(any(LearningMaterial.class));
        when(pythonRagClient.indexText(eq(42L), eq("7"), any(RagIndexTextDTO.class))).thenReturn(
                new PythonRagClient.IndexResult(
                        "material-42",
                        "视频处理记录",
                        "PARTIAL",
                        "video+bailian-asr+keyframe-ocr",
                        "部分完成",
                        3,
                        List.of("video.frame_ocr[1]: Bailian OCR returned empty text"),
                        List.of()
                )
        );

        LearningMaterialVO result = ragService.indexText(dto, "7");

        assertThat(result.getStatus()).isEqualTo("PARTIAL");
        verify(logService).recordRagError(
                eq("material"),
                eq("index"),
                eq("material_index_partial_warning"),
                eq("RAG_INDEX_PARTIAL"),
                anyString(),
                isNull(),
                argThat(context -> containsErrorLocation(context, "video.frame_ocr[1]"))
        );
    }

    @Test
    void listRecentMaterialsUsesSelectedDateRange() {
        LocalDate startDate = LocalDate.now().minusDays(2);
        LocalDate endDate = LocalDate.now().minusDays(1);
        when(learningMaterialMapper.findRecentByUserIdBetween(eq("7"), any(LocalDateTime.class), any(LocalDateTime.class), eq(10)))
                .thenReturn(List.of());

        List<LearningMaterialVO> result = ragService.listRecentMaterials("7", startDate, endDate, 10);

        assertThat(result).isEmpty();
        verify(learningMaterialMapper).findRecentByUserIdBetween(
                eq("7"),
                eq(startDate.atStartOfDay()),
                eq(endDate.plusDays(1).atStartOfDay()),
                eq(10)
        );
    }

    @Test
    void getQueryTaskUpdatesHistoryDurationFromTimestampWithTimezone() {
        RagQueryHistory existing = new RagQueryHistory();
        existing.setUserId("7");
        existing.setTaskId("task-1");
        existing.setStatus("RUNNING");
        existing.setEvidenceCount(0);
        existing.setExpandedQueriesJson("[]");
        existing.setEvidencesJson("[]");
        existing.setDiagnosticsJson("{}");
        existing.setProgressEventsJson("[]");
        existing.setCreatedAt(OffsetDateTime.parse("2026-06-20T22:19:30+08:00"));
        RagQueryTaskVO task = RagQueryTaskVO.builder()
                .taskId("task-1")
                .status("RUNNING")
                .updatedAt(LocalDateTime.of(2026, 6, 20, 22, 19, 39))
                .progressEvents(List.of())
                .build();
        when(pythonRagClient.getQueryTask(eq("task-1"))).thenReturn(task);
        when(ragQueryHistoryMapper.findByTaskIdAndUserId(eq("task-1"), eq("7"))).thenReturn(existing);

        RagQueryTaskVO result = ragService.getQueryTask("task-1", "7");

        assertThat(result.getTaskId()).isEqualTo("task-1");
        verify(ragQueryHistoryMapper).updateByTaskId(argThat(history ->
                "task-1".equals(history.getTaskId())
                        && "RUNNING".equals(history.getStatus())
                        && Integer.valueOf(9000).equals(history.getDurationMs())
        ));
    }

    @Test
    void queryNormalizesMetadataFilterAndCandidateMultiplier() {
        RagQueryDTO dto = new RagQueryDTO();
        dto.setQuestion("只查我的 RAG 资料");
        dto.setTopK(99);
        dto.setCandidateMultiplier(1);
        dto.setMetadataFilter(Map.of(
                "documentType", " markdown ",
                "source", List.of("upload", ""),
                "pageIndex", 3,
                "visibilityScope", "public",
                "userId", "other-user",
                "unknownKey", "unknown",
                "emptyKey", "",
                "emptyList", List.of()
        ));
        when(pythonRagClient.query(any(RagQueryDTO.class))).thenReturn(RagQueryVO.builder()
                .answer("基于资料回答")
                .expandedQueries(List.of())
                .evidences(List.of())
                .diagnostics(Map.of())
                .progressEvents(List.of())
                .build());
        ArgumentCaptor<RagQueryDTO> captor = ArgumentCaptor.forClass(RagQueryDTO.class);

        ragService.query(dto, "7");

        verify(pythonRagClient).query(captor.capture());
        RagQueryDTO normalized = captor.getValue();
        assertThat(normalized.getTopK()).isEqualTo(20);
        assertThat(normalized.getCandidateMultiplier()).isEqualTo(2);
        assertThat(normalized.getMetadataFilter()).containsEntry("documentType", "markdown");
        assertThat(normalized.getMetadataFilter()).containsEntry("source", List.of("upload"));
        assertThat(normalized.getMetadataFilter()).containsEntry("pageIndex", "3");
        assertThat(normalized.getMetadataFilter()).containsEntry("userId", "7");
        assertThat(normalized.getMetadataFilter()).containsEntry("visibilityScope", "private");
        assertThat(normalized.getMetadataFilter()).doesNotContainKeys("unknownKey", "emptyKey", "emptyList");
        assertThat(String.valueOf(normalized.getMetadataFilter().get("__ignoredMetadataFilterKeys")))
                .contains("unknownKey", "emptyKey", "emptyList", "userId", "visibilityScope");
    }

    @Test
    void queryRefusedWritesGuardFieldsToHistoryAndLogsBusinessSuccess() {
        RagQueryDTO dto = new RagQueryDTO();
        dto.setQuestion("酸面包二次发酵温湿度曲线");
        dto.setTopK(3);
        dto.setCandidateMultiplier(4);
        when(pythonRagClient.query(any(RagQueryDTO.class))).thenReturn(RagQueryVO.builder()
                .answer("当前知识库没有检索到足够相关的证据，无法基于个人资料回答该问题。")
                .answerStatus("REFUSED")
                .refusalReason("LOW_CONFIDENCE")
                .refusalPolicy("STRICT_EVIDENCE_GUARD_V1")
                .confidence(0.21)
                .supportingEvidenceIds(List.of())
                .refusalMessage("证据相关性不足")
                .expandedQueries(List.of("酸面包二次发酵温湿度曲线"))
                .evidences(List.of())
                .diagnostics(Map.of("answerGuard", Map.of(
                        "answerStatus", "REFUSED",
                        "refusalReason", "LOW_CONFIDENCE",
                        "confidence", 0.21
                )))
                .progressEvents(List.of())
                .build());

        RagQueryVO result = ragService.query(dto, "7");

        assertThat(result.getAnswerStatus()).isEqualTo("REFUSED");
        verify(ragQueryHistoryMapper).insert(argThat(history ->
                Integer.valueOf(0).equals(history.getEvidenceCount())
                        && history.getDiagnosticsJson().contains("\"answerStatus\":\"REFUSED\"")
                        && history.getDiagnosticsJson().contains("\"refusalReason\":\"LOW_CONFIDENCE\"")
        ));
        verify(logService).recordRagEvent(
                eq("rag_query"),
                eq("retrieve"),
                eq("rag_query_refused"),
                eq("RAG 查询完成"),
                argThat(context -> "REFUSED".equals(context.get("answerStatus"))
                        && "LOW_CONFIDENCE".equals(context.get("refusalReason"))
                        && Integer.valueOf(0).equals(context.get("supportingEvidenceCount")))
        );
    }

    @Test
    void pythonRagClientReadsGuardFieldsAndKeepsLegacyCompatibility() throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        PythonRagClient client = new PythonRagClient(new PythonRagProperties(), mapper);
        JsonNode refusedRoot = mapper.readTree("""
                {
                  "answer": "证据不足，已拒答",
                  "answerStatus": "REFUSED",
                  "refusalReason": "LOW_CONFIDENCE",
                  "refusalPolicy": "STRICT_EVIDENCE_GUARD_V1",
                  "confidence": 0.21,
                  "supportingEvidenceIds": [],
                  "refusalMessage": "证据相关性不足",
                  "expandedQueries": ["酸面包"],
                  "evidences": [],
                  "diagnostics": {
                    "answerGuard": {
                      "answerStatus": "REFUSED",
                      "refusalReason": "LOW_CONFIDENCE",
                      "confidence": 0.21
                    }
                  },
                  "progressEvents": []
                }
                """);
        JsonNode legacyRoot = mapper.readTree("""
                {
                  "answer": "旧响应回答",
                  "expandedQueries": [],
                  "evidences": [
                    {
                      "evidenceId": "chunk-1",
                      "documentId": "doc-1",
                      "title": "旧资料",
                      "snippet": "旧响应 evidence",
                      "source": "unit-test",
                      "sectionName": "全文",
                      "documentType": "markdown",
                      "score": 0.8
                    }
                  ],
                  "diagnostics": {},
                  "progressEvents": []
                }
                """);

        RagQueryVO refused = (RagQueryVO) ReflectionTestUtils.invokeMethod(client, "readQueryResult", refusedRoot);
        RagQueryVO legacy = (RagQueryVO) ReflectionTestUtils.invokeMethod(client, "readQueryResult", legacyRoot);

        assertThat(refused.getAnswerStatus()).isEqualTo("REFUSED");
        assertThat(refused.getRefusalReason()).isEqualTo("LOW_CONFIDENCE");
        assertThat(refused.getConfidence()).isEqualTo(0.21);
        assertThat(refused.getSupportingEvidenceIds()).isEmpty();
        assertThat(legacy.getAnswerStatus()).isEqualTo("ANSWERED");
        assertThat(legacy.getSupportingEvidenceIds()).containsExactly("chunk-1");
    }

    @Test
    void listQueryHistoryBackfillsGuardFieldsFromDiagnosticsJson() {
        RagQueryHistory history = new RagQueryHistory();
        history.setId(12L);
        history.setTaskId("task-refused");
        history.setUserId("7");
        history.setQuestion("酸面包问题");
        history.setAnswer("证据不足，已拒答");
        history.setStatus("COMPLETED");
        history.setTopK(5);
        history.setEvidenceCount(0);
        history.setExpandedQueriesJson("[]");
        history.setEvidencesJson("[]");
        history.setDiagnosticsJson("""
                {
                  "answerGuard": {
                    "answerStatus": "REFUSED",
                    "refusalReason": "LOW_CONFIDENCE",
                    "refusalPolicy": "STRICT_EVIDENCE_GUARD_V1",
                    "confidence": 0.21,
                    "supportingEvidenceIds": [],
                    "refusalMessage": "证据相关性不足"
                  }
                }
                """);
        history.setProgressEventsJson("[]");
        when(ragQueryHistoryMapper.findRecentByUserIdBetween(eq("7"), any(LocalDateTime.class), any(LocalDateTime.class), eq(5)))
                .thenReturn(List.of(history));

        var result = ragService.listQueryHistory("7", LocalDate.now().minusDays(1), LocalDate.now(), 5);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).getAnswerStatus()).isEqualTo("REFUSED");
        assertThat(result.get(0).getRefusalReason()).isEqualTo("LOW_CONFIDENCE");
        assertThat(result.get(0).getConfidence()).isEqualTo(0.21);
        assertThat(result.get(0).getRefusalMessage()).isEqualTo("证据相关性不足");
        assertThat(result.get(0).getSupportingEvidenceIds()).isEmpty();
    }

    @Test
    void queryNonPersistentUsesScopedQueryAndDoesNotWriteHistory() {
        RagQueryDTO dto = new RagQueryDTO();
        dto.setQuestion("Agent 只读探针查询 Redis 证据");
        dto.setTopK(3);
        dto.setCandidateMultiplier(4);
        dto.setMetadataFilter(Map.of("documentType", "markdown", "userId", "other-user"));
        when(pythonRagClient.query(any(RagQueryDTO.class))).thenReturn(RagQueryVO.builder()
                .answer("Redis 证据")
                .expandedQueries(List.of("Redis 证据"))
                .evidences(List.of())
                .diagnostics(Map.of("candidateCount", 2))
                .progressEvents(List.of())
                .build());
        ArgumentCaptor<RagQueryDTO> captor = ArgumentCaptor.forClass(RagQueryDTO.class);

        RagQueryVO result = ragService.queryNonPersistent(dto, "7");

        assertThat(result.getAnswer()).isEqualTo("Redis 证据");
        verify(pythonRagClient).query(captor.capture());
        RagQueryDTO scoped = captor.getValue();
        assertThat(scoped.getMetadataFilter()).containsEntry("userId", "7");
        assertThat(scoped.getMetadataFilter()).containsEntry("visibilityScope", "private");
        assertThat(scoped.getMetadataFilter()).containsEntry("documentType", "markdown");
        assertThat(String.valueOf(scoped.getMetadataFilter().get("__ignoredMetadataFilterKeys"))).contains("userId");
        verify(ragQueryHistoryMapper, never()).insert(any(RagQueryHistory.class));
    }

    @Test
    void previewMaterialLoadsMarkdownContentForCurrentUser() {
        LearningMaterial material = new LearningMaterial();
        material.setId(3L);
        material.setTitle("02_llm_gateway.md");
        material.setOriginalFilename("02_llm_gateway.md");
        material.setUserId("7");
        material.setDocumentType("markdown");
        material.setStorageType("oss");
        material.setOriginalFilePath("https://cdn.example.com/learning-evidence/1/markdown/02_llm_gateway.md");
        material.setObjectKey("learning-evidence/1/markdown/02_llm_gateway.md");
        material.setPublicUrl("https://cdn.example.com/learning-evidence/1/markdown/02_llm_gateway.md");
        when(learningMaterialMapper.findByIdAndUserId(eq(3L), eq("7"))).thenReturn(material);
        when(objectStorageService.load(eq("oss"), eq(material.getOriginalFilePath()), eq(material.getObjectKey()), eq("02_llm_gateway.md")))
                .thenReturn(new ObjectStorageService.LoadedObject(
                        "\uFEFF## 参考回答\nRAG 引用预览".getBytes(StandardCharsets.UTF_8),
                        "02_llm_gateway.md",
                        "application/octet-stream"
                ));

        MaterialPreviewVO result = ragService.previewMaterial(
                3L,
                "https://cdn.example.com/learning-evidence/1/markdown/02_llm_gateway.md#_2-参考回答",
                "7"
        );

        assertThat(result.getMaterialId()).isEqualTo(3L);
        assertThat(result.getTitle()).isEqualTo("02_llm_gateway.md");
        assertThat(result.getContent()).startsWith("## 参考回答");
        assertThat(result.getContentType()).isEqualTo("application/octet-stream");
    }

    @Test
    void previewMaterialRejectsSourceFromAnotherObject() {
        LearningMaterial material = new LearningMaterial();
        material.setId(3L);
        material.setTitle("02_llm_gateway.md");
        material.setOriginalFilename("02_llm_gateway.md");
        material.setUserId("7");
        material.setDocumentType("markdown");
        material.setStorageType("oss");
        material.setOriginalFilePath("https://cdn.example.com/learning-evidence/1/markdown/02_llm_gateway.md");
        material.setObjectKey("learning-evidence/1/markdown/02_llm_gateway.md");
        material.setPublicUrl("https://cdn.example.com/learning-evidence/1/markdown/02_llm_gateway.md");
        when(learningMaterialMapper.findByIdAndUserId(eq(3L), eq("7"))).thenReturn(material);

        org.junit.jupiter.api.Assertions.assertThrows(
                IllegalArgumentException.class,
                () -> ragService.previewMaterial(3L, "https://cdn.example.com/learning-evidence/9/markdown/other.md", "7")
        );

        verify(objectStorageService, never()).load(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void uploadMaterialChunkReturnsProcessingWhenAllChunksReceived() {
        MockMultipartFile chunk = new MockMultipartFile(
                "file",
                "course.mp4",
                "video/mp4",
                "hello".getBytes()
        );
        doAnswer(invocation -> {
            LearningMaterial material = invocation.getArgument(0);
            material.setId(88L);
            return null;
        }).when(learningMaterialMapper).insert(any(LearningMaterial.class));
        when(logEventMapper.findRecentProgressByMaterialId(eq(88L), eq(40))).thenReturn(List.of());
        when(logEventMapper.findVideoProgressByMaterialId(eq(88L), eq(80))).thenReturn(List.of());

        MaterialUploadChunkVO result = ragService.uploadMaterialChunk(
                chunk,
                "upload123",
                "course.mp4",
                0,
                1,
                5L,
                false,
                "7"
        );

        assertThat(result.getCompleted()).isTrue();
        assertThat(result.getStatus()).isEqualTo("PROCESSING");
        assertThat(result.getNextChunkIndex()).isEqualTo(1);
        assertThat(result.getMaterial()).isNotNull();
        assertThat(result.getMaterial().getId()).isEqualTo(88L);
        verify(ragUploadWorker).completeChunkedUpload(
                eq(88L),
                eq("7"),
                any(Path.class),
                any(Path.class),
                eq("upload123"),
                eq("course.mp4"),
                eq("video/mp4"),
                eq(1),
                eq(5L),
                eq(false)
        );
        verify(objectStorageService, never()).store(any(Path.class), anyString(), anyString(), anyString(), any());
    }

    @Test
    void uploadMaterialChunkLoadsVideoProgressEventsForFrontend() {
        MockMultipartFile chunk = new MockMultipartFile(
                "file",
                "course.mp4",
                "video/mp4",
                "hello".getBytes()
        );
        doAnswer(invocation -> {
            LearningMaterial material = invocation.getArgument(0);
            material.setId(89L);
            return null;
        }).when(learningMaterialMapper).insert(any(LearningMaterial.class));
        when(logEventMapper.findRecentProgressByMaterialId(eq(89L), eq(40))).thenReturn(List.of());
        when(logEventMapper.findVideoProgressByMaterialId(eq(89L), eq(80))).thenReturn(List.of());

        ragService.uploadMaterialChunk(
                chunk,
                "upload-video-progress",
                "course.mp4",
                0,
                1,
                5L,
                false,
                "7"
        );

        verify(logEventMapper).findRecentProgressByMaterialId(eq(89L), eq(40));
        verify(logEventMapper).findVideoProgressByMaterialId(eq(89L), eq(80));
    }

    @Test
    void uploadMaterialChunkKeepsReceivedChunksWhenUploadIncomplete() throws Exception {
        MockMultipartFile firstChunk = new MockMultipartFile(
                "file",
                "course.mp4",
                "video/mp4",
                "hello".getBytes(StandardCharsets.UTF_8)
        );

        MaterialUploadChunkVO result = ragService.uploadMaterialChunk(
                firstChunk,
                "resume-upload",
                "course.mp4",
                0,
                3,
                15L,
                false,
                "7"
        );

        Path chunkPath = tempDir.resolve("chunks").resolve("7").resolve("resume-upload").resolve("chunk-00000.part");
        assertThat(result.getCompleted()).isFalse();
        assertThat(result.getStatus()).isEqualTo("UPLOADING");
        assertThat(result.getReceivedChunks()).isEqualTo(1);
        assertThat(result.getNextChunkIndex()).isEqualTo(1);
        assertThat(Files.exists(chunkPath)).isTrue();
        verify(learningMaterialMapper, never()).insert(any(LearningMaterial.class));
        verify(ragUploadWorker, never()).completeChunkedUpload(
                any(),
                anyString(),
                any(Path.class),
                any(Path.class),
                anyString(),
                anyString(),
                anyString(),
                any(),
                any(),
                any()
        );
    }

    @Test
    void uploadMaterialChunkReusesMaterialWhenFinalChunkIsRetried() {
        MockMultipartFile chunk = new MockMultipartFile(
                "file",
                "course.mp4",
                "video/mp4",
                "hello".getBytes(StandardCharsets.UTF_8)
        );
        doAnswer(invocation -> {
            LearningMaterial material = invocation.getArgument(0);
            material.setId(90L);
            return null;
        }).when(learningMaterialMapper).insert(any(LearningMaterial.class));
        LearningMaterial existingMaterial = new LearningMaterial();
        existingMaterial.setId(90L);
        existingMaterial.setTitle("course.mp4");
        existingMaterial.setUserId("7");
        existingMaterial.setDocumentType("mp4");
        existingMaterial.setSource("upload");
        existingMaterial.setStatus("PENDING");
        when(learningMaterialMapper.findByIdAndUserId(eq(90L), eq("7"))).thenReturn(existingMaterial);
        when(logEventMapper.findRecentProgressByMaterialId(eq(90L), eq(40))).thenReturn(List.of());
        when(logEventMapper.findVideoProgressByMaterialId(eq(90L), eq(80))).thenReturn(List.of());

        MaterialUploadChunkVO firstResult = ragService.uploadMaterialChunk(
                chunk,
                "retry-final",
                "course.mp4",
                0,
                1,
                5L,
                false,
                "7"
        );
        MaterialUploadChunkVO retryResult = ragService.uploadMaterialChunk(
                chunk,
                "retry-final",
                "course.mp4",
                0,
                1,
                5L,
                false,
                "7"
        );

        assertThat(firstResult.getMaterial().getId()).isEqualTo(90L);
        assertThat(retryResult.getCompleted()).isTrue();
        assertThat(retryResult.getMaterial().getId()).isEqualTo(90L);
        assertThat(retryResult.getNextChunkIndex()).isEqualTo(1);
        verify(learningMaterialMapper, times(1)).insert(any(LearningMaterial.class));
        verify(ragUploadWorker, times(1)).completeChunkedUpload(
                eq(90L),
                eq("7"),
                any(Path.class),
                any(Path.class),
                eq("retry-final"),
                eq("course.mp4"),
                eq("video/mp4"),
                eq(1),
                eq(5L),
                eq(false)
        );
    }

    @Test
    void uploadMaterialChunkReschedulesWhenPreviousBackgroundMergeFailed() {
        MockMultipartFile chunk = new MockMultipartFile(
                "file",
                "course.mp4",
                "video/mp4",
                "hello".getBytes(StandardCharsets.UTF_8)
        );
        doAnswer(invocation -> {
            LearningMaterial material = invocation.getArgument(0);
            material.setId(91L);
            return null;
        }).when(learningMaterialMapper).insert(any(LearningMaterial.class));
        LearningMaterial failedMaterial = new LearningMaterial();
        failedMaterial.setId(91L);
        failedMaterial.setTitle("course.mp4");
        failedMaterial.setUserId("7");
        failedMaterial.setDocumentType("mp4");
        failedMaterial.setSource("upload");
        failedMaterial.setStatus("FAILED");
        failedMaterial.setParser("upload-chunk-error");
        when(learningMaterialMapper.findByIdAndUserId(eq(91L), eq("7"))).thenReturn(failedMaterial);
        when(logEventMapper.findRecentProgressByMaterialId(eq(91L), eq(40))).thenReturn(List.of());
        when(logEventMapper.findVideoProgressByMaterialId(eq(91L), eq(80))).thenReturn(List.of());

        ragService.uploadMaterialChunk(
                chunk,
                "retry-failed-final",
                "course.mp4",
                0,
                1,
                5L,
                false,
                "7"
        );
        MaterialUploadChunkVO retryResult = ragService.uploadMaterialChunk(
                chunk,
                "retry-failed-final",
                "course.mp4",
                0,
                1,
                5L,
                false,
                "7"
        );

        assertThat(retryResult.getCompleted()).isTrue();
        assertThat(retryResult.getMaterial().getStatus()).isEqualTo("PENDING");
        verify(learningMaterialMapper).updateStatus(eq(91L), eq("PENDING"));
        verify(ragUploadWorker, times(2)).completeChunkedUpload(
                eq(91L),
                eq("7"),
                any(Path.class),
                any(Path.class),
                eq("retry-failed-final"),
                eq("course.mp4"),
                eq("video/mp4"),
                eq(1),
                eq(5L),
                eq(false)
        );
    }

    @Test
    void listResumeTemplatesReturnsCurrentUserHistoryWithCurrentPath() {
        ResumeTemplate template = resumeTemplate("tpl-history", "7", 2, "EXPORTED");
        template.setCurrentFilename("resume-v2.docx");
        template.setCurrentFilePath("uploads/resume-v2.docx");
        when(resumeTemplateMapper.findRecentByUserId(eq("7"), eq(12))).thenReturn(List.of(template));
        when(resumeTemplateFieldMapper.findByTemplateIdAndVersion(eq("tpl-history"), eq(2))).thenReturn(List.of());

        List<ResumeTemplateVO> result = ragService.listResumeTemplates("7", null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).getTemplateId()).isEqualTo("tpl-history");
        assertThat(result.get(0).getFilename()).isEqualTo("resume-v2.docx");
        assertThat(result.get(0).getCurrentFilePath()).isEqualTo("uploads/resume-v2.docx");
        assertThat(result.get(0).getFieldCount()).isZero();
        assertThat(result.get(0).getUnsupportedRegionCount()).isZero();
        verify(resumeTemplateMapper).findRecentByUserId(eq("7"), eq(12));
    }

    @Test
    void generateResumeTemplatePatchesUsesRagEvidenceWithoutWritingHistory() {
        ResumeTemplate template = resumeTemplate("tpl-1", "7", 1, "READY");
        ResumePatchGenerateDTO dto = new ResumePatchGenerateDTO();
        dto.setVersion(1);
        dto.setJobDescription("需要 RAG、FastAPI 和 Spring Boot 项目经验");
        dto.setResumeMaterialId(42L);
        dto.setResumeText("前端伪造摘要不应进入 Python");
        dto.setTopK(3);
        LearningMaterial resumeMaterial = new LearningMaterial();
        resumeMaterial.setId(42L);
        resumeMaterial.setUserId("7");
        resumeMaterial.setDocumentSummary("服务端简历摘要：负责 FastAPI 检索、React 模板确认和 Java 联调。");
        when(resumeTemplateMapper.findByIdAndUserId(eq("tpl-1"), eq("7"))).thenReturn(template);
        when(resumeTemplateFieldMapper.findByTemplateIdAndVersion(eq("tpl-1"), eq(1))).thenReturn(List.of());
        when(learningMaterialMapper.findByIdAndUserId(eq(42L), eq("7"))).thenReturn(resumeMaterial);
        when(pythonRagClient.query(any(RagQueryDTO.class))).thenReturn(RagQueryVO.builder()
                .answer("命中项目证据")
                .expandedQueries(List.of("RAG FastAPI 项目"))
                .evidences(List.of(RagEvidenceVO.builder()
                        .evidenceId("ev-1")
                        .documentTitle("项目复盘")
                        .title("项目复盘")
                        .sectionName("RAG 平台")
                        .snippet("实现 FastAPI 检索接口")
                        .source("upload")
                        .sourcePath("material.md")
                        .score(0.91)
                        .build()))
                .diagnostics(Map.of())
                .progressEvents(List.of())
                .build());
        when(pythonRagClient.generateResumePatches(any())).thenReturn(new PythonRagClient.ResumePatchGenerationResult(
                "tpl-1",
                1,
                "local",
                "resume_content_patch_response",
                Map.of("type", "object"),
                List.of(Map.of(
                        "fieldId", "field-1",
                        "sourceTextHash", "hash-1",
                        "newText", "实现 FastAPI 检索接口",
                        "rewriteReason", "突出岗位要求",
                        "evidenceIds", List.of("ev-1"),
                        "confidence", 0.9,
                        "riskFlags", List.of("NONE"),
                        "status", "DRAFT"
                )),
                List.of()
        ));
        ArgumentCaptor<RagQueryDTO> queryCaptor = ArgumentCaptor.forClass(RagQueryDTO.class);
        ArgumentCaptor<Map<String, Object>> payloadCaptor = ArgumentCaptor.forClass(Map.class);

        ResumePatchDraftVO result = ragService.generateResumeTemplatePatches("tpl-1", dto, "7");

        assertThat(result.getTemplateId()).isEqualTo("tpl-1");
        assertThat(result.getPatches()).hasSize(1);
        assertThat(result.getEvidenceCandidates()).hasSize(1);
        verify(pythonRagClient).query(queryCaptor.capture());
        assertThat(queryCaptor.getValue().getQuestion()).contains("需要 RAG、FastAPI");
        assertThat(queryCaptor.getValue().getQuestion()).contains("服务端简历摘要");
        verify(pythonRagClient).generateResumePatches(payloadCaptor.capture());
        assertThat(payloadCaptor.getValue()).containsEntry("resumeText", resumeMaterial.getDocumentSummary());
        assertThat(payloadCaptor.getValue()).doesNotContainEntry("resumeText", "前端伪造摘要不应进入 Python");
        verify(ragQueryHistoryMapper, never()).insert(any(RagQueryHistory.class));
        verify(resumeTemplatePatchDraftMapper).insert(argThat(draft ->
                "tpl-1".equals(draft.getTemplateId())
                        && "7".equals(draft.getUserId())
                        && draft.getJobDescriptionHash() != null
                        && !draft.getJobDescriptionHash().isBlank()
        ));
    }

    @Test
    void generateResumeTemplatePatchesWithConfirmedAnnotationsFreezesAllowedFields() {
        ResumeTemplate template = resumeTemplate("tpl-confirmed", "7", 1, "READY");
        ResumeTemplateField allowedField = resumeField("tpl-confirmed", "7", 1, "field-allowed", "OPTIONAL");
        ResumeTemplateField lockedField = resumeField("tpl-confirmed", "7", 1, "field-locked", "OPTIONAL");
        ResumeTemplateRegionAnnotation annotation = editableAnnotation("field-allowed", "突出 RAG 平台证据", "REQUIRED");
        ResumePatchGenerateDTO dto = new ResumePatchGenerateDTO();
        dto.setVersion(1);
        dto.setJobDescription("需要 RAG 平台和 FastAPI 项目经验");
        dto.setUseConfirmedAnnotations(true);
        when(resumeTemplateMapper.findByIdAndUserId(eq("tpl-confirmed"), eq("7"))).thenReturn(template);
        when(resumeTemplateFieldMapper.findByTemplateIdAndVersion(eq("tpl-confirmed"), eq(1))).thenReturn(List.of(allowedField, lockedField));
        when(resumeTemplateRegionAnnotationMapper.findEditableBoundActive(eq("tpl-confirmed"), eq(1), eq("7"))).thenReturn(List.of(annotation));
        when(resumeTemplateRegionAnnotationMapper.maxRevision(eq("tpl-confirmed"), eq(1))).thenReturn(3);
        when(pythonRagClient.query(any(RagQueryDTO.class))).thenReturn(RagQueryVO.builder()
                .answer("命中证据")
                .expandedQueries(List.of())
                .evidences(List.of())
                .diagnostics(Map.of())
                .progressEvents(List.of())
                .build());
        when(pythonRagClient.generateResumePatches(any())).thenReturn(new PythonRagClient.ResumePatchGenerationResult(
                "tpl-confirmed",
                1,
                "local",
                "resume_content_patch_response",
                Map.of("type", "object"),
                List.of(Map.of(
                        "fieldId", "field-allowed",
                        "sourceTextHash", "hash-field-allowed",
                        "newText", "项目：RAG 平台",
                        "rewriteReason", "按图片确认区域生成",
                        "evidenceIds", List.of(),
                        "confidence", 0.8,
                        "riskFlags", List.of("NONE"),
                        "status", "DRAFT"
                )),
                List.of()
        ));
        @SuppressWarnings("unchecked")
        ArgumentCaptor<Map<String, Object>> payloadCaptor = ArgumentCaptor.forClass(Map.class);

        ResumePatchDraftVO result = ragService.generateResumeTemplatePatches("tpl-confirmed", dto, "7");

        assertThat(result.getAllowedFieldIds()).containsExactly("field-allowed");
        assertThat(result.getAnnotationRevision()).isEqualTo(3);
        verify(pythonRagClient).generateResumePatches(payloadCaptor.capture());
        Map<String, Object> payload = payloadCaptor.getValue();
        assertThat(String.valueOf(payload.get("fields"))).contains("field-allowed").doesNotContain("field-locked");
        assertThat(String.valueOf(payload.get("fieldInstructions"))).contains("突出 RAG 平台证据");
        assertThat(String.valueOf(payload.get("fieldEvidencePolicies"))).contains("REQUIRED");
        verify(resumeTemplatePatchDraftMapper).insert(argThat(draft ->
                draft.getAllowedFieldIdsJson().contains("field-allowed")
                        && Integer.valueOf(3).equals(draft.getAnnotationRevision())
        ));
    }

    @Test
    void saveResumeTemplateAnnotationsRejectsEvidencePolicyDowngrade() {
        ResumeTemplate template = resumeTemplate("tpl-policy", "7", 1, "READY");
        ResumeTemplateField field = resumeField("tpl-policy", "7", 1, "field-required", "REQUIRED");
        ResumeTemplateAnnotationSaveDTO dto = new ResumeTemplateAnnotationSaveDTO();
        dto.setVersion(1);
        ResumeTemplateAnnotationSaveDTO.AnnotationItem item = new ResumeTemplateAnnotationSaveDTO.AnnotationItem();
        item.setFieldId("field-required");
        item.setPageIndex(0);
        item.setRect(Map.of("x", 0.1, "y", 0.1, "width", 0.2, "height", 0.08));
        item.setSourceType("AUTO");
        item.setEditable(true);
        item.setSectionKey("project_experience");
        item.setRequiredEvidencePolicy("OPTIONAL");
        item.setStatus("ACTIVE");
        dto.setAnnotations(List.of(item));
        when(resumeTemplateMapper.findByIdAndUserId(eq("tpl-policy"), eq("7"))).thenReturn(template);
        when(resumeTemplateFieldMapper.findByTemplateIdAndVersion(eq("tpl-policy"), eq(1))).thenReturn(List.of(field));
        when(resumeTemplateRegionAnnotationMapper.maxRevision(eq("tpl-policy"), eq(1))).thenReturn(1);

        org.junit.jupiter.api.Assertions.assertThrows(
                IllegalArgumentException.class,
                () -> ragService.saveResumeTemplateAnnotations("tpl-policy", dto, "7")
        );

        verify(resumeTemplateRegionAnnotationMapper, never()).insert(any(ResumeTemplateRegionAnnotation.class));
        verify(resumeTemplateRegionAnnotationMapper, never()).update(any(ResumeTemplateRegionAnnotation.class));
    }

    @Test
    void exportResumeTemplateRejectsDraftBeforeConfirmation() {
        ResumeTemplate template = resumeTemplate("tpl-2", "7", 1, "READY");
        ResumeTemplatePatchDraft draft = patchDraft("draft-1", "tpl-2", "7", 1, "DRAFT");
        ResumeTemplateExportDTO dto = new ResumeTemplateExportDTO();
        dto.setVersion(1);
        dto.setPatchDraftId("draft-1");
        dto.setIdempotencyKey("export-key");
        when(resumeTemplateMapper.findByIdAndUserId(eq("tpl-2"), eq("7"))).thenReturn(template);
        when(resumeTemplateExportMapper.findByIdempotencyKey(eq("tpl-2"), eq("7"), eq("export-key"))).thenReturn(null);
        when(resumeTemplatePatchDraftMapper.findByIdAndTemplateIdAndUserId(eq("draft-1"), eq("tpl-2"), eq("7"))).thenReturn(draft);

        org.junit.jupiter.api.Assertions.assertThrows(
                IllegalArgumentException.class,
                () -> ragService.exportResumeTemplate("tpl-2", dto, "7")
        );

        verify(objectStorageService, never()).load(anyString(), anyString(), anyString(), anyString());
        verify(pythonRagClient, never()).exportResumeTemplate(any());
    }

    @Test
    void exportResumeTemplateReturnsExistingExportForIdempotencyKey() {
        ResumeTemplate template = resumeTemplate("tpl-3", "7", 1, "READY");
        ResumeTemplateExport existing = new ResumeTemplateExport();
        existing.setId("export-1");
        existing.setTemplateId("tpl-3");
        existing.setUserId("7");
        existing.setBaseVersion(1);
        existing.setExportVersion(2);
        existing.setPatchDraftId("draft-1");
        existing.setFilename("resume-v2.docx");
        existing.setFilePath("uploads/resume-v2.docx");
        existing.setStorageType("local");
        existing.setLayoutValidationJson("{\"status\":\"PASSED\"}");
        existing.setIdempotencyKey("export-key");
        existing.setStatus("EXPORTED");
        ResumeTemplateExportDTO dto = new ResumeTemplateExportDTO();
        dto.setVersion(1);
        dto.setPatchDraftId("draft-1");
        dto.setIdempotencyKey("export-key");
        when(resumeTemplateMapper.findByIdAndUserId(eq("tpl-3"), eq("7"))).thenReturn(template);
        when(resumeTemplateExportMapper.findByIdempotencyKey(eq("tpl-3"), eq("7"), eq("export-key"))).thenReturn(existing);

        ResumeTemplateExportVO result = ragService.exportResumeTemplate("tpl-3", dto, "7");

        assertThat(result.getExportId()).isEqualTo("export-1");
        assertThat(result.getExportVersion()).isEqualTo(2);
        verify(resumeTemplatePatchDraftMapper, never()).findByIdAndTemplateIdAndUserId(anyString(), anyString(), anyString());
        verify(pythonRagClient, never()).exportResumeTemplate(any());
    }

    @Test
    void generateResumeTemplatePatchesRejectsVersionConflict() {
        ResumeTemplate template = resumeTemplate("tpl-4", "7", 2, "READY");
        ResumePatchGenerateDTO dto = new ResumePatchGenerateDTO();
        dto.setVersion(1);
        dto.setJobDescription("需要 RAG 经验");
        when(resumeTemplateMapper.findByIdAndUserId(eq("tpl-4"), eq("7"))).thenReturn(template);

        org.junit.jupiter.api.Assertions.assertThrows(
                IllegalArgumentException.class,
                () -> ragService.generateResumeTemplatePatches("tpl-4", dto, "7")
        );

        verify(pythonRagClient, never()).generateResumePatches(any());
    }

    @Test
    void deleteResumeTemplateDeletesOwnedRecordAndStoredFiles() {
        ResumeTemplate template = resumeTemplate("tpl-delete", "7", 1, "READY");
        template.setOriginalFilePath(tempDir.resolve("original.docx").toString());
        template.setObjectKey(template.getOriginalFilePath());
        template.setCurrentFilePath(tempDir.resolve("current.docx").toString());
        template.setCurrentObjectKey(template.getCurrentFilePath());
        ResumeTemplatePreviewPage page = new ResumeTemplatePreviewPage();
        page.setTemplateId("tpl-delete");
        page.setStorageType("local");
        page.setFilePath(tempDir.resolve("preview.png").toString());
        page.setObjectKey(page.getFilePath());
        ResumeTemplateExport export = new ResumeTemplateExport();
        export.setTemplateId("tpl-delete");
        export.setUserId("7");
        export.setStorageType("local");
        export.setFilePath(tempDir.resolve("export.docx").toString());
        export.setObjectKey(export.getFilePath());
        when(resumeTemplateMapper.findByIdAndUserId(eq("tpl-delete"), eq("7"))).thenReturn(template);
        when(resumeTemplatePreviewPageMapper.findAllByTemplateId(eq("tpl-delete"))).thenReturn(List.of(page));
        when(resumeTemplateExportMapper.findAllByTemplateId(eq("tpl-delete"), eq("7"))).thenReturn(List.of(export));

        ragService.deleteResumeTemplate("tpl-delete", "7");

        verify(resumeTemplateMapper).deleteByIdAndUserId(eq("tpl-delete"), eq("7"));
        verify(objectStorageService).delete(eq("local"), eq(template.getOriginalFilePath()), eq(template.getObjectKey()));
        verify(objectStorageService).delete(eq("local"), eq(template.getCurrentFilePath()), eq(template.getCurrentObjectKey()));
        verify(objectStorageService).delete(eq("local"), eq(page.getFilePath()), eq(page.getObjectKey()));
        verify(objectStorageService).delete(eq("local"), eq(export.getFilePath()), eq(export.getObjectKey()));
    }

    /**
     * 校验日志上下文中包含 Python 返回的具体报错位置。
     */
    private boolean containsErrorLocation(Map<String, Object> context, String expectedLocation) {
        Object location = context.get("errorLocation");
        return location != null && location.toString().contains(expectedLocation);
    }

    /**
     * 构造测试用简历模板实体。
     */
    private ResumeTemplate resumeTemplate(String id, String userId, Integer version, String status) {
        ResumeTemplate template = new ResumeTemplate();
        template.setId(id);
        template.setUserId(userId);
        template.setTemplateName("resume.docx");
        template.setOriginalFilename("resume.docx");
        template.setCurrentFilename("resume.docx");
        template.setStorageType("local");
        template.setCurrentFilePath("uploads/resume.docx");
        template.setCurrentStorageType("local");
        template.setVersion(version);
        template.setStatus(status);
        template.setFileType("docx");
        template.setLayoutFingerprintJson("{}");
        template.setUnsupportedRegionsJson("[]");
        return template;
    }

    /**
     * 构造测试用补丁草稿实体。
     */
    private ResumeTemplatePatchDraft patchDraft(String id, String templateId, String userId, Integer version, String status) {
        ResumeTemplatePatchDraft draft = new ResumeTemplatePatchDraft();
        draft.setId(id);
        draft.setTemplateId(templateId);
        draft.setUserId(userId);
        draft.setTemplateVersion(version);
        draft.setStatus(status);
        draft.setPatchesJson("[]");
        draft.setEvidenceCandidatesJson("[]");
        draft.setValidationErrorsJson("[]");
        draft.setAllowedFieldIdsJson("[]");
        draft.setProvider("local");
        return draft;
    }

    /**
     * 构造测试用简历模板字段。
     */
    private ResumeTemplateField resumeField(String templateId, String userId, Integer version, String fieldId, String evidencePolicy) {
        ResumeTemplateField field = new ResumeTemplateField();
        field.setId("db-" + fieldId);
        field.setTemplateId(templateId);
        field.setUserId(userId);
        field.setTemplateVersion(version);
        field.setFieldId(fieldId);
        field.setSectionKey("project_experience");
        field.setDisplayName("项目经历");
        field.setSourceText("项目：RAG 平台");
        field.setSourceTextHash("hash-" + fieldId);
        field.setLocationRefsJson("[{\"partName\":\"document\",\"containerType\":\"paragraph\",\"paragraphIndex\":0,\"runStart\":0,\"runEnd\":0,\"textStart\":0,\"textEnd\":7}]");
        field.setStyleFingerprintJson("{}");
        field.setMaxChars(300);
        field.setMaxLines(3);
        field.setRequiredEvidencePolicy(evidencePolicy);
        field.setUnsupportedRegionsJson("[]");
        return field;
    }

    /**
     * 构造测试用已确认可编辑标注。
     */
    private ResumeTemplateRegionAnnotation editableAnnotation(String fieldId, String instruction, String evidencePolicy) {
        ResumeTemplateRegionAnnotation annotation = new ResumeTemplateRegionAnnotation();
        annotation.setId("ann-" + fieldId);
        annotation.setTemplateId("tpl-confirmed");
        annotation.setUserId("7");
        annotation.setTemplateVersion(1);
        annotation.setFieldId(fieldId);
        annotation.setPageIndex(0);
        annotation.setRectJson("{\"x\":0.1,\"y\":0.1,\"width\":0.2,\"height\":0.08}");
        annotation.setSourceType("AUTO");
        annotation.setEditable(true);
        annotation.setSectionKey("project_experience");
        annotation.setUserInstruction(instruction);
        annotation.setRequiredEvidencePolicy(evidencePolicy);
        annotation.setStatus("ACTIVE");
        annotation.setAnnotationRevision(3);
        return annotation;
    }
}
