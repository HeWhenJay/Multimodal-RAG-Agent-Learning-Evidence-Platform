package com.itxiang.evidence.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class AgentConversationFolderCreateDTO {

    @NotBlank(message = "文件夹名称不能为空")
    private String name;

    private Integer sortOrder;
}
