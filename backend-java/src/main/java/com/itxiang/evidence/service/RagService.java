package com.itxiang.evidence.service;

import com.itxiang.evidence.dto.RagIndexTextDTO;
import com.itxiang.evidence.dto.RagQueryDTO;
import com.itxiang.evidence.vo.LearningMaterialVO;
import com.itxiang.evidence.vo.RagEvidenceVO;
import com.itxiang.evidence.vo.RagOverviewVO;
import com.itxiang.evidence.vo.RagQueryVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

public interface RagService {

    RagOverviewVO overview();

    List<LearningMaterialVO> listRecentMaterials();

    LearningMaterialVO getMaterial(Long id);

    List<RagEvidenceVO> listMaterialEvidences(Long id, Integer limit);

    LearningMaterialVO indexText(RagIndexTextDTO dto);

    LearningMaterialVO uploadMaterial(MultipartFile file, Boolean highPrecision);

    RagQueryVO query(RagQueryDTO dto);
}
