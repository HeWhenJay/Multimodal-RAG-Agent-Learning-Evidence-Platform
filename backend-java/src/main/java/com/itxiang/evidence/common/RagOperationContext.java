package com.itxiang.evidence.common;

import jakarta.servlet.http.HttpServletRequest;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

public final class RagOperationContext {

    private static final ThreadLocal<Deque<Operation>> OPERATIONS = ThreadLocal.withInitial(ArrayDeque::new);
    private static final ThreadLocal<Set<Integer>> LOGGED_THROWABLES = ThreadLocal.withInitial(HashSet::new);

    private RagOperationContext() {
    }

    /**
     * 打开一个当前线程内的 RAG 操作上下文。
     */
    public static Scope open(Operation operation) {
        OPERATIONS.get().push(operation);
        return new Scope(operation);
    }

    /**
     * 获取当前线程中的 RAG 操作上下文。
     */
    public static Operation current() {
        Deque<Operation> operations = OPERATIONS.get();
        return operations.isEmpty() ? unknown() : operations.peek();
    }

    /**
     * 标记异常已经写入错误日志，避免重复记录。
     */
    public static void markErrorLogged(Throwable throwable) {
        if (throwable == null) {
            return;
        }
        Set<Integer> logged = LOGGED_THROWABLES.get();
        Throwable current = throwable;
        while (current != null) {
            logged.add(System.identityHashCode(current));
            current = current.getCause();
        }
    }

    /**
     * 判断异常链中是否已有异常被记录。
     */
    public static boolean isErrorLogged(Throwable throwable) {
        if (throwable == null) {
            return false;
        }
        Set<Integer> logged = LOGGED_THROWABLES.get();
        Throwable current = throwable;
        while (current != null) {
            if (logged.contains(System.identityHashCode(current))) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    /**
     * 清理当前线程中的 RAG 操作上下文。
     */
    public static void clear() {
        OPERATIONS.remove();
        LOGGED_THROWABLES.remove();
    }

    /**
     * 创建 RAG 操作描述。
     */
    public static Operation operation(String module, String stage, String action, String displayName) {
        return new Operation(module, stage, action, displayName);
    }

    /**
     * 根据 HTTP 请求推断 RAG 操作描述。
     */
    public static Operation fromRequest(HttpServletRequest request) {
        if (request == null) {
            return unknown();
        }
        return fromRequest(request.getMethod(), request.getRequestURI());
    }

    /**
     * 根据请求方法和路径推断 RAG 操作描述。
     */
    public static Operation fromRequest(String method, String requestUri) {
        String normalizedMethod = method == null ? "" : method.toUpperCase(Locale.ROOT);
        String path = requestUri == null ? "" : requestUri.replaceAll("/+$", "");
        if (!path.startsWith("/api/rag")) {
            return unknown();
        }
        if ("GET".equals(normalizedMethod) && "/api/rag/overview".equals(path)) {
            return operation("overview", "overview", "rag_overview_query", "获取 RAG 概览");
        }
        if ("GET".equals(normalizedMethod) && "/api/rag/materials".equals(path)) {
            return operation("material", "list", "material_list_query", "获取近期学习资料");
        }
        if ("GET".equals(normalizedMethod) && path.matches("/api/rag/materials/\\d+")) {
            return operation("material", "status", "material_status_query", "查询学习资料解析状态");
        }
        if ("GET".equals(normalizedMethod) && path.matches("/api/rag/materials/\\d+/evidences")) {
            return operation("evidence", "evidence", "material_evidence_query", "查询学习资料 evidence");
        }
        if ("POST".equals(normalizedMethod) && "/api/rag/materials/text".equals(path)) {
            return operation("material", "index", "material_index_text_request", "索引文本学习资料");
        }
        if ("POST".equals(normalizedMethod) && "/api/rag/materials/upload".equals(path)) {
            return operation("material", "upload", "material_upload_request", "上传并索引学习资料");
        }
        if ("POST".equals(normalizedMethod) && "/api/rag/query".equals(path)) {
            return operation("rag_query", "retrieve", "rag_query_request", "RAG 检索问答");
        }
        return operation("rag", "request", "rag_request", "RAG 请求处理");
    }

    /**
     * 生成用于日志和错误响应的操作标签。
     */
    public static String stageLabel(Operation operation) {
        Operation safeOperation = operation == null ? unknown() : operation;
        return safeOperation.displayName()
                + " [" + safeOperation.module() + "/" + safeOperation.stage() + "/" + safeOperation.action() + "]";
    }

    /**
     * 生成面向前端的失败提示。
     */
    public static String failureMessage(Operation operation, Throwable throwable) {
        return stageLabel(operation) + " 失败：" + safeThrowableMessage(throwable);
    }

    /**
     * 获取安全的异常提示文本。
     */
    public static String safeThrowableMessage(Throwable throwable) {
        if (throwable == null || throwable.getMessage() == null || throwable.getMessage().isBlank()) {
            return "服务处理失败，请查看错误日志";
        }
        return throwable.getMessage();
    }

    /**
     * 返回兜底的未知 RAG 操作。
     */
    private static Operation unknown() {
        return operation("rag", "unknown", "rag_unknown_failure", "RAG 未知环节");
    }

    /**
     * RAG 操作描述，包含模块、阶段、动作和中文展示名。
     */
    public record Operation(String module, String stage, String action, String displayName) {
    }

    /**
     * try-with-resources 使用的上下文作用域。
     */
    public static final class Scope implements AutoCloseable {
        private final Operation operation;
        private boolean closed;

        /**
         * 绑定当前作用域对应的操作描述。
         */
        private Scope(Operation operation) {
            this.operation = operation;
        }

        /**
         * 关闭作用域并移除当前操作描述。
         */
        @Override
        public void close() {
            if (closed) {
                return;
            }
            Deque<Operation> operations = OPERATIONS.get();
            if (!operations.isEmpty() && operations.peek().equals(operation)) {
                operations.pop();
            } else {
                operations.remove(operation);
            }
            closed = true;
        }
    }
}
