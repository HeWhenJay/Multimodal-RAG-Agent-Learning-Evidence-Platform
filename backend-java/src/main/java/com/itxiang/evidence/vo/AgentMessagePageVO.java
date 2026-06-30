package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.util.List;

@Data
@Builder
public class AgentMessagePageVO {

    private String taskId;
    private List<AgentChatMessageVO> messages;
    private Long oldestSequenceNo;
    private Long newestSequenceNo;
    private Boolean hasMoreBefore;
    private Boolean hasMoreAfter;
    private Integer limit;
}
