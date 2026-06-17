package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AppUser;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface AppUserMapper {

    /**
     * 按账号查询登录用户。
     */
    AppUser findByAccount(@Param("account") String account);

    /**
     * 按用户 ID 查询登录用户。
     */
    AppUser findById(@Param("id") Long id);

    /**
     * 更新用户最后登录时间。
     */
    void updateLastLoginAt(@Param("id") Long id, @Param("loginAt") java.time.LocalDateTime loginAt);
}
