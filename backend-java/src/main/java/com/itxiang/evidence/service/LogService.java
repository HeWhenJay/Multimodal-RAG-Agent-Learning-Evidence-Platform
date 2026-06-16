package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.LogErrorCreateDTO;
import com.itxiang.evidence.dto.LogEventCreateDTO;
import com.itxiang.evidence.vo.LogErrorVO;
import com.itxiang.evidence.vo.LogEventVO;
import com.itxiang.evidence.vo.LogOverviewVO;

import java.util.List;
import java.util.Map;

public interface LogService {

    Long recordEvent(LogEventCreateDTO dto);

    Integer recordEvents(List<LogEventCreateDTO> dtoList);

    Long recordError(LogErrorCreateDTO dto);

    void recordRagEvent(String module, String stage, String action, String message, Map<String, Object> context);

    void recordRagError(String module,
                        String stage,
                        String action,
                        String errorCode,
                        String message,
                        Throwable throwable,
                        Map<String, Object> context);

    List<LogEventVO> listRecentEvents(Integer limit);

    List<LogErrorVO> listRecentErrors(Integer limit);

    LogOverviewVO overview(Integer days);
}
