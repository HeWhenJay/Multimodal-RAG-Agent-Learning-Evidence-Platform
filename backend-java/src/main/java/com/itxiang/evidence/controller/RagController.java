package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.RagOperationContext;
import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.service.AuthService;
import com.itxiang.evidence.service.LogService;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialPreviewVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.RagQueryHistoryVO;
import com.itxiang.evidence.vo.RagQueryTaskVO;
import com.itxiang.evidence.vo.RagQueryVO;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;
import java.time.LocalDate;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/rag")
@Tag(name = "RAG", description = "多模态学习证据库 RAG 接口")
public class RagController {

    private final RagService ragService;
    private final LogService logService;
    private final AuthService authService;

    /**
     * 获取 RAG 资料与证据概览。
     */
    @GetMapping("/overview")
    @Operation(summary = "获取 RAG 概览")
    public Result<RagOverviewVO> overview(@RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("获取 RAG 概览");
        return execute(
                RagOperationContext.operation("overview", "overview", "rag_overview_query", "获取 RAG 概览"),
                () -> ragService.overview(currentUserId(authorization))
        );
    }

    /**
     * 获取最近学习资料列表。
     */
    @GetMapping("/materials")
    @Operation(summary = "获取近期学习资料")
    public Result<List<LearningMaterialVO>> materials(@RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("获取近期学习资料列表");
        return execute(
                RagOperationContext.operation("material", "list", "material_list_query", "获取近期学习资料"),
                () -> ragService.listRecentMaterials(currentUserId(authorization))
        );
    }

    /**
     * 查询单个学习资料的解析状态。
     */
    @GetMapping("/materials/{id}")
    @Operation(summary = "查询学习资料解析状态")
    public Result<LearningMaterialVO> material(@PathVariable Long id,
                                               @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("查询学习资料解析状态: id={}", id);
        return execute(
                RagOperationContext.operation("material", "status", "material_status_query", "查询学习资料解析状态"),
                context("materialId", id),
                () -> ragService.getMaterial(id, currentUserId(authorization))
        );
    }

    /**
     * 查询指定学习资料的证据片段。
     */
    @GetMapping("/materials/{id}/evidences")
    @Operation(summary = "查询学习资料 evidence")
    public Result<List<RagEvidenceVO>> materialEvidences(@PathVariable Long id,
                                                         @RequestHeader(value = "Authorization", required = false) String authorization,
                                                         @RequestParam(defaultValue = "20") Integer limit) {
        log.info("查询学习资料 evidence: id={}, limit={}", id, limit);
        return execute(
                RagOperationContext.operation("evidence", "evidence", "material_evidence_query", "查询学习资料 evidence"),
                context("materialId", id, "limit", limit),
                () -> ragService.listMaterialEvidences(id, currentUserId(authorization), limit)
        );
    }

    /**
     * 读取文本类学习资料内容，用于新标签预览。
     */
    @GetMapping("/materials/{id}/preview")
    @Operation(summary = "预览学习资料文本内容")
    public Result<MaterialPreviewVO> materialPreview(@PathVariable Long id,
                                                     @RequestHeader(value = "Authorization", required = false) String authorization,
                                                     @RequestParam(value = "source", required = false) String source) {
        log.info("预览学习资料文本内容: id={}", id);
        return execute(
                RagOperationContext.operation("material", "preview", "material_preview_query", "预览学习资料文本内容"),
                context("materialId", id),
                () -> ragService.previewMaterial(id, source, currentUserId(authorization))
        );
    }

    /**
     * 索引用户直接粘贴的文本学习资料。
     */
    @PostMapping("/materials/text")
    @Operation(summary = "索引文本学习资料")
    public Result<LearningMaterialVO> indexText(@Valid @RequestBody RagIndexTextDTO dto,
                                                @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("索引文本学习资料: title={}, documentType={}", dto.getTitle(), dto.getDocumentType());
        return execute(
                RagOperationContext.operation("material", "index", "material_index_text_request", "索引文本学习资料"),
                context("title", dto.getTitle(), "documentType", dto.getDocumentType()),
                () -> ragService.indexText(dto, currentUserId(authorization))
        );
    }

