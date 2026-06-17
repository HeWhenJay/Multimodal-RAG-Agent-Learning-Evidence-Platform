package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Builder
public class AuthUserVO {

    private Long id;
    private String account;
    private String displayName;
    private String email;
    private String role;
    private LocalDateTime loginAt;
}
