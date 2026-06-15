# Multimodal-RAG-Agent-Learning-Evidence-Platform-React-Java-Python

中文名：学迹智配 Agent：基于 RAG 的多模态学习证据库与岗位适配系统

技术栈：React + Java Spring Boot + Python FastAPI + RAG + Agent

## 项目定位

本项目面向大学生和求职准备人群，目标是把个人学习资料、课程笔记、项目材料、视频片段和简历沉淀为可检索、可引用、可复用的个人学习证据库。

系统计划通过 RAG 支撑个人知识问答、资料复习定位和证据引用，通过 Agent 完成两个明确任务：

- 根据岗位 JD 分析用户已掌握内容、能力缺口和学习计划。
- 根据岗位 JD、用户简历和个人知识库证据，生成更契合岗位的简历优化建议。

## 技术边界

- `frontend-react/`：React 前端，负责资料管理、知识库检索、视频复习、JD 分析和简历适配页面。
- `backend-java/`：Java 后端业务服务，负责用户、权限、资料、简历、岗位、任务状态和 AI 服务调用。
- `ai-python/`：Python AI 服务，负责文件解析、RAG 索引、检索、视频处理和 Agent 编排。
- `docs/`：产品、架构、API、RAG、Video RAG 和 Agent 设计文档。
- `infra/`：Docker、SQL、脚本和部署配置。
- `samples/`：示例资料、岗位 JD 和简历样例。

## 规划能力

- 多格式资料导入：Markdown、PDF、Word、PPT、视频。
- 自动学习笔记生成：章节、概念、重点、复习问题。
- 视频 RAG：ASR、关键帧、OCR、时间轴切块、片段检索和跳转复习。
- 个人知识库 RAG：混合检索、元数据过滤、重排序和证据引用。
- JD 学习计划 Agent：岗位要求解析、个人证据匹配、缺口分析、学习计划生成。
- 简历适配 Agent：基于真实证据进行简历改写，并标注证据支持程度。

## 初始骨架说明

当前仓库只创建项目目录骨架和占位文件，不包含具体业务代码。