    /**
     * 保存上传文件并交给 Python RAG 服务解析入库。
     */
    @PostMapping("/materials/upload")
    @Operation(summary = "上传并索引学习资料")
    public Result<LearningMaterialVO> uploadMaterial(@RequestParam("file") MultipartFile file,
                                                     @RequestHeader(value = "Authorization", required = false) String authorization,
                                                     @RequestParam(value = "highPrecision", defaultValue = "false") Boolean highPrecision) {
        log.info("上传学习资料: filename={}, size={}, highPrecision={}",
                file.getOriginalFilename(), file.getSize(), highPrecision);
        return execute(
                RagOperationContext.operation("material", "upload", "material_upload_request", "上传并索引学习资料"),
                context(
                        "filename", file.getOriginalFilename() == null ? "" : file.getOriginalFilename(),
                        "fileSize", file.getSize(),
                        "highPrecision", Boolean.TRUE.equals(highPrecision)
                ),
                () -> ragService.uploadMaterial(file, highPrecision, currentUserId(authorization))
        );
    }

    /**
     * 接收学习资料分片，全部分片到齐后合并并触发索引。
     */
    @PostMapping("/materials/upload/chunk")
    @Operation(summary = "分片上传并索引学习资料")
    public Result<MaterialUploadChunkVO> uploadMaterialChunk(@RequestParam("file") MultipartFile file,
                                                             @RequestHeader(value = "Authorization", required = false) String authorization,
                                                             @RequestParam(value = "uploadId", required = false) String uploadId,
                                                             @RequestParam("filename") String filename,
                                                             @RequestParam("chunkIndex") Integer chunkIndex,
                                                             @RequestParam("totalChunks") Integer totalChunks,
                                                             @RequestParam("totalSize") Long totalSize,
                                                             @RequestParam(value = "highPrecision", defaultValue = "false") Boolean highPrecision) {
        log.info("分片上传学习资料: uploadId={}, filename={}, chunkIndex={}, totalChunks={}, chunkSize={}",
                uploadId, filename, chunkIndex, totalChunks, file.getSize());
        return execute(
                RagOperationContext.operation("material", "upload", "material_upload_chunk_request", "分片上传并索引学习资料"),
                context(
                        "uploadId", uploadId,
                        "filename", filename,
                        "chunkIndex", chunkIndex,
                        "totalChunks", totalChunks,
                        "chunkSize", file.getSize(),
                        "totalSize", totalSize,
                        "highPrecision", Boolean.TRUE.equals(highPrecision)
                ),
                () -> ragService.uploadMaterialChunk(
                        file,
                        uploadId,
                        filename,
                        chunkIndex,
                        totalChunks,
                        totalSize,
                        highPrecision,
                        currentUserId(authorization)
                )
        );
    }

    /**
     * 重新读取原始文件并重建学习资料索引。
     */
    @PostMapping("/materials/{id}/reindex")
    @Operation(summary = "重建学习资料索引")
    public Result<LearningMaterialVO> reindexMaterial(@PathVariable Long id,
                                                      @RequestHeader(value = "Authorization", required = false) String authorization,
                                                      @RequestParam(value = "highPrecision", defaultValue = "false") Boolean highPrecision) {
        log.info("重建学习资料索引: id={}, highPrecision={}", id, highPrecision);
        return execute(
                RagOperationContext.operation("material", "reindex", "material_reindex_request", "重建学习资料索引"),
                context(
                        "materialId", id,
                        "highPrecision", Boolean.TRUE.equals(highPrecision)
                ),
                () -> ragService.reindexMaterial(id, highPrecision, currentUserId(authorization))
        );
    }

    /**
     * 执行 RAG 检索问答。
     */
    @PostMapping("/query")
    @Operation(summary = "RAG 检索问答")
    public Result<RagQueryVO> query(@Valid @RequestBody RagQueryDTO dto,
                                    @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("执行 RAG 检索问答: question={}, topK={}", dto.getQuestion(), dto.getTopK());
        return execute(
                RagOperationContext.operation("rag_query", "retrieve", "rag_query_request", "RAG 检索问答"),
                context(
                        "questionLength", dto.getQuestion() == null ? 0 : dto.getQuestion().length(),
                        "topK", dto.getTopK() == null ? 5 : dto.getTopK()
                ),
                () -> ragService.query(dto, currentUserId(authorization))
        );
    }

