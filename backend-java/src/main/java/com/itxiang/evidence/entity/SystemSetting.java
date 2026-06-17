package com.itxiang.evidence.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class SystemSetting {

    private String settingKey;
    private String settingGroup;
    private String label;
    private String settingValue;
    private Integer sortOrder;
    private LocalDateTime updatedAt;
}
