package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class SystemSettingVO {

    private String key;
    private String group;
    private String label;
    private String value;
    private Integer sortOrder;
}
