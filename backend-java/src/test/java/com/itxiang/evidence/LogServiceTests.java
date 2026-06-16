package com.itxiang.evidence;

import com.itxiang.evidence.dto.LogErrorCreateDTO;
import com.itxiang.evidence.dto.LogEventCreateDTO;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.vo.LogErrorVO;
import com.itxiang.evidence.vo.LogEventVO;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
class LogServiceTests {

    @Autowired
    private LogService logService;

    @Test
    void recordEventPersistsSanitizedContext() {
        LogEventCreateDTO dto = new LogEventCreateDTO();
        dto.setSource("java");
        dto.setDomain("rag");
        dto.setModule("material");
        dto.setStage("index");
        dto.setAction("material_index_file_result");
        dto.setMessage("index finished");
        dto.setMaterialId(1L);
        dto.setDocumentId("material-1");

        Map<String, Object> context = new LinkedHashMap<>();
        context.put("question", "should not be stored");
        context.put("chunkCount", 3);
        dto.setContext(context);

        Long id = logService.recordEvent(dto);

        List<LogEventVO> events = logService.listRecentEvents(5);
        assertThat(id).isNotNull();
        assertThat(events).anySatisfy(event -> {
            assertThat(event.getId()).isEqualTo(id);
            assertThat(event.getDomain()).isEqualTo("rag");
            assertThat(event.getContextJson()).contains("\"question\":\"***\"");
            assertThat(event.getContextJson()).contains("\"chunkCount\":3");
        });
    }

    @Test
    void recordErrorAggregatesSameFingerprint() {
        LogErrorCreateDTO first = errorDto();
        LogErrorCreateDTO second = errorDto();

        Long firstId = logService.recordError(first);
        Long secondId = logService.recordError(second);

        List<LogErrorVO> errors = logService.listRecentErrors(5);
        assertThat(secondId).isEqualTo(firstId);
        assertThat(errors).anySatisfy(error -> {
            assertThat(error.getId()).isEqualTo(firstId);
            assertThat(error.getErrorCode()).isEqualTo("RAG_PYTHON_TIMEOUT");
            assertThat(error.getOccurrenceCount()).isGreaterThanOrEqualTo(2);
        });
    }

    private LogErrorCreateDTO errorDto() {
        LogErrorCreateDTO dto = new LogErrorCreateDTO();
        dto.setSource("java");
        dto.setDomain("rag");
        dto.setModule("rag_query");
        dto.setStage("retrieve");
        dto.setAction("rag_query_failed");
        dto.setErrorType("PythonRagClientException");
        dto.setErrorCode("RAG_PYTHON_TIMEOUT");
        dto.setMessage("Python RAG call timed out for material 12345");
        dto.setStackTrace("java.lang.IllegalStateException: timeout\n\tat com.itxiang.evidence.client.PythonRagClient.query(PythonRagClient.java:1)");
        return dto;
    }
}
