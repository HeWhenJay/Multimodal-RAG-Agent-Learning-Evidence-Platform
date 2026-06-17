package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.dto.AuthLoginDTO;
import com.itxiang.evidence.service.AuthService;
import com.itxiang.evidence.vo.AuthLoginVO;
import com.itxiang.evidence.vo.AuthUserVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/auth")
@Tag(name = "登录认证", description = "登录认证接口")
public class AuthController {

    private final AuthService authService;

    /**
     * 使用账号密码登录并写入登录会话。
     */
    @PostMapping("/login")
    @Operation(summary = "账号密码登录")
    public Result<AuthLoginVO> login(@Valid @RequestBody AuthLoginDTO dto, HttpServletRequest request) {
        try {
            return Result.success(authService.login(dto, clientIp(request), request.getHeader("User-Agent")));
        } catch (IllegalArgumentException e) {
            log.warn("登录失败: account={}, reason={}", dto.getAccount(), e.getMessage());
            return Result.error(e.getMessage());
        }
    }

    /**
     * 根据 Bearer Token 查询当前登录用户。
     */
    @GetMapping("/me")
    @Operation(summary = "获取当前登录用户")
    public Result<AuthUserVO> me(@RequestHeader(value = "Authorization", required = false) String authorization) {
        try {
            return Result.success(authService.currentUser(bearerToken(authorization)));
        } catch (IllegalArgumentException e) {
            return Result.error(e.getMessage());
        }
    }

    /**
     * 退出登录并撤销当前会话。
     */
    @PostMapping("/logout")
    @Operation(summary = "退出登录")
    public Result<Void> logout(@RequestHeader(value = "Authorization", required = false) String authorization) {
        authService.logout(bearerToken(authorization));
        return Result.success();
    }

    /**
     * 从 Authorization 头中提取 Bearer Token。
     */
    private String bearerToken(String authorization) {
        if (authorization == null || authorization.isBlank()) {
            return null;
        }
        String prefix = "Bearer ";
        return authorization.startsWith(prefix) ? authorization.substring(prefix.length()).trim() : authorization.trim();
    }

    /**
     * 获取客户端 IP，优先读取代理透传的 X-Forwarded-For。
     */
    private String clientIp(HttpServletRequest request) {
        String forwardedFor = request.getHeader("X-Forwarded-For");
        if (forwardedFor != null && !forwardedFor.isBlank()) {
            return forwardedFor.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
}
