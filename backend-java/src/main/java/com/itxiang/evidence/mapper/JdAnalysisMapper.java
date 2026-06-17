package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.JdAnalysisReport;
import com.itxiang.evidence.entity.JdAnalysisSkill;
import com.itxiang.evidence.entity.JdLearningPlanItem;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface JdAnalysisMapper {

    /**
     * 查询最近一次 JD 分析报告。
     */
    JdAnalysisReport findLatestReportByUserId(@Param("userId") String userId);

    /**
     * 新增 JD 分析报告。
     */
    void insertReport(JdAnalysisReport report);

    /**
     * 新增 JD 技能匹配项。
     */
    void insertSkill(JdAnalysisSkill skill);

    /**
     * 新增 JD 学习计划项。
     */
    void insertPlanItem(JdLearningPlanItem item);

    /**
     * 查询 JD 分析报告关联的技能项。
     */
    List<JdAnalysisSkill> findSkillsByReportId(@Param("reportId") Long reportId);

    /**
     * 查询 JD 分析报告关联的学习计划。
     */
    List<JdLearningPlanItem> findPlanByReportId(@Param("reportId") Long reportId);
}
