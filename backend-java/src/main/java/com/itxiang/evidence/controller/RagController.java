package com.itxiang.evidence.controller;

import com.itxiang.evidence.common.Result;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagOverviewVO;
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
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/rag")
@Tag(name = "RAG", description = "多模态学习证据库 RAG 接口")
public class RagController {

    private final RagService ragService;

    @GetMapping("/overview")
    @Operation(summary = "获取 RAG 概览")
    public Result<RagOverviewVO> overview() {
        log.info("获取 RAG 概览");
        return Result.success(ragService.overview());
    }

    @GetMapping("/materials")
    @Operation(summary = "获取近期学习资料")
    public Result<List<LearningMaterialVO>> materials() {
        log.info("获取近期学习资料列表");
        return Result.success(ragService.listRecentMaterials());
    }

    @GetMapping("/materials/{id}")
    @Operation(summary = "查询学习资料解析状态")
    public Result<LearningMaterialVO> material(@PathVariable Long id) {
        try {
            log.info("查询学习资料解析状态: id={}", id);
            return Result.success(ragService.getMaterial(id));
        } catch (Exception e) {
            log.warn("查询学习资料解析状态失败: {}", e.getMessage());
            return Result.error(e.getMessage());
        }
    }

    @GetMapping("/materials/{id}/evidences")
    @Operation(summary = "查询学习资料 evidence")
    public Result<List<RagEvidenceVO>> materialEvidences(@PathVariable Long id,
                                                         @RequestParam(defaultValue = "20") Integer limit) {
        try {
            log.info("查询学习资料 evidence: id={}, limit={}", id, limit);
            return Result.success(ragService.listMaterialEvidences(id, limit));
        } catch (Exception e) {
            log.warn("查询学习资料 evidence 失败: {}", e.getMessage());
            return Result.error(e.getMessage());
        }
    }

    @PostMapping("/materials/text")
    @Operation(summary = "索引文本学习资料")
    public Result<LearningMaterialVO> indexText(@Valid @RequestBody RagIndexTextDTO dto) {
        try {
            log.info("索引文本学习资料: title={}, documentType={}", dto.getTitle(), dto.getDocumentType());
            return Result.success(ragService.indexText(dto));
        } catch (Exception e) {
            log.warn("索引文本学习资料失败: {}", e.getMessage());
            return Result.error(e.getMessage());
        }
    }

    @PostMapping("/materials/upload")
    @Operation(summary = "上传并索引学习资料")
    public Result<LearningMaterialVO> uploadMaterial(@RequestParam("file") MultipartFile file,
                                                     @RequestParam(value = "highPrecision", defaultValue = "false") Boolean highPrecision) {
        try {
            log.info("上传学习资料: filename={}, size={}, highPrecision={}",
                    file.getOriginalFilename(), file.getSize(), highPrecision);
            return Result.success(ragService.uploadMaterial(file, highPrecision));
        } catch (Exception e) {
            log.warn("上传学习资料失败: {}", e.getMessage());
            return Result.error(e.getMessage());
        }
    }

    @PostMapping("/query")
    @Operation(summary = "RAG 检索问答")
    public Result<RagQueryVO> query(@Valid @RequestBody RagQueryDTO dto) {
        try {
            log.info("执行 RAG 检索问答: question={}, topK={}", dto.getQuestion(), dto.getTopK());
            return Result.success(ragService.query(dto));
        } catch (Exception e) {
            log.warn("RAG 检索问答失败: {}", e.getMessage());
            return Result.error(e.getMessage());
        }
    }
}
