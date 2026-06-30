package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentConversationFolder;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentConversationFolderMapper {

    /**
     * 新增当前用户的 Agent 会话文件夹。
     */
    void insert(AgentConversationFolder folder);

    /**
     * 查询当前用户的会话文件夹。
     */
    List<AgentConversationFolder> findByUserId(@Param("userId") String userId);

    /**
     * 按 ID 和当前用户查询会话文件夹。
     */
    AgentConversationFolder findByIdAndUserId(@Param("id") String id, @Param("userId") String userId);

    /**
     * 查询当前用户下一个排序值。
     */
    Integer nextSortOrder(@Param("userId") String userId);

    /**
     * 更新会话文件夹名称和排序。
     */
    void update(AgentConversationFolder folder);

    /**
     * 删除当前用户的会话文件夹。
     */
    void deleteByIdAndUserId(@Param("id") String id, @Param("userId") String userId);
}
