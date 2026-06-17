package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.dto.AuthLoginDTO;
import com.itxiang.evidence.entity.AppUser;
import com.itxiang.evidence.entity.AuthLoginRecord;
import com.itxiang.evidence.entity.AuthSession;
import com.itxiang.evidence.mapper.AppUserMapper;
import com.itxiang.evidence.mapper.AuthLoginRecordMapper;
import com.itxiang.evidence.mapper.AuthSessionMapper;
import com.itxiang.evidence.service.AuthService;
import com.itxiang.evidence.vo.AuthLoginVO;
import com.itxiang.evidence.vo.AuthUserVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Locale;

@Slf4j
@Service
@RequiredArgsConstructor
public class AuthServiceImpl implements AuthService {

    private static final String ACTIVE_STATUS = "ACTIVE";
    private static final String INVALID_LOGIN_MESSAGE = "账号或密码错误";
    private static final int SESSION_HOURS = 12;
    private static final int REMEMBER_DAYS = 30;
    private static final int TOKEN_BYTES = 32;
    private static final int HASH_BITS = 256;

    private final AppUserMapper appUserMapper;
    private final AuthSessionMapper authSessionMapper;
    private final AuthLoginRecordMapper authLoginRecordMapper;
    private final SecureRandom secureRandom = new SecureRandom();

    /**
     * 校验账号密码，创建会话并记录登录结果。
     */
    @Override
    @Transactional(noRollbackFor = IllegalArgumentException.class)
    public AuthLoginVO login(AuthLoginDTO dto, String ipAddress, String userAgent) {
        String account = normalizeAccount(dto.getAccount());
        AppUser user = appUserMapper.findByAccount(account);
        if (user == null) {
            recordLogin(null, account, false, INVALID_LOGIN_MESSAGE, ipAddress, userAgent);
            throw new IllegalArgumentException(INVALID_LOGIN_MESSAGE);
        }
        if (!ACTIVE_STATUS.equalsIgnoreCase(user.getStatus())) {
            recordLogin(user, account, false, "账号已停用", ipAddress, userAgent);
            throw new IllegalArgumentException("账号已停用");
        }
        if (!passwordMatches(dto.getPassword(), user)) {
            recordLogin(user, account, false, INVALID_LOGIN_MESSAGE, ipAddress, userAgent);
            throw new IllegalArgumentException(INVALID_LOGIN_MESSAGE);
        }

        LocalDateTime loginAt = LocalDateTime.now();
        boolean remember = Boolean.TRUE.equals(dto.getRemember());
        LocalDateTime expiresAt = remember ? loginAt.plusDays(REMEMBER_DAYS) : loginAt.plusHours(SESSION_HOURS);
        String token = newToken();

        AuthSession session = new AuthSession();
        session.setUserId(user.getId());
        session.setTokenHash(sha256Hex(token));
        session.setRememberMe(remember);
        session.setExpiresAt(expiresAt);
        session.setRevoked(false);
        authSessionMapper.insert(session);
        appUserMapper.updateLastLoginAt(user.getId(), loginAt);
        recordLogin(user, account, true, null, ipAddress, userAgent);

        log.info("用户登录成功: userId={}, account={}", user.getId(), account);
        user.setLastLoginAt(loginAt);
        return AuthLoginVO.builder()
                .token(token)
                .expiresAt(expiresAt)
                .user(toUserVO(user, loginAt))
                .build();
    }

    /**
     * 根据令牌查找当前有效会话和用户信息。
     */
    @Override
    public AuthUserVO currentUser(String token) {
        AuthSession session = activeSession(token);
        AppUser user = appUserMapper.findById(session.getUserId());
        if (user == null || !ACTIVE_STATUS.equalsIgnoreCase(user.getStatus())) {
            throw new IllegalArgumentException("登录状态已失效");
        }
        return toUserVO(user, user.getLastLoginAt());
    }

    /**
     * 撤销指定令牌对应的会话。
     */
    @Override
    @Transactional
    public void logout(String token) {
        if (token == null || token.isBlank()) {
            return;
        }
        authSessionMapper.revokeByTokenHash(sha256Hex(token));
    }

    /**
     * 查询有效登录会话，不存在时抛出登录失效错误。
     */
    private AuthSession activeSession(String token) {
        if (token == null || token.isBlank()) {
            throw new IllegalArgumentException("登录状态已失效");
        }
        AuthSession session = authSessionMapper.findActiveByTokenHash(sha256Hex(token), LocalDateTime.now());
        if (session == null) {
            throw new IllegalArgumentException("登录状态已失效");
        }
        return session;
    }

    /**
     * 使用用户表中的 PBKDF2 参数校验密码。
     */
    private boolean passwordMatches(String password, AppUser user) {
        try {
            String algorithm = defaultText(user.getPasswordAlgorithm(), "PBKDF2WithHmacSHA256");
            int iterations = user.getPasswordIterations() == null ? 120000 : user.getPasswordIterations();
            PBEKeySpec spec = new PBEKeySpec(
                    password.toCharArray(),
                    defaultText(user.getPasswordSalt(), "").getBytes(StandardCharsets.UTF_8),
                    iterations,
                    HASH_BITS
            );
            byte[] encoded = SecretKeyFactory.getInstance(algorithm).generateSecret(spec).getEncoded();
            spec.clearPassword();
            byte[] actual = HexFormat.of().formatHex(encoded).getBytes(StandardCharsets.UTF_8);
            byte[] expected = defaultText(user.getPasswordHash(), "").getBytes(StandardCharsets.UTF_8);
            return MessageDigest.isEqual(expected, actual);
        } catch (Exception e) {
            log.warn("密码校验失败: userId={}, reason={}", user.getId(), e.getMessage());
            return false;
        }
    }

    /**
     * 保存一次登录尝试记录。
     */
    private void recordLogin(AppUser user,
                             String account,
                             boolean success,
                             String failureReason,
                             String ipAddress,
                             String userAgent) {
        AuthLoginRecord record = new AuthLoginRecord();
        record.setUserId(user == null ? null : user.getId());
        record.setAccount(account);
        record.setSuccess(success);
        record.setFailureReason(truncate(failureReason, 255));
        record.setIpAddress(truncate(ipAddress, 80));
        record.setUserAgent(truncate(userAgent, 500));
        authLoginRecordMapper.insert(record);
    }

    /**
     * 将用户实体转换为登录用户视图。
     */
    private AuthUserVO toUserVO(AppUser user, LocalDateTime loginAt) {
        return AuthUserVO.builder()
                .id(user.getId())
                .account(user.getAccount())
                .displayName(user.getDisplayName())
                .email(user.getEmail())
                .role(user.getRole())
                .loginAt(loginAt)
                .build();
    }

    /**
     * 生成不可预测的会话令牌。
     */
    private String newToken() {
        byte[] bytes = new byte[TOKEN_BYTES];
        secureRandom.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    /**
     * 计算令牌的 SHA-256 哈希，数据库只保存哈希值。
     */
    private String sha256Hex(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }

    /**
     * 规范化账号输入，保证大小写不影响登录。
     */
    private String normalizeAccount(String account) {
        return defaultText(account, "").trim().toLowerCase(Locale.ROOT);
    }

    /**
     * 为空文本提供默认值。
     */
    private String defaultText(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }

    /**
     * 截断过长文本。
     */
    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
