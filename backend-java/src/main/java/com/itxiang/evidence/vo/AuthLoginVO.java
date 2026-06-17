package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class AuthLoginVO {

    private String token;
    private LocalDateTime expiresAt;
    private AuthUserVO user;
}
