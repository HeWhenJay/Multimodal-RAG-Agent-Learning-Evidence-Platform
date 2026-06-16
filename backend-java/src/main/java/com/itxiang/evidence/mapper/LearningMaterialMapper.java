package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.LearningMaterial;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface LearningMaterialMapper {

    void insert(LearningMaterial material);

    void updateIndexResult(@Param("id") Long id,
                           @Param("status") String status,
                           @Param("parser") String parser,
                           @Param("documentSummary") String documentSummary,
                           @Param("chunkCount") Integer chunkCount);

    void updateStatus(@Param("id") Long id, @Param("status") String status);

    List<LearningMaterial> findRecent(@Param("limit") Integer limit);

    Long countAll();

    Integer sumChunkCount();
}

