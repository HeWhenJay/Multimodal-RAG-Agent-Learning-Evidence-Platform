package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.dto.ResumePatchGenerateDTO;
import com.itxiang.evidence.dto.ResumePatchValidateDTO;
import com.itxiang.evidence.dto.ResumeTemplateAnnotationSaveDTO;
import com.itxiang.evidence.dto.ResumeTemplateExportDTO;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.MaterialUploadChunkVO;
import com.itxiang.evidence.vo.MaterialPreviewVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.RagQueryHistoryVO;
import com.itxiang.evidence.vo.RagQueryTaskVO;
import com.itxiang.evidence.vo.RagQueryVO;
import com.itxiang.evidence.vo.ResumePatchDraftVO;
import com.itxiang.evidence.vo.ResumeTemplateExportVO;
import com.itxiang.evidence.vo.ResumeTemplatePreviewVO;
import com.itxiang.evidence.vo.ResumeTemplateVO;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDate;
import java.util.List;

public interface RagService {

    /**
     * 获取 RAG 资料、切块和证据概览。
     */
    RagOverviewVO overview(String userId);

    /**
     * 查询最近学习资料。
     */
    List<LearningMaterialVO> listRecentMaterials(String userId);

    /**
     * 按最近天数和数量查询学习资料。
     */
    List<LearningMaterialVO> listRecentMaterials(String userId, LocalDate startDate, LocalDate endDate, Integer limit);

    /**
     * 查询单个学习资料解析状态。
     */
    LearningMaterialVO getMaterial(Long id, String userId);

    /**
     * 查询单个学习资料的证据片段。
     */
    List<RagEvidenceVO> listMaterialEvidences(Long id, String userId, Integer limit);

    /**
     * 读取文本类学习资料内容，用于前端新标签预览。
     */
    MaterialPreviewVO previewMaterial(Long id, String source, String userId);

    /**
     * 索引文本学习资料。
     */
    LearningMaterialVO indexText(RagIndexTextDTO dto, String userId);

    /**
     * 上传并索引文件学习资料。
     */
    LearningMaterialVO uploadMaterial(MultipartFile file, Boolean highPrecision, String userId);

    /**
     * 接收文件分片，全部到齐后合并并索引学习资料。
     */
    MaterialUploadChunkVO uploadMaterialChunk(MultipartFile file,
                                              String uploadId,
                                              String filename,
                                              Integer chunkIndex,
                                              Integer totalChunks,
                                              Long totalSize,
                                              Boolean highPrecision,
                                              String userId);

    /**
     * 重新读取原始文件并重建资料索引。
     */
    LearningMaterialVO reindexMaterial(Long id, Boolean highPrecision, String userId);

    /**
     * 执行 RAG 检索问答。
     */
    RagQueryVO query(RagQueryDTO dto, String userId);

    /**
     * 执行 Agent 只读探针使用的非持久化 RAG 查询，不写询问历史。
     */
    RagQueryVO queryNonPersistent(RagQueryDTO dto, String userId);

    /**
     * 查询当前用户最近几次 RAG 询问历史。
     */
    List<RagQueryHistoryVO> listQueryHistory(String userId, LocalDate startDate, LocalDate endDate, Integer limit);

    /**
     * 创建 RAG 检索问答任务，供前端轮询进度详情。
     */
    RagQueryTaskVO startQueryTask(RagQueryDTO dto, String userId);

    /**
     * 查询 RAG 检索问答任务状态。
     */
    RagQueryTaskVO getQueryTask(String taskId, String userId);

    /**
     * 上传并解析简历模板字段绑定。
     */
    ResumeTemplateVO uploadResumeTemplate(MultipartFile file, String userId);

    /**
     * 查询当前用户最近的简历模板历史。
     */
    List<ResumeTemplateVO> listResumeTemplates(String userId, Integer limit);

    /**
     * 查询简历模板字段绑定。
     */
    ResumeTemplateVO getResumeTemplate(String templateId, String userId);

    /**
     * 删除当前用户上传的简历模板及其派生记录。
     */
    void deleteResumeTemplate(String templateId, String userId);

    /**
     * 查询或生成简历模板图片预览。
     */
    ResumeTemplatePreviewVO previewResumeTemplate(String templateId, Boolean refresh, String userId);

    /**
     * 读取简历模板预览页图片字节。
     */
    byte[] loadResumeTemplatePreviewImage(String templateId, Integer pageIndex, String userId);

    /**
     * 保存用户对图片区域的可改写约束。
     */
    ResumeTemplatePreviewVO saveResumeTemplateAnnotations(String templateId, ResumeTemplateAnnotationSaveDTO dto, String userId);

    /**
     * 基于 JD 和当前用户 evidence 生成字段级补丁草稿。
     */
    ResumePatchDraftVO generateResumeTemplatePatches(String templateId, ResumePatchGenerateDTO dto, String userId);

    /**
     * 校验用户确认的字段级补丁。
     */
    ResumePatchDraftVO validateResumeTemplatePatches(String templateId, ResumePatchValidateDTO dto, String userId);

    /**
     * 应用已确认补丁并导出新的 DOCX 版本。
     */
    ResumeTemplateExportVO exportResumeTemplate(String templateId, ResumeTemplateExportDTO dto, String userId);
}
