package com.itxiang.evidence.vo;

import lombok.Builder;
import lombok.Data;

import java.util.LinkedHashMap;
import java.util.Map;

@Data
@Builder
public class AgentToolResultVO {

    private String taskId;
    private String toolCallId;
    private String toolName;
    private String status;
    private Boolean ownershipVerified;
    private String scope;
    private Object data;
    private Map<String, Object> diagnostics;
    private String errorCode;
    private String errorMessage;
    private Boolean retryable;

    /**
     * 构造内部工具调用失败响应。
     */
    public static AgentToolResultVO failed(String taskId,
                                           String toolCallId,
                                           String toolName,
                                           String errorCode,
                                           String errorMessage,
                                           boolean retryable) {
        return AgentToolResultVO.builder()
                .taskId(taskId)
                .toolCallId(toolCallId)
                .toolName(toolName)
                .status("REJECTED")
                .ownershipVerified(false)
                .scope("current_user_or_authorized")
                .diagnostics(new LinkedHashMap<>())
                .errorCode(errorCode)
                .errorMessage(errorMessage)
                .retryable(retryable)
                .build();
    }
}
