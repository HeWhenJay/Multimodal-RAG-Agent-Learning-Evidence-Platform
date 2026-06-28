package com.itxiang.evidence;

import com.itxiang.evidence.config.AgentInternalTokenResolver;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class AgentInternalTokenResolverTests {

    @TempDir
    private Path tempDir;

    @Test
    void explicitTokenHasPriorityOverLocalFile() {
        String resolved = AgentInternalTokenResolver.resolve(" configured-token ");

        assertThat(resolved).isEqualTo("configured-token");
    }

    @Test
    void emptyTokenReadsOrCreatesLocalSharedFile() throws Exception {
        Path tokenFile = tempDir.resolve("agent-internal-token");
        System.setProperty("evidence.agent.internal-token-file", tokenFile.toString());
        try {
            String first = AgentInternalTokenResolver.resolve("");
            String second = AgentInternalTokenResolver.resolve("");

            assertThat(first).isNotBlank();
            assertThat(second).isEqualTo(first);
            assertThat(Files.readString(tokenFile)).contains(first);
        } finally {
            System.clearProperty("evidence.agent.internal-token-file");
        }
    }
}