    /**
     * 查询当前用户最近几次 RAG 询问历史。
     */
    @GetMapping("/query/history")
    @Operation(summary = "查询 RAG 询问历史")
    public Result<List<RagQueryHistoryVO>> queryHistory(@RequestHeader(value = "Authorization", required = false) String authorization,
                                                       @RequestParam(required = false) LocalDate startDate,
                                                       @RequestParam(required = false) LocalDate endDate,
                                                       @RequestParam(defaultValue = "5") Integer limit) {
        log.info("查询 RAG 询问历史: startDate={}, endDate={}, limit={}", startDate, endDate, limit);
        return execute(
                RagOperationContext.operation("rag_query", "history", "rag_query_history_query", "查询 RAG 询问历史"),
                context("startDate", startDate, "endDate", endDate, "limit", limit),
                () -> ragService.listQueryHistory(currentUserId(authorization), startDate, endDate, limit)
        );
    }

    /**
     * 创建 RAG 检索问答任务，前端随后轮询读取阶段详情。
     */
    @PostMapping("/query/tasks")
    @Operation(summary = "创建 RAG 检索问答任务")
    public Result<RagQueryTaskVO> startQueryTask(@Valid @RequestBody RagQueryDTO dto,
                                                 @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("创建 RAG 检索问答任务: question={}, topK={}", dto.getQuestion(), dto.getTopK());
        return execute(
                RagOperationContext.operation("rag_query", "retrieve", "rag_query_task_request", "创建 RAG 检索问答任务"),
                context(
                        "questionLength", dto.getQuestion() == null ? 0 : dto.getQuestion().length(),
                        "topK", dto.getTopK() == null ? 5 : dto.getTopK()
                ),
                () -> ragService.startQueryTask(dto, currentUserId(authorization))
        );
    }

    /**
     * 轮询 RAG 检索问答任务状态。
     */
    @GetMapping("/query/tasks/{taskId}")
    @Operation(summary = "查询 RAG 检索问答任务状态")
    public Result<RagQueryTaskVO> getQueryTask(@PathVariable String taskId,
                                               @RequestHeader(value = "Authorization", required = false) String authorization) {
        log.info("查询 RAG 检索问答任务状态: taskId={}", taskId);
        return execute(
                RagOperationContext.operation("rag_query", "retrieve", "rag_query_task_poll", "查询 RAG 检索问答任务状态"),
                context("taskId", taskId),
                () -> ragService.getQueryTask(taskId, currentUserId(authorization))
        );
    }

    private String currentUserId(String authorization) {
        return String.valueOf(authService.currentUser(bearerToken(authorization)).getId());
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
     * 在 RAG 操作上下文中执行控制器逻辑。
     */
    private <T> Result<T> execute(RagOperationContext.Operation operation, Supplier<T> supplier) {
        return execute(operation, Map.of(), supplier);
    }

    /**
     * 执行 RAG 操作并在失败时统一记录错误日志。
     */
    private <T> Result<T> execute(RagOperationContext.Operation operation,
                                  Map<String, Object> context,
                                  Supplier<T> supplier) {
        try (RagOperationContext.Scope ignored = RagOperationContext.open(operation)) {
            return Result.success(supplier.get());
        } catch (Exception e) {
            log.warn("{} 失败: {}", RagOperationContext.stageLabel(operation), e.getMessage());
            if (!RagOperationContext.isErrorLogged(e)) {
                logService.recordRagError(
                        operation.module(),
                        operation.stage(),
                        operation.action(),
                        fallbackErrorCode(e),
                        RagOperationContext.stageLabel(operation) + " 失败",
                        e,
                        failureContext(operation, context)
                );
            }
            return Result.error(RagOperationContext.failureMessage(operation, e));
        } finally {
            RagOperationContext.clear();
        }
    }

    /**
     * 将键值对列表组装为日志上下文。
     */
    private Map<String, Object> context(Object... entries) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index + 1 < entries.length; index += 2) {
            result.put(String.valueOf(entries[index]), entries[index + 1]);
        }
        return result;
    }

    /**
     * 组装控制器失败时的补充上下文。
     */
    private Map<String, Object> failureContext(RagOperationContext.Operation operation, Map<String, Object> context) {
        Map<String, Object> result = new LinkedHashMap<>(context == null ? Map.of() : context);
        result.put("failureStageLabel", RagOperationContext.stageLabel(operation));
        result.put("controller", "RagController");
        return result;
    }

    /**
     * 将异常类型映射为 RAG 错误码。
     */
    private String fallbackErrorCode(Exception e) {
        if (e instanceof IllegalArgumentException) {
            return "RAG_REQUEST_INVALID";
        }
        return "RAG_UNEXPECTED_ERROR";
    }
}
