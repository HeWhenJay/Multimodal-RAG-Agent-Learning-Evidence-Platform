package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.AuthLoginDTO;
import com.itxiang.evidence.vo.AuthLoginVO;
import com.itxiang.evidence.vo.AuthUserVO;

public interface AuthService {

    /**
     * 校验账号密码并创建登录会话。
     */
    AuthLoginVO login(AuthLoginDTO dto, String ipAddress, String userAgent);

    /**
     * 根据会话令牌获取当前登录用户。
     */
    AuthUserVO currentUser(String token);

    /**
     * 撤销当前登录会话。
     */
    void logout(String token);
}
