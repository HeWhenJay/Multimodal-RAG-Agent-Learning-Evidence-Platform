package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class MaterialPreviewVO {

    private Long materialId;
    private String title;
    private String documentType;
    private String source;
    private String contentType;
    private String content;
}
