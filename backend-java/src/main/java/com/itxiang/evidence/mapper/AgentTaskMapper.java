package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentTask;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentTaskMapper {

    /**
     * 新增 Agent 任务。
     */
    void insert(AgentTask task);

    /**
     * 按任务 ID 查询任务。
     */
    AgentTask findById(@Param("id") String id);

    /**
     * 锁定任务行，用于同一任务内消息序号串行分配。
     */
    AgentTask lockByIdForUpdate(@Param("id") String id);

    /**
     * 按当前用户查询任务详情。
     */
    AgentTask findByIdAndUserId(@Param("id") String id, @Param("userId") String userId);

    /**
     * 查询当前用户最近的 Agent 会话任务。
     */
    List<AgentTask> findRecentByUserId(@Param("userId") String userId, @Param("limit") Integer limit);

    /**
     * 查询当前用户某个文件夹下的 Agent 会话任务。
     */
    List<AgentTask> findByUserIdAndFolderId(@Param("userId") String userId,
                                            @Param("folderId") String folderId,
                                            @Param("limit") Integer limit);

    /**
     * 查询当前用户未分类的 Agent 会话任务。
     */
    List<AgentTask> findUnfiledByUserId(@Param("userId") String userId, @Param("limit") Integer limit);

    /**
     * 回写 Python Agent 任务事件后的主状态。
     */
    void updateFromEvent(AgentTask task);

    /**
     * 更新当前任务的会话主题标题。
     */
    void updateTitle(AgentTask task);

    /**
     * 移动当前用户的会话任务到指定文件夹，folderId 为空表示未分类。
     */
    void updateFolder(@Param("id") String id, @Param("userId") String userId, @Param("folderId") String folderId);

    /**
     * 删除文件夹时将会话任务移回未分类。
     */
    void clearFolderForUser(@Param("folderId") String folderId, @Param("userId") String userId);
}
