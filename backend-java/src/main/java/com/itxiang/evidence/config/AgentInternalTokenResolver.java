package com.itxiang.evidence.config;

import lombok.extern.slf4j.Slf4j;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.SecureRandom;
import java.util.Base64;

@Slf4j
public final class AgentInternalTokenResolver {

    private static final String TOKEN_FILE_ENV = "EVIDENCE_AGENT_INTERNAL_TOKEN_FILE";
    private static final String TOKEN_FILE_PROPERTY = "evidence.agent.internal-token-file";
    private static final String LOCAL_TOKEN_FILE = ".local/agent-internal-token";
    private static final int TOKEN_BYTES = 32;
    private static final SecureRandom SECURE_RANDOM = new SecureRandom();

    private AgentInternalTokenResolver() {
    }

    /**
     * 解析 Agent 内部共享令牌；显式配置优先，本地开发自动使用仓库私有文件。
     */
    public static String resolve(String configuredToken) {
        if (configuredToken != null && !configuredToken.isBlank()) {
            return configuredToken.trim();
        }
        try {
            return readOrCreateLocalToken(resolveTokenFile());
        } catch (Exception e) {
            log.warn("Agent 内部令牌本地兜底失败: path={}, reason={}", resolveTokenFile(), e.getMessage());
            return "";
        }
    }

    /**
     * 解析本地共享令牌文件路径，支持测试或部署脚本显式覆盖。
     */
    public static Path resolveTokenFile() {
        String configuredPath = System.getProperty(TOKEN_FILE_PROPERTY);
        if (configuredPath == null || configuredPath.isBlank()) {
            configuredPath = System.getenv(TOKEN_FILE_ENV);
        }
        if (configuredPath != null && !configuredPath.isBlank()) {
            return Path.of(configuredPath).toAbsolutePath().normalize();
        }
        return resolveRepositoryRoot().resolve(LOCAL_TOKEN_FILE).toAbsolutePath().normalize();
    }

    /**
     * 读取本地共享令牌；文件不存在时原子创建，避免 Java/Python 首次同时启动生成不同值。
     */
    private static String readOrCreateLocalToken(Path tokenFile) throws IOException {
        if (Files.exists(tokenFile)) {
            String existing = Files.readString(tokenFile, StandardCharsets.UTF_8).trim();
            if (!existing.isBlank()) {
                return existing;
            }
        }
        Files.createDirectories(tokenFile.getParent());
        String generated = newToken();
        try {
            Files.writeString(
                    tokenFile,
                    generated + System.lineSeparator(),
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE_NEW,
                    StandardOpenOption.WRITE
            );
            log.info("已生成本地 Agent 内部共享令牌文件: path={}", tokenFile);
            return generated;
        } catch (FileAlreadyExistsException e) {
            String existing = Files.readString(tokenFile, StandardCharsets.UTF_8).trim();
            if (!existing.isBlank()) {
                return existing;
            }
            Files.writeString(
                    tokenFile,
                    generated + System.lineSeparator(),
                    StandardCharsets.UTF_8,
                    StandardOpenOption.TRUNCATE_EXISTING,
                    StandardOpenOption.WRITE
            );
            log.info("已修复空的本地 Agent 内部共享令牌文件: path={}", tokenFile);
            return generated;
        }
    }

    /**
     * 从当前工作目录向上查找仓库根目录，找不到时退回当前目录。
     */
    private static Path resolveRepositoryRoot() {
        Path current = Path.of("").toAbsolutePath().normalize();
        Path cursor = current;
        while (cursor != null) {
            if (Files.exists(cursor.resolve(".git"))
                    || (Files.isDirectory(cursor.resolve("backend-java")) && Files.isDirectory(cursor.resolve("ai-python")))) {
                return cursor;
            }
            cursor = cursor.getParent();
        }
        return current;
    }

    /**
     * 生成不可预测的服务间共享令牌。
     */
    private static String newToken() {
        byte[] bytes = new byte[TOKEN_BYTES];
        SECURE_RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
}
