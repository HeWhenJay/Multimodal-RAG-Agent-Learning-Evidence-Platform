package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AuthSession;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;

@Mapper
public interface AuthSessionMapper {

    /**
     * 新增登录会话。
     */
    void insert(AuthSession session);

    /**
     * 按令牌哈希查询未过期且未撤销的会话。
     */
    AuthSession findActiveByTokenHash(@Param("tokenHash") String tokenHash,
                                      @Param("now") LocalDateTime now);

    /**
     * 按令牌哈希撤销会话。
     */
    void revokeByTokenHash(@Param("tokenHash") String tokenHash);
}
