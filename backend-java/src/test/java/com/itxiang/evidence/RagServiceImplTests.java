package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.dto.ResumePatchGenerateDTO;
import com.itxiang.evidence.dto.ResumeTemplateExportDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagQueryHistory;
import com.itxiang.evidence.entity.ResumeTemplate;
import com.itxiang.evidence.entity.ResumeTemplateExport;
import com.itxiang.evidence.entity.ResumeTemplatePatchDraft;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.LogEventMapper;
import com.itxiang.evidence.mapper.RagQueryHistoryMapper;
import com.itxiang.evidence.mapper.ResumeTemplateExportMapper;
import com.itxiang.evidence.mapper.ResumeTemplateFieldMapper;
import com.itxiang.evidence.mapper.ResumeTemplateMapper;
import com.itxiang.evidence.mapper.ResumeTemplatePatchDraftMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import com.itxiang.evidence.service.Impl.RagIndexWorker;
import com.itxiang.evidence.service.Impl.RagServiceImpl;
import com.itxiang.evidence.service.Impl.RagUploadWorker;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagQueryTaskVO;
import com.itxiang.evidence.vo.RagQueryVO;
import com.itxiang.evidence.vo.ResumePatchDraftVO;
import com.itxiang.evidence.vo.ResumeTemplateExportVO;
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
    void generateResumeTemplatePatchesUsesRagEvidenceWithoutWritingHistory() {
        ResumeTemplate template = resumeTemplate("tpl-1", "7", 1, "READY");
        ResumePatchGenerateDTO dto = new ResumePatchGenerateDTO();
        dto.setVersion(1);
        dto.setJobDescription("需要 RAG、FastAPI 和 Spring Boot 项目经验");
        dto.setTopK(3);
        when(resumeTemplateMapper.findByIdAndUserId(eq("tpl-1"), eq("7"))).thenReturn(template);
        when(resumeTemplateFieldMapper.findByTemplateIdAndVersion(eq("tpl-1"), eq(1))).thenReturn(List.of());
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

        ResumePatchDraftVO result = ragService.generateResumeTemplatePatches("tpl-1", dto, "7");

        assertThat(result.getTemplateId()).isEqualTo("tpl-1");
        assertThat(result.getPatches()).hasSize(1);
        assertThat(result.getEvidenceCandidates()).hasSize(1);
        verify(pythonRagClient).query(queryCaptor.capture());
        assertThat(queryCaptor.getValue().getQuestion()).contains("需要 RAG、FastAPI");
        verify(ragQueryHistoryMapper, never()).insert(any(RagQueryHistory.class));
        verify(resumeTemplatePatchDraftMapper).insert(argThat(draft ->
                "tpl-1".equals(draft.getTemplateId())
                        && "7".equals(draft.getUserId())
                        && draft.getJobDescriptionHash() != null
                        && !draft.getJobDescriptionHash().isBlank()
        ));
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
        draft.setJobDescriptionHash("hash");
        draft.setPatchesJson("[]");
        draft.setEvidenceCandidatesJson("[]");
        draft.setValidationErrorsJson("[]");
        draft.setProvider("local");
        return draft;
    }
}
