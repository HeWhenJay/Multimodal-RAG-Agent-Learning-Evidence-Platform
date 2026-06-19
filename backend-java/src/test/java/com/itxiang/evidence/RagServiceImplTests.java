package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.LogEventMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import com.itxiang.evidence.service.Impl.RagIndexWorker;
import com.itxiang.evidence.service.Impl.RagServiceImpl;
import com.itxiang.evidence.service.Impl.RagUploadWorker;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockMultipartFile;

import java.nio.file.Path;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class RagServiceImplTests {

    @Mock
    private LearningMaterialMapper learningMaterialMapper;

    @Mock
    private LogEventMapper logEventMapper;

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
    private ObjectMapper objectMapper;

    @InjectMocks
    private RagServiceImpl ragService;

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

    /**
     * 校验日志上下文中包含 Python 返回的具体报错位置。
     */
    private boolean containsErrorLocation(Map<String, Object> context, String expectedLocation) {
        Object location = context.get("errorLocation");
        return location != null && location.toString().contains(expectedLocation);
    }
}
