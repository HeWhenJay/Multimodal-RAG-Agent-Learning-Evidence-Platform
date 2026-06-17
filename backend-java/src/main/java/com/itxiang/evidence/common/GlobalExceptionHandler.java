package com.itxiang.evidence.common;

import com.itxiang.evidence.service.LogService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolationException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.BindException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.multipart.MultipartException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.stream.Collectors;

@Slf4j
@RestControllerAdvice
@RequiredArgsConstructor
public class GlobalExceptionHandler {

    private final LogService logService;

    /**
     * 处理请求体参数校验失败。
     */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public Result<Void> handleMethodArgumentNotValid(MethodArgumentNotValidException e,
                                                     HttpServletRequest request) {
        return handleBadRequest(e, request, firstFieldError(e), "RAG_REQUEST_INVALID");
    }

    /**
     * 处理绑定参数校验失败。
     */
    @ExceptionHandler(BindException.class)
    public Result<Void> handleBindException(BindException e, HttpServletRequest request) {
        String message = e.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getDefaultMessage() == null ? error.getField() + " 参数不合法" : error.getDefaultMessage())
                .findFirst()
                .orElse("请求参数不合法");
        return handleBadRequest(e, request, message, "RAG_REQUEST_INVALID");
    }

    /**
     * 处理约束校验失败。
     */
    @ExceptionHandler(ConstraintViolationException.class)
    public Result<Void> handleConstraintViolation(ConstraintViolationException e,
                                                  HttpServletRequest request) {
        String message = e.getConstraintViolations().stream()
                .map(violation -> violation.getMessage() == null ? "请求参数不合法" : violation.getMessage())
                .findFirst()
                .orElse("请求参数不合法");
        return handleBadRequest(e, request, message, "RAG_REQUEST_INVALID");
    }

    /**
     * 处理 Spring 方法级校验失败。
     */
    @ExceptionHandler(HandlerMethodValidationException.class)
    public Result<Void> handleHandlerMethodValidation(HandlerMethodValidationException e,
                                                      HttpServletRequest request) {
        String message = e.getAllErrors().stream()
                .map(error -> error.getDefaultMessage() == null ? "请求参数不合法" : error.getDefaultMessage())
                .collect(Collectors.joining("; "));
        return handleBadRequest(e, request, message.isBlank() ? "请求参数不合法" : message, "RAG_REQUEST_INVALID");
    }

    /**
     * 处理请求缺参、类型错误、JSON 不可读和 multipart 异常。
     */
    @ExceptionHandler({
            MissingServletRequestParameterException.class,
            MissingServletRequestPartException.class,
            MethodArgumentTypeMismatchException.class,
            HttpMessageNotReadableException.class,
            MultipartException.class
    })
    public Result<Void> handleRequestException(Exception e, HttpServletRequest request) {
        return handleBadRequest(e, request, RagOperationContext.safeThrowableMessage(e), "RAG_REQUEST_INVALID");
    }

    /**
     * 处理未被业务代码捕获的异常。
     */
    @ExceptionHandler(Exception.class)
    public Result<Void> handleException(Exception e, HttpServletRequest request) {
        RagOperationContext.Operation operation = RagOperationContext.fromRequest(request);
        if (isRagRequest(request)) {
            log.error("{} 未捕获异常: {}", RagOperationContext.stageLabel(operation), e.getMessage(), e);
            recordRagErrorIfNeeded(operation, e, "RAG_UNEXPECTED_ERROR", request);
            RagOperationContext.clear();
            return Result.error(RagOperationContext.failureMessage(operation, e));
        }
        log.error("请求处理失败: {}", e.getMessage(), e);
        RagOperationContext.clear();
        return Result.error(RagOperationContext.safeThrowableMessage(e));
    }

    /**
     * 统一处理请求参数错误，并在 RAG 请求中记录错误日志。
     */
    private Result<Void> handleBadRequest(Exception e,
                                          HttpServletRequest request,
                                          String message,
                                          String errorCode) {
        RagOperationContext.Operation operation = RagOperationContext.fromRequest(request);
        if (isRagRequest(request)) {
            log.warn("{} 请求参数错误: {}", RagOperationContext.stageLabel(operation), message);
            recordRagErrorIfNeeded(operation, e, errorCode, request);
            RagOperationContext.clear();
            return Result.error(RagOperationContext.stageLabel(operation) + " 失败：" + message);
        }
        RagOperationContext.clear();
        return Result.error(message);
    }

    /**
     * 未记录过的 RAG 异常才写入错误日志。
     */
    private void recordRagErrorIfNeeded(RagOperationContext.Operation operation,
                                        Exception e,
                                        String errorCode,
                                        HttpServletRequest request) {
        if (RagOperationContext.isErrorLogged(e)) {
            return;
        }
        logService.recordRagError(
                operation.module(),
                operation.stage(),
                operation.action(),
                errorCode,
                RagOperationContext.stageLabel(operation) + " 失败",
                e,
                requestContext(operation, request)
        );
    }

    /**
     * 构造异常请求上下文。
     */
    private Map<String, Object> requestContext(RagOperationContext.Operation operation, HttpServletRequest request) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("failureStageLabel", RagOperationContext.stageLabel(operation));
        if (request != null) {
            context.put("httpMethod", request.getMethod());
            context.put("requestPath", request.getRequestURI());
        }
        context.put("handler", "GlobalExceptionHandler");
        return context;
    }

    /**
     * 判断当前请求是否属于 RAG API。
     */
    private boolean isRagRequest(HttpServletRequest request) {
        return request != null
                && request.getRequestURI() != null
                && request.getRequestURI().startsWith("/api/rag");
    }

    /**
     * 提取第一个字段校验错误提示。
     */
    private String firstFieldError(MethodArgumentNotValidException e) {
        return e.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getDefaultMessage() == null ? error.getField() + " 参数不合法" : error.getDefaultMessage())
                .findFirst()
                .orElse("请求参数不合法");
    }
}
