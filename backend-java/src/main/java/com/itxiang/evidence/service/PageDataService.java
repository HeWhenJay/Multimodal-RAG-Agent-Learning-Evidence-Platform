package com.itxiang.evidence.service;

import com.itxiang.evidence.vo.DashboardVO;
import com.itxiang.evidence.vo.SystemSettingVO;

import java.time.LocalDate;
import java.util.List;

public interface PageDataService {

    /**
     * 获取工作台页面所需的数据库聚合数据。
     */
    DashboardVO dashboard(String userId, LocalDate startDate, LocalDate endDate, Integer recentDays, Integer recentLimit);

    /**
     * 获取系统设置展示数据。
     */
    List<SystemSettingVO> systemSettings();
}
