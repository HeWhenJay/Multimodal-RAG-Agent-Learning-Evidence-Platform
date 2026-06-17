package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class AppUser {

    private Long id;
    private String account;
    private String email;
    private String displayName;
    private String role;
    private String passwordHash;
    private String passwordSalt;
    private String passwordAlgorithm;
    private Integer passwordIterations;
    private String status;
    private LocalDateTime lastLoginAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
