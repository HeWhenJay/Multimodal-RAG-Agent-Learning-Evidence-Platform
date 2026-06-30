package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;
import java.util.List;

@Data
@Builder
public class AgentConversationFolderVO {

    private String id;
    private String name;
    private Integer sortOrder;
    private Integer conversationCount;
    private List<AgentTaskVO> conversations;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
}
