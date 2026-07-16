package com.itxiang.evidence;

import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.service.Impl.RagHttpFallbackTaskDispatcher;
import com.itxiang.evidence.service.Impl.RagIndexWorker;
import com.itxiang.evidence.service.Impl.RagUploadWorker;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

class RagHttpFallbackTaskDispatcherTests {

    @AfterEach
    void clearTransactionState() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
        TransactionSynchronizationManager.setActualTransactionActive(false);
    }

    @Test
    void textFallbackStartsOnlyAfterTransactionCommit() {
        RagIndexWorker ragIndexWorker = mock(RagIndexWorker.class);
        RagHttpFallbackTaskDispatcher dispatcher = new RagHttpFallbackTaskDispatcher(ragIndexWorker, mock(RagUploadWorker.class));
        LearningMaterial material = new LearningMaterial();
        material.setId(61L);
        RagIndexTextDTO dto = new RagIndexTextDTO();
        dto.setContent("事务提交后索引");
        TransactionSynchronizationManager.initSynchronization();
        TransactionSynchronizationManager.setActualTransactionActive(true);

        dispatcher.publishTextIndex(material, "7", dto);

        verifyNoInteractions(ragIndexWorker);
        List<TransactionSynchronization> synchronizations = TransactionSynchronizationManager.getSynchronizations();
        assertThat(synchronizations).hasSize(1);
        synchronizations.forEach(TransactionSynchronization::afterCommit);
        verify(ragIndexWorker).indexTextMaterial(eq(61L), eq("7"), eq(dto));
    }
}
