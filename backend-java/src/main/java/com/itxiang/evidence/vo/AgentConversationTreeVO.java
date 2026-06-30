package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.util.List;

@Data
@Builder
public class AgentConversationTreeVO {

    private AgentConversationFolderVO unfiled;
    private List<AgentConversationFolderVO> folders;
}
