package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AuthLoginRecord;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface AuthLoginRecordMapper {

    /**
     * 新增登录尝试记录。
     */
    void insert(AuthLoginRecord record);
}
