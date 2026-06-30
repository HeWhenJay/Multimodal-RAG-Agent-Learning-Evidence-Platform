package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.entity.SystemSetting;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.mapper.SystemSettingMapper;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.PageDataService;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.DashboardVO;
import com.itxiang.evidence.vo.LogOverviewVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.SystemSettingVO;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class PageDataServiceImpl implements PageDataService {

    private final RagService ragService;
    private final LogService logService;
    private final LearningMaterialMapper learningMaterialMapper;
    private final SystemSettingMapper systemSettingMapper;

    /**
     * 聚合工作台统计、最近资料和系统错误概览。
     */
    @Override
    public DashboardVO dashboard(String userId, LocalDate startDate, LocalDate endDate, Integer recentDays, Integer recentLimit) {
        LocalDateTime sevenDaysAgo = LocalDateTime.now().minusDays(7);
        RecentTaskQuery recentTaskQuery = normalizeRecentTaskQuery(startDate, endDate, recentDays, recentLimit);
        RagOverviewVO ragOverview = ragService.overview(userId);
        LogOverviewVO logOverview = logService.overview(30);
        return DashboardVO.builder()
                .materialCount(defaultLong(ragOverview.getMaterialCount()))
                .materialDelta7Days(defaultLong(learningMaterialMapper.countSinceByUserId(userId, sevenDaysAgo)))
                .evidenceCount(defaultInt(ragOverview.getChunkCount()))
                .openErrorCount(defaultLong(logOverview.getOpenErrorCount()))
                .errorCount30Days(defaultLong(logOverview.getErrorCount()))
                .recentTaskStartDate(recentTaskQuery.startDate().toString())
                .recentTaskEndDate(recentTaskQuery.endDate().toString())
                .recentTaskLimit(recentTaskQuery.limit())
                .recentMaterials(ragService.listRecentMaterials(userId, recentTaskQuery.startDate(), recentTaskQuery.endDate(), recentTaskQuery.limit()))
                .build();
    }

    /**
     * 查询系统设置展示项。
     */
    @Override
    public List<SystemSettingVO> systemSettings() {
        return systemSettingMapper.findAll().stream()
                .map(this::toSystemSettingVO)
                .toList();
    }

    /**
     * 转换系统设置展示对象。
     */
    private SystemSettingVO toSystemSettingVO(SystemSetting setting) {
        return SystemSettingVO.builder()
                .key(setting.getSettingKey())
                .group(setting.getSettingGroup())
                .label(setting.getLabel())
                .value(setting.getSettingValue())
                .sortOrder(setting.getSortOrder())
                .build();
    }

    /**
     * 归一化工作台近期任务筛选条件，保证查询始终落在最近 7 天内。
     */
    private RecentTaskQuery normalizeRecentTaskQuery(LocalDate startDate, LocalDate endDate, Integer recentDays, Integer recentLimit) {
        int safeDays = recentDays == null ? 7 : Math.max(1, Math.min(recentDays, 7));
        int safeLimit = recentLimit == null ? 5 : Math.max(1, Math.min(recentLimit, 50));
        LocalDate today = LocalDate.now();
        LocalDate earliestDate = today.minusDays(6);
        LocalDate safeEndDate = endDate == null ? today : clampDate(endDate, earliestDate, today);
        LocalDate safeStartDate = startDate == null ? safeEndDate.minusDays(safeDays - 1L) : clampDate(startDate, earliestDate, today);
        if (safeStartDate.isAfter(safeEndDate)) {
            safeStartDate = safeEndDate;
        }
        return new RecentTaskQuery(safeStartDate, safeEndDate, safeLimit);
    }

    /**
     * 将日期限制在允许范围内。
     */
    private LocalDate clampDate(LocalDate value, LocalDate minDate, LocalDate maxDate) {
        if (value.isBefore(minDate)) {
            return minDate;
        }
        if (value.isAfter(maxDate)) {
            return maxDate;
        }
        return value;
    }

    /**
     * 为 Long 空值提供 0 默认值。
     */
    private Long defaultLong(Long value) {
        return value == null ? 0L : value;
    }

    /**
     * 为 Integer 空值提供 0 默认值。
     */
    private Integer defaultInt(Integer value) {
        return value == null ? 0 : value;
    }

    private record RecentTaskQuery(LocalDate startDate, LocalDate endDate, Integer limit) {
    }
}
