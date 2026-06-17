package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class AuthSession {

    private Long id;
    private Long userId;
    private String tokenHash;
    private Boolean rememberMe;
    private LocalDateTime expiresAt;
    private Boolean revoked;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
