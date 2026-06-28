package com.itxiang.evidence;

import com.itxiang.evidence.common.GlobalExceptionHandler;
import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.service.LogService;
import org.junit.jupiter.api.Test;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.mockito.Mockito.mock;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class GlobalExceptionHandlerTests {

    @Test
    void methodNotSupportedReturnsPathAwareMessage() throws Exception {
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new PostOnlyController())
                .setControllerAdvice(new GlobalExceptionHandler(mock(LogService.class)))
                .build();

        mockMvc.perform(get("/api/agent/tasks"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.msg").value("请求方法不支持：GET /api/agent/tasks"));
    }

    @RestController
    static class PostOnlyController {

        /**
         * 仅用于测试 GET 命中 POST 接口时的错误提示。
         */
        @PostMapping("/api/agent/tasks")
        Result<Void> create() {
            return Result.success(null);
        }
    }
}
