package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.LogErrorCreateDTO;
import com.itxiang.evidence.dto.LogEventCreateDTO;
import com.itxiang.evidence.vo.LogErrorVO;
import com.itxiang.evidence.vo.LogEventVO;
import com.itxiang.evidence.vo.LogOverviewVO;

import java.util.List;
import java.util.Map;

public interface LogService {

    /**
     * 写入单条业务事件日志。
     */
    Long recordEvent(LogEventCreateDTO dto);

    /**
     * 批量写入业务事件日志。
     */
    Integer recordEvents(List<LogEventCreateDTO> dtoList);

    /**
     * 写入或聚合同类错误日志。
     */
    Long recordError(LogErrorCreateDTO dto);

    /**
     * 写入 RAG 业务状态事件。
     */
    void recordRagEvent(String module, String stage, String action, String message, Map<String, Object> context);

    /**
     * 写入 RAG 用户可见进度事件。
     */
    void recordRagProgress(String module,
                           String stage,
                           String action,
                           String message,
                           Map<String, Object> context,
                           Boolean success);

    /**
     * 写入 RAG 错误日志并标记异常已记录。
     */
    void recordRagError(String module,
                        String stage,
                        String action,
                        String errorCode,
                        String message,
                        Throwable throwable,
                        Map<String, Object> context);

    /**
     * 查询最近业务事件日志。
     */
    List<LogEventVO> listRecentEvents(Integer limit);

    /**
     * 查询最近错误日志。
     */
    List<LogErrorVO> listRecentErrors(Integer limit);

    /**
     * 统计指定天数内的日志概览。
     */
    LogOverviewVO overview(Integer days);
}
