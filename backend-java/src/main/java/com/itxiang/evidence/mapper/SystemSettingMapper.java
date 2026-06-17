package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.SystemSetting;
import org.apache.ibatis.annotations.Mapper;

import java.util.List;

@Mapper
public interface SystemSettingMapper {

    /**
     * 查询全部系统设置展示项。
     */
    List<SystemSetting> findAll();
}
