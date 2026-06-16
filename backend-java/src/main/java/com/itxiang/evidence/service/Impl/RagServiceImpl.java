package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.RagQueryVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
public class RagServiceImpl implements RagService {

    private static final String DEMO_USER_ID = "demo-user";
    private static final Path UPLOAD_ROOT = Path.of("uploads", "rag");
    private static final DateTimeFormatter DATE_PATH_FORMATTER = DateTimeFormatter.BASIC_ISO_DATE;

    private final LearningMaterialMapper learningMaterialMapper;
    private final PythonRagClient pythonRagClient;

    @Override
    public RagOverviewVO overview() {
        Long materialCount = learningMaterialMapper.countAll();
        Integer chunkCount = learningMaterialMapper.sumChunkCount();
        List<LearningMaterial> recent = learningMaterialMapper.findRecent(1);
        PythonRagClient.PythonOverview pythonOverview = pythonRagClient.fetchOverviewSafely();
        return RagOverviewVO.builder()
                .materialCount(materialCount == null ? 0 : materialCount)
                .chunkCount(chunkCount == null ? pythonOverview.chunkCount() : chunkCount)
                .evidenceCount(pythonOverview.evidenceCount())
                .lastIndexedTitle(recent.isEmpty() ? pythonOverview.lastIndexedTitle() : recent.get(0).getTitle())
                .build();
    }

    @Override
    public List<LearningMaterialVO> listRecentMaterials() {
        return learningMaterialMapper.findRecent(20).stream()
                .map(this::convertToVO)
                .toList();
    }

    @Override
    public LearningMaterialVO getMaterial(Long id) {
        LearningMaterial material = learningMaterialMapper.findById(id);
        if (material == null) {
            throw new IllegalArgumentException("资料不存在");
        }
        return convertToVO(material);
    }

    @Override
    public List<RagEvidenceVO> listMaterialEvidences(Long id, Integer limit) {
        LearningMaterial material = learningMaterialMapper.findById(id);
        if (material == null) {
            throw new IllegalArgumentException("资料不存在");
        }
        int safeLimit = limit == null ? 20 : Math.max(1, Math.min(limit, 100));
        return pythonRagClient.listDocumentEvidences("material-" + id, safeLimit);
    }

    @Override
    @Transactional
    public LearningMaterialVO indexText(RagIndexTextDTO dto) {
        LearningMaterial material = new LearningMaterial();
        material.setTitle(dto.getTitle());
        material.setDocumentType(blankToDefault(dto.getDocumentType(), "markdown"));
        material.setSource(blankToDefault(dto.getSource(), "manual"));
        material.setStatus("PENDING");
        material.setChunkCount(0);
        learningMaterialMapper.insert(material);

        learningMaterialMapper.updateStatus(material.getId(), "PARSING");
        material.setStatus("PARSING");
        try {
            PythonRagClient.IndexResult result = pythonRagClient.indexText(material.getId(), DEMO_USER_ID, dto);
            applyIndexResult(material, result);
        } catch (Exception e) {
            log.warn("文本资料解析入库失败: materialId={}, reason={}", material.getId(), e.getMessage());
            markFailed(material, e.getMessage());
        }
        return convertToVO(material);
    }

    @Override
    @Transactional
    public LearningMaterialVO uploadMaterial(MultipartFile file, Boolean highPrecision) {
        String filename = file.getOriginalFilename() == null ? "未命名资料" : file.getOriginalFilename();
        Path savedPath = saveUploadFile(file, filename);
        LearningMaterial material = new LearningMaterial();
        material.setTitle(filename);
        material.setDocumentType(detectDocumentType(filename));
        material.setSource("upload");
        material.setStatus("PENDING");
        material.setChunkCount(0);
        material.setOriginalFilename(filename);
        material.setOriginalFilePath(savedPath.toString());
        learningMaterialMapper.insert(material);

        learningMaterialMapper.updateStatus(material.getId(), "PARSING");
        material.setStatus("PARSING");
        try {
            PythonRagClient.IndexResult result = pythonRagClient.indexFile(
                    material.getId(),
                    DEMO_USER_ID,
                    material,
                    file,
                    Boolean.TRUE.equals(highPrecision)
            );
            applyIndexResult(material, result);
        } catch (Exception e) {
            log.warn("文件资料解析入库失败: materialId={}, reason={}", material.getId(), e.getMessage());
            markFailed(material, e.getMessage());
        }
        return convertToVO(material);
    }

