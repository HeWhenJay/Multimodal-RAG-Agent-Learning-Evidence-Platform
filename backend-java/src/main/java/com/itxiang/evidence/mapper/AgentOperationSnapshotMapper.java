package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentOperationSnapshot;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface AgentOperationSnapshotMapper {

    /**
     * 新增操作快照。
     */
    void insert(AgentOperationSnapshot snapshot);

    /**
     * 查询指定操作和类型的快照。
     */
    AgentOperationSnapshot findByOperationIdAndType(@Param("operationId") String operationId,
                                                    @Param("snapshotType") String snapshotType);
}
