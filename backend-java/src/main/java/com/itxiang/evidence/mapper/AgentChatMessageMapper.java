package com.itxiang.evidence.mapper;

import com.itxiang.evidence.entity.AgentChatMessage;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AgentChatMessageMapper {

    /**
     * 新增任务消息投影，用于恢复历史聊天记录。
     */
    void insert(AgentChatMessage message);

    /**
     * 获取当前任务下一条稳定消息序号。
     */
    Long nextSequenceNo(@Param("taskId") String taskId);

    /**
     * 按任务和幂等键查询既有消息。
     */
    AgentChatMessage findByTaskIdAndDedupeKey(@Param("taskId") String taskId, @Param("dedupeKey") String dedupeKey);

    /**
     * 按任务和消息 ID 查询消息，用于校验摘要覆盖范围。
     */
    AgentChatMessage findByIdAndTaskId(@Param("id") String id, @Param("taskId") String taskId);

    /**
     * 按任务和幂等键更新消息内容。
     */
    void updateByDedupeKey(AgentChatMessage message);

    /**
     * 查询当前任务的可展示消息流。
     */
    List<AgentChatMessage> findByTaskId(@Param("taskId") String taskId);

    /**
     * 按 sequence_no 稳定分页查询消息；before 和 after 同时为空时返回最近窗口。
     */
    List<AgentChatMessage> findPageBySequence(@Param("taskId") String taskId,
                                              @Param("beforeSequenceNo") Long beforeSequenceNo,
                                              @Param("afterSequenceNo") Long afterSequenceNo,
                                              @Param("limit") Integer limit);

    /**
     * 统计某个序号之前的消息数量，用于判断是否还能加载更早消息。
     */
    int countBeforeSequence(@Param("taskId") String taskId, @Param("sequenceNo") Long sequenceNo);

    /**
     * 统计某个序号之后的消息数量，用于判断是否还能加载更新消息。
     */
    int countAfterSequence(@Param("taskId") String taskId, @Param("sequenceNo") Long sequenceNo);

    /**
     * 查询当前任务最近原文窗口。
     */
    List<AgentChatMessage> findRecentByTaskId(@Param("taskId") String taskId, @Param("limit") Integer limit);

    /**
     * 查询尚未被摘要覆盖、且不属于最近原文窗口的早期压缩候选。
     */
    List<AgentChatMessage> findCompressionCandidates(@Param("taskId") String taskId,
                                                     @Param("latestCoveredMessageEndId") String latestCoveredMessageEndId,
                                                     @Param("recentLimit") Integer recentLimit,
                                                     @Param("limit") Integer limit);

    /**
     * 统计尚未被摘要覆盖、且不属于最近原文窗口的早期消息数量。
     */
    int countCompressionCandidates(@Param("taskId") String taskId,
                                   @Param("latestCoveredMessageEndId") String latestCoveredMessageEndId,
                                   @Param("recentLimit") Integer recentLimit);

    /**
     * 查询锚点或范围之前的少量消息。
     */
    List<AgentChatMessage> findBeforeContext(@Param("taskId") String taskId,
                                             @Param("anchorMessageId") String anchorMessageId,
                                             @Param("coveredMessageStartId") String coveredMessageStartId,
                                             @Param("limit") Integer limit);

    /**
     * 查询锚点消息或摘要覆盖范围内消息。
     */
    List<AgentChatMessage> findCoreContext(@Param("taskId") String taskId,
                                           @Param("anchorMessageId") String anchorMessageId,
                                           @Param("coveredMessageStartId") String coveredMessageStartId,
                                           @Param("coveredMessageEndId") String coveredMessageEndId,
                                           @Param("limit") Integer limit);

    /**
     * 查询锚点或范围之后的少量消息。
     */
    List<AgentChatMessage> findAfterContext(@Param("taskId") String taskId,
                                            @Param("anchorMessageId") String anchorMessageId,
                                            @Param("coveredMessageEndId") String coveredMessageEndId,
                                            @Param("limit") Integer limit);
}