    @Override
    public RagQueryVO query(RagQueryDTO dto) {
        return pythonRagClient.query(dto);
    }

    private LearningMaterialVO convertToVO(LearningMaterial material) {
        return LearningMaterialVO.builder()
                .id(material.getId())
                .title(material.getTitle())
                .documentType(material.getDocumentType())
                .source(material.getSource())
                .status(material.getStatus())
                .parser(material.getParser())
                .documentSummary(material.getDocumentSummary())
                .chunkCount(material.getChunkCount() == null ? 0 : material.getChunkCount())
                .originalFilename(material.getOriginalFilename())
                .originalFilePath(material.getOriginalFilePath())
                .createdAt(material.getCreatedAt())
                .updatedAt(material.getUpdatedAt())
                .build();
    }

    private String detectDocumentType(String filename) {
        String lower = filename.toLowerCase();
        if (lower.endsWith(".md")) {
            return "markdown";
        }
        if (lower.endsWith(".pdf")) {
            return "pdf";
        }
        if (lower.endsWith(".docx")) {
            return "docx";
        }
        if (lower.endsWith(".pptx")) {
            return "pptx";
        }
        if (lower.endsWith(".ppt")) {
            return "ppt";
        }
        if (lower.endsWith(".doc")) {
            return "doc";
        }
        if (lower.endsWith(".xlsx")) {
            return "xlsx";
        }
        if (lower.endsWith(".xls")) {
            return "xls";
        }
        if (lower.endsWith(".txt")) {
            return "txt";
        }
        if (lower.endsWith(".png")) {
            return "png";
        }
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) {
            return "jpg";
        }
        if (lower.endsWith(".webp")) {
            return "webp";
        }
        return "text";
    }

    private String blankToDefault(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }

    private void applyIndexResult(LearningMaterial material, PythonRagClient.IndexResult result) {
        learningMaterialMapper.updateIndexResult(
                material.getId(),
                result.status(),
                result.parser(),
                result.documentSummary(),
                result.chunkCount()
        );
        material.setStatus(result.status());
        material.setParser(result.parser());
        material.setDocumentSummary(result.documentSummary());
        material.setChunkCount(result.chunkCount());
    }

    private void markFailed(LearningMaterial material, String reason) {
        String message = reason == null ? "Python RAG 解析失败" : reason;
        learningMaterialMapper.updateIndexResult(
                material.getId(),
                "FAILED",
                "python-rag-error",
                truncate(message, 500),
                0
        );
        material.setStatus("FAILED");
        material.setParser("python-rag-error");
        material.setDocumentSummary(truncate(message, 500));
        material.setChunkCount(0);
    }

    private Path saveUploadFile(MultipartFile file, String filename) {
        if (file.isEmpty()) {
            throw new IllegalArgumentException("上传文件不能为空");
        }
        String datePath = LocalDate.now().format(DATE_PATH_FORMATTER);
        Path directory = UPLOAD_ROOT.resolve(datePath);
        String safeFilename = sanitizeFilename(filename);
        Path target = directory.resolve(UUID.randomUUID() + "-" + safeFilename);
        try {
            Files.createDirectories(directory);
            try (var inputStream = file.getInputStream()) {
                Files.copy(inputStream, target);
            }
            return target;
        } catch (IOException e) {
            throw new IllegalStateException("保存上传文件失败: " + e.getMessage(), e);
        }
    }

    private String sanitizeFilename(String filename) {
        String fallback = filename == null || filename.isBlank() ? "material" : filename;
        return fallback
                .replaceAll("[\\\\/:*?\"<>|]+", "_")
                .replaceAll("\\s+", "_")
                .toLowerCase(Locale.ROOT);
    }

    private String truncate(String value, int maxLength) {
        if (value == null || value.length() <= maxLength) {
            return value;
        }
        return value.substring(0, maxLength);
    }
}
