package com.itxiang.evidence;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.itxiang.evidence.config.LogProperties;
import com.itxiang.evidence.dto.LogEventCreateDTO;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.LogErrorMapper;
import com.itxiang.evidence.mapper.LogEventMapper;
import com.itxiang.evidence.service.Impl.LogServiceImpl;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class LogServiceImplTests {

    @Mock
    private LogEventMapper logEventMapper;

    @Mock
    private LogErrorMapper logErrorMapper;

    @Mock
    private LearningMaterialMapper learningMaterialMapper;

    @Mock
    private LogProperties logProperties;

    private LogServiceImpl logService;

    @BeforeEach
    void setUp() {
        logService = new LogServiceImpl(
                logEventMapper,
                logErrorMapper,
                learningMaterialMapper,
                new ObjectMapper(),
                logProperties
        );
    }

    @Test
    void runningRagProgressSyncsMaterialBackToParsing() {
        enableLogs();
        LogEventCreateDTO dto = progressEvent("embedding.chunk", "RUNNING", Map.of(
                "materialId", 42,
                "documentId", "material-42",
                "status", "RUNNING",
                "currentChunk", 4230,
                "totalChunks", 4873,
                "percent", 81
        ));

        logService.recordEvent(dto);

        verify(learningMaterialMapper).updateProgressStatus(eq(42L), eq("PARSING"), eq(null), eq(null));
    }

    @Test
    void completedRagProgressSyncsFinalMaterialStatus() {
        enableLogs();
        LogEventCreateDTO dto = progressEvent("index.completed", "COMPLETED", Map.of(
                "materialId", 42,
                "documentId", "material-42",
                "status", "COMPLETED",
                "parseStatus", "READY",
                "parser", "video+bailian-asr+keyframe-ocr",
                "chunkCount", 4873
        ));

        logService.recordEvent(dto);

        verify(learningMaterialMapper).updateProgressStatus(
                eq(42L),
                eq("READY"),
                eq("video+bailian-asr+keyframe-ocr"),
                eq(4873)
        );
    }

    @Test
    void nonRagProgressDoesNotSyncMaterialStatus() {
        enableLogs();
        LogEventCreateDTO dto = new LogEventCreateDTO();
        dto.setModule("material");
        dto.setAction("business_event");
        dto.setDomain("rag");
        dto.setEventType("business_state");
        dto.setMaterialId(42L);

        logService.recordEvent(dto);

        verify(learningMaterialMapper, never()).updateProgressStatus(any(), any(), any(), any());
    }

    /**
     * 构造 Python 回调的 RAG 进度事件。
     */
    private LogEventCreateDTO progressEvent(String stage, String status, Map<String, Object> context) {
        LogEventCreateDTO dto = new LogEventCreateDTO();
        dto.setSource("python");
        dto.setDomain("rag");
        dto.setModule("material");
        dto.setStage(stage);
        dto.setEventType("rag_progress");
        dto.setAction("rag_progress_" + stage.replace(".", "_"));
        dto.setMessage("RAG 进度");
        dto.setSuccess(!"FAILED".equals(status));
        dto.setContext(context);
        return dto;
    }

    /**
     * 开启日志并使用默认长度配置。
     */
    private void enableLogs() {
        when(logProperties.getEnabled()).thenReturn(true);
        when(logProperties.getMaxContextBytes()).thenReturn(20480);
    }
}
