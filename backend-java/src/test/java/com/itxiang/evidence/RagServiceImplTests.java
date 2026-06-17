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
import com.itxiang.evidence.vo.LearningMaterialVO;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doAnswer;
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

    /**
     * 校验日志上下文中包含 Python 返回的具体报错位置。
     */
    private boolean containsErrorLocation(Map<String, Object> context, String expectedLocation) {
        Object location = context.get("errorLocation");
        return location != null && location.toString().contains(expectedLocation);
    }
}
