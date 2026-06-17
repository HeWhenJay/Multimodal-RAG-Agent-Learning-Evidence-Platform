package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class AuthLoginRecord {

    private Long id;
    private Long userId;
    private String account;
    private Boolean success;
    private String failureReason;
    private String ipAddress;
    private String userAgent;
    private LocalDateTime createdAt;
}
