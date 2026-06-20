package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.entity.RagQueryHistory;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.LogEventMapper;
import com.itxiang.evidence.mapper.RagQueryHistoryMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import com.itxiang.evidence.service.Impl.RagIndexWorker;
import com.itxiang.evidence.service.Impl.RagServiceImpl;
import com.itxiang.evidence.service.Impl.RagUploadWorker;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
import com.itxiang.evidence.vo.RagQueryTaskVO;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.api.extension.ExtendWith;
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

    /**
     * 校验日志上下文中包含 Python 返回的具体报错位置。
     */
    private boolean containsErrorLocation(Map<String, Object> context, String expectedLocation) {
        Object location = context.get("errorLocation");
        return location != null && location.toString().contains(expectedLocation);
    }
}
