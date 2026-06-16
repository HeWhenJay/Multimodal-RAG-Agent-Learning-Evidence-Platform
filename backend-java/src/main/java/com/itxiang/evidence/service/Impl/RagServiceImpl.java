package com.itxiang.evidence.service.Impl;

import com.itxiang.evidence.client.PythonRagClient;
import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.entity.LearningMaterial;
import com.itxiang.evidence.mapper.LearningMaterialMapper;
import com.itxiang.evidence.service.RagService;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.RagQueryVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class RagServiceImpl implements RagService {

    private static final String DEMO_USER_ID = "demo-user";

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
    @Transactional
    public LearningMaterialVO indexText(RagIndexTextDTO dto) {
        LearningMaterial material = new LearningMaterial();
        material.setTitle(dto.getTitle());
        material.setDocumentType(blankToDefault(dto.getDocumentType(), "markdown"));
        material.setSource(blankToDefault(dto.getSource(), "manual"));
        material.setStatus("INDEXING");
        material.setChunkCount(0);
        learningMaterialMapper.insert(material);

        PythonRagClient.IndexResult result = pythonRagClient.indexText(material.getId(), DEMO_USER_ID, dto);
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
        return convertToVO(material);
    }

    @Override
    @Transactional
    public LearningMaterialVO uploadMaterial(MultipartFile file) {
        String filename = file.getOriginalFilename() == null ? "未命名资料" : file.getOriginalFilename();
        LearningMaterial material = new LearningMaterial();
        material.setTitle(filename);
        material.setDocumentType(detectDocumentType(filename));
        material.setSource("upload");
        material.setStatus("INDEXING");
        material.setChunkCount(0);
        learningMaterialMapper.insert(material);

        PythonRagClient.IndexResult result = pythonRagClient.indexFile(material.getId(), DEMO_USER_ID, material, file);
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
        if (lower.endsWith(".html")) {
            return "html";
        }
        return "text";
    }

    private String blankToDefault(String value, String defaultValue) {
        return value == null || value.isBlank() ? defaultValue : value;
    }
}

