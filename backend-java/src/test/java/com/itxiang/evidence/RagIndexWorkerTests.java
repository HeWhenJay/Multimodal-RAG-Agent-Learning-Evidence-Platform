package com.itxiang.evidence;

import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.ObjectStorageService;
import com.itxiang.evidence.service.Impl.RagIndexWorker;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.SimpleTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.List;
import java.util.function.Consumer;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RagIndexWorkerTests {

    @Test
    void textFallbackCallsPythonAndWritesIndexResult() {
        LearningMaterialMapper materialMapper = mock(LearningMaterialMapper.class);
        PythonRagClient pythonRagClient = mock(PythonRagClient.class);
        LogService logService = mock(LogService.class);
        TransactionTemplate transactionTemplate = mock(TransactionTemplate.class);
        RagIndexWorker worker = new RagIndexWorker(
                materialMapper,
                pythonRagClient,
                logService,
                mock(ObjectStorageService.class),
                transactionTemplate
        );
        LearningMaterial material = new LearningMaterial();
        material.setId(71L);
        material.setUserId("7");
        material.setTitle("HTTP fallback 文本");
        material.setStatus("PARSING");
        RagIndexTextDTO dto = new RagIndexTextDTO();
        dto.setContent("Kafka 不可用时仍可通过 HTTP 索引");
        when(materialMapper.findByIdAndUserId(71L, "7")).thenReturn(material);
        when(pythonRagClient.indexText(71L, "7", dto)).thenReturn(new PythonRagClient.IndexResult(
                "material-71", "HTTP fallback 文本", "READY", "unit-parser", "索引完成", 2, List.of(), List.of()
        ));
        doAnswer(invocation -> {
            @SuppressWarnings("unchecked")
            Consumer<SimpleTransactionStatus> callback = (Consumer<SimpleTransactionStatus>) invocation.getArgument(0);
            callback.accept(new SimpleTransactionStatus());
            return null;
        }).when(transactionTemplate).executeWithoutResult(any());

        worker.indexTextMaterial(71L, "7", dto);

        verify(pythonRagClient).indexText(71L, "7", dto);
        verify(materialMapper).updateIndexResult(71L, "READY", "unit-parser", "索引完成", 2);
        verify(logService).recordRagEvent(eq("material"), eq("index"), eq("material_text_index_async_result"), any(), any());
    }
}
