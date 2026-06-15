package com.itsheng.evidence.service;

import com.itsheng.evidence.dto.RagIndexTextDTO;
import com.itsheng.evidence.dto.RagQueryDTO;
import com.itsheng.evidence.vo.LearningMaterialVO;
import com.itsheng.evidence.vo.RagOverviewVO;
import com.itsheng.evidence.vo.RagQueryVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

public interface RagService {

    RagOverviewVO overview();

    List<LearningMaterialVO> listRecentMaterials();

    LearningMaterialVO indexText(RagIndexTextDTO dto);

    LearningMaterialVO uploadMaterial(MultipartFile file);

    RagQueryVO query(RagQueryDTO dto);
}

