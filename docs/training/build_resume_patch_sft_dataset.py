"""构建简历字段级修改建议 SFT 数据集。

脚本职责：
- 保留原有前 50 条干净模拟样本；
- 从公开 Hugging Face 数据集抽取脱敏岗位/技能信号；
- 结合本项目 LangGraph 流程自行构建补充样本；
- 生成 500 条无乱码、证据引用合法的 JSONL。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import urllib.request
from pathlib import Path
from typing import Any


random.seed(20260724)

ROOT = Path("docs/training")
TARGET = ROOT / "resume_patch_sft_combined_100.jsonl"
BACKUP = ROOT / "resume_patch_sft_combined_100.before_clean_100.jsonl"
SOURCE_SIM = ROOT / "resume_patch_sft_samples.jsonl"


def clean_text(value: Any) -> str:
    """清理公开文本中的乱码、联系方式和多余空白。"""
    text = str(value or "")
    for old, new in {"\ufb01": "fi", "\ufb02": "fl", "�": ""}.items():
        text = text.replace(old, new)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[已脱敏邮箱]", text)
    text = re.sub(r"\(?\d{3}\)?[-\s]\d{3}[-\s]\d{4}", "[已脱敏电话]", text)
    return re.sub(r"\s+", " ", text).strip()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 文件。"""
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """写入 UTF-8 JSONL。"""
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )


ROLES: list[tuple[str, list[str], list[str]]] = [
    ("AI Agent 开发实习生", ["Python", "FastAPI", "LangGraph", "Agent", "RAG", "工具调用"], ["Python/FastAPI 接口开发", "LangGraph 状态流编排", "RAG evidence 引用"]),
    ("RAG 应用开发实习生", ["Python", "RAG", "BM25", "embedding", "RRF", "证据引用"], ["文档切块与索引", "混合检索", "引用证据返回"]),
    ("Python 后端实习生", ["Python", "FastAPI", "REST API", "pytest", "SQL", "日志"], ["REST API 开发", "异常处理", "接口测试"]),
    ("Java 后端开发实习生", ["Java", "Spring Boot", "MyBatis", "MySQL", "REST API"], ["Controller-Service-Mapper 分层", "MySQL 数据访问", "接口联调"]),
    ("React 前端实习生", ["React", "Vite", "TypeScript", "表单", "表格", "接口联调"], ["管理后台页面", "组件化开发", "接口状态处理"]),
    ("大模型评测实习生", ["LLM 评测", "schema", "幻觉检测", "Python", "成对比较"], ["固定测试集", "训练前后对比", "错误归因"]),
    ("文档解析实习生", ["Python", "DOCX", "PDF", "MinerU", "递归切块", "元数据"], ["多格式文档解析", "标题段落保留", "解析失败降级"]),
    ("简历优化算法实习生", ["JD 匹配", "字段级改写", "证据约束", "评分", "RAG"], ["岗位要求抽取", "证据约束改写", "缺口分析"]),
    ("数据工程实习生", ["Python", "SQL", "ETL", "Pandas", "数据质量"], ["数据清洗", "任务脚本", "质量校验"]),
    ("AI 安全与合规实习生", ["Prompt Injection", "幻觉检测", "风险标签", "证据白名单"], ["无证据拒写", "非法字段过滤", "人工复核"]),
]


PROJECT_EVIDENCE_POOL: list[tuple[str, str, str, str, list[str]]] = [
    ("EV-PROJ-RAG", "RAG 检索模块设计", "混合检索", "实现递归切块、BM25 与 embedding 向量召回，并使用 RRF 融合候选 evidence。", ["RAG", "BM25", "embedding", "RRF", "证据引用"]),
    ("EV-PROJ-AGENT", "LangGraph 工作流设计", "节点编排", "定义 JDProfile、EvidenceBundle 与 ResumePatchProposal 状态，由 JD 摘要、证据检索和修改建议节点依次处理。", ["LangGraph", "Agent", "JD 匹配", "字段级改写"]),
    ("EV-PROJ-API", "FastAPI 服务记录", "接口开发", "基于 FastAPI 封装文档解析与检索接口，统一错误响应，并使用 pytest 覆盖主要请求流程。", ["Python", "FastAPI", "REST API", "pytest"]),
    ("EV-PROJ-JAVA", "Java 后端模块说明", "分层架构", "后端采用 Controller、Service、Mapper 分层，并通过 MyBatis 维护资料、岗位和任务状态数据。", ["Java", "Spring Boot", "MyBatis", "MySQL"]),
    ("EV-PROJ-FE", "React 前端功能记录", "后台管理", "使用 React 与 Vite 开发资料列表、岗位表单、文件上传和简历修改建议确认页面。", ["React", "Vite", "表单", "接口联调"]),
    ("EV-PROJ-DOCX", "DOCX 补丁模块说明", "文档导出", "根据 fieldId 与 sourceTextHash 定位字段，应用用户确认的补丁并导出新的 DOCX。", ["DOCX", "字段级改写", "patch", "简历优化"]),
    ("EV-PROJ-EVAL", "模型评测方案", "训练前后对比", "冻结 JD、resumeFields 与 evidenceCandidates，比较模型训练前后的 schema 通过率、证据忠实度、幻觉率和胜率。", ["LLM 评测", "schema", "幻觉检测", "成对比较"]),
    ("EV-PROJ-SAFE", "简历生成安全规则", "事实约束", "修改建议只能引用输入 evidenceId；证据不足时输出缺口，不新增公司、奖项、项目结果或量化数字。", ["幻觉检测", "风险标签", "证据白名单", "Prompt Injection"]),
    ("EV-PROJ-DATA", "数据处理作业", "清洗统计", "使用 pandas 处理缺失值和重复记录，并输出基础统计表格。", ["Python", "Pandas", "数据质量"]),
    ("EV-PROJ-PARSE", "文档解析记录", "MinerU 降级", "优先通过 MINERU_COMMAND 调用 MinerU 解析资料，失败时降级为本地 DOCX/PDF 文本提取。", ["Python", "MinerU", "DOCX", "PDF", "递归切块"]),
]


KEYWORD_ZH: dict[str, str] = {
    "Python": "Python 编程",
    "Java": "Java 后端开发",
    "JavaScript": "JavaScript 前端开发",
    "React": "React 前端框架",
    "Spring Boot": "Spring Boot 后端框架",
    "FastAPI": "FastAPI 接口服务",
    "SQL": "SQL 数据查询",
    "MySQL": "MySQL 数据库",
    "RAG": "RAG 检索增强生成",
    "LLM": "大模型应用",
    "Agent": "智能体工作流",
    "LangGraph": "LangGraph 状态图编排",
    "API": "接口开发",
    "REST": "REST 接口",
    "Docker": "Docker 容器化",
    "Kubernetes": "Kubernetes 容器编排",
    "AWS": "AWS 云服务",
    "Azure": "Azure 云服务",
    "PyTorch": "PyTorch 模型实验",
    "TensorFlow": "TensorFlow 模型实验",
    "NLP": "自然语言处理",
    "Transformer": "Transformer 模型结构",
    "Selenium": "Selenium 自动化测试",
    "Excel": "Excel 数据分析",
    "Tableau": "Tableau 可视化",
    "Machine Learning": "机器学习",
    "Git": "Git 版本管理",
    "Linux": "Linux 环境",
    "Redis": "Redis 缓存",
    "MongoDB": "MongoDB 数据库",
    "PostgreSQL": "PostgreSQL 数据库",
    "Spark": "Spark 数据处理",
    "Airflow": "Airflow 任务调度",
    "C++": "C++ 编程",
    "C#": "C# 编程",
    "BM25": "BM25 稀疏检索",
    "embedding": "向量表示",
    "RRF": "RRF 融合排序",
    "schema": "结构化 schema 校验",
    "pytest": "pytest 自动化测试",
    "Pandas": "Pandas 数据处理",
    "DOCX": "DOCX 文档处理",
    "PDF": "PDF 文档解析",
    "MinerU": "MinerU 文档识别",
    "Prompt Injection": "提示注入风险",
}


def localize_keywords(keywords: list[str]) -> list[str]:
    """把技术关键词转为中文语料占比更高的训练短语。"""
    localized: list[str] = []
    for keyword in keywords:
        localized.append(KEYWORD_ZH.get(keyword, keyword if any("\u4e00" <= char <= "\u9fff" for char in keyword) else f"{keyword} 技术能力"))
    return localized


def localize_existing_sample(row: dict[str, Any]) -> dict[str, Any]:
    """把保留的旧样本补充为中文优先语料，不改变 patch schema。"""
    input_block = row.setdefault("input", {})
    profile = input_block.setdefault("jdProfile", {})
    if isinstance(profile.get("keywords"), list):
        profile["keywords"] = localize_keywords([str(item) for item in profile["keywords"]])
    simulation = input_block.setdefault("langGraphSimulation", {})
    jd_summary = simulation.setdefault("jdSummary", {})
    if isinstance(jd_summary.get("keywords"), list):
        jd_summary["keywords"] = localize_keywords([str(item) for item in jd_summary["keywords"]])
    evidence_summary = simulation.setdefault("evidenceSummary", {})
    if isinstance(evidence_summary.get("supportingKeywords"), list):
        evidence_summary["supportingKeywords"] = localize_keywords([str(item) for item in evidence_summary["supportingKeywords"]])
    row["instruction"] = "你是 LangGraph 中的字段级简历修改建议节点。请根据岗位摘要、原简历字段和证据候选，输出严格的字段级补丁 JSON；只能引用输入中的证据编号，不得编造经历、公司、奖项、量化结果或未证实能力；证据不足时必须保留原文或输出缺口并标记风险。"
    input_block["languagePolicy"] = "训练语料以中文表达为主，技术名词可保留英文缩写，但需要搭配中文解释。"
    row.setdefault("datasetMeta", {})["language"] = "zh-CN"
    return row


def build_sample(
    index: int,
    role: str,
    keywords: list[str],
    requirements: list[str],
    evidence_items: list[tuple[str, str, str, str, list[str]]],
    source_text: str,
    strength: str,
    source_tag: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """构造单条 LangGraph patch_suggestion_node 训练样本。"""
    keywords = localize_keywords(keywords)
    field_id = f"field_{index:04d}"
    source_hash = "hash-" + hashlib.sha1((field_id + source_text).encode("utf-8")).hexdigest()[:24]
    evidence_candidates = []
    for offset, evidence in enumerate(evidence_items[:3], start=1):
        evidence_id, title, section, snippet, _ = evidence
        evidence_candidates.append(
            {
                "evidenceId": f"{evidence_id}-{index:04d}-{offset}",
                "documentTitle": title,
                "sectionName": section,
                "snippet": snippet,
                "source": "公开数据集脱敏样本或项目自构造证据",
                "score": round(0.92 - 0.06 * (offset - 1), 2),
            }
        )
    if strength == "STRONG":
        new_text = f"围绕{role}要求，{source_text.rstrip('。')}，重点体现 {'、'.join(keywords[:4])} 相关能力。"
        reason = "JD 关键词与 evidence 高度匹配，改写为岗位导向的字段级经历描述。"
        risk_flags = ["NONE"]
        confidence = 0.88
        risk = "LOW"
        evidence_ids = [item["evidenceId"] for item in evidence_candidates]
    elif strength == "PARTIAL":
        new_text = f"{source_text.rstrip('。')}，可保守关联到 {'、'.join(keywords[:2])} 等岗位基础要求。"
        reason = "证据只覆盖部分岗位要求，采用保守措辞，不扩写为独立负责或生产级成果。"
        risk_flags = ["LOW_CONFIDENCE"]
        confidence = 0.63
        risk = "MEDIUM"
        evidence_ids = [item["evidenceId"] for item in evidence_candidates]
    else:
        new_text = source_text
        reason = "未找到可支撑岗位核心要求的 evidence，保留原文并提示人工补充学习或项目证明。"
        risk_flags = ["MISSING_EVIDENCE", "LOW_CONFIDENCE"]
        confidence = 0.30
        risk = "HIGH"
        evidence_candidates = []
        evidence_ids = []
    return {
        "sampleId": f"resume_patch_clean_{index:04d}",
        "split": "train",
        "instruction": "你是 LangGraph 中的字段级简历修改建议节点。请根据岗位摘要、原简历字段和证据候选，输出严格的字段级补丁 JSON；只能引用输入中的证据编号，不得编造经历、公司、奖项、量化结果或未证实能力；证据不足时必须保留原文或输出缺口并标记风险。",
        "input": {
            "jobId": f"JOB-CLEAN-{index:04d}",
            "targetRole": role,
            "languagePolicy": "训练语料以中文表达为主，技术名词可保留英文缩写，但需要搭配中文解释。",
            "langGraphSimulation": {
                "steps": ["jd_summary_agent", "evidence_summary_agent", "patch_suggestion_node"],
                "jdSummary": {
                    "priorityRequirements": requirements,
                    "keywords": keywords,
                    "mustNotClaim": ["未出现在 evidence 中的量化成果", "未出现在 evidence 中的公司/奖项/岗位", "生产级规模或线上用户量"],
                },
                "evidenceSummary": {
                    "evidenceStrength": strength,
                    "supportingKeywords": keywords[:4] if strength != "NONE" else [],
                    "unsupportedClaims": ["生产级规模", "未证实量化指标", "未提供证据的工具或模型经验"],
                },
                "testedNode": "patch_suggestion_node",
            },
            "jdProfile": {"priorityRequirements": requirements, "keywords": keywords, "source": "public_cleaned_or_project_synthetic"},
            "resumeFields": [
                {
                    "fieldId": field_id,
                    "sectionKey": "project_experience",
                    "sourceText": source_text,
                    "sourceTextHash": source_hash,
                    "maxChars": 240,
                    "maxLines": 6,
                    "requiredEvidencePolicy": "REQUIRED",
                }
            ],
            "evidenceCandidates": evidence_candidates,
            "evidenceStrength": strength,
            "unsupportedClaims": ["生产级规模", "未证实量化指标", "未提供 evidence 的公司/奖项/岗位"],
            "provenance": provenance,
        },
        "output": {
            "patches": [
                {
                    "fieldId": field_id,
                    "sourceTextHash": source_hash,
                    "newText": clean_text(new_text)[:220],
                    "rewriteReason": reason,
                    "evidenceIds": evidence_ids,
                    "confidence": confidence,
                    "riskFlags": risk_flags,
                    "status": "DRAFT",
                }
            ],
            "riskAssessment": {"riskLevel": risk, "reviewerAction": "进入人工确认；弱证据和无证据样本不得直接写入投递版简历。"},
            "trainingNotes": "仿照 LangGraph 单节点调用：上游已提供 JD 摘要和 evidence 摘要，本样本只训练 patch_suggestion_node。",
        },
        "datasetMeta": {
            "source": source_tag,
            "qualityAction": "clean_rebuild_500_no_question_mark_pollution",
            "langGraphFlow": "jd_summary -> evidence_summary -> patch_suggestion",
            "language": "zh-CN",
        },
    }


def fetch_hf_rows() -> list[dict[str, Any]]:
    """下载公开 resume-ATS 数据，失败时返回空列表。"""
    url = "https://datasets-server.huggingface.co/rows?dataset=0xnbk%2Fresume-ats-score-v1-en&config=default&split=train&offset=0&length=260"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Codex clean resume patch builder"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
        return [item.get("row", {}) for item in data.get("rows", [])]
    except Exception:
        return []


def role_from_text(text: str) -> str:
    """从公开 JD 片段推断岗位。"""
    lower = text.lower()
    for pattern, role in [
        ("business analyst", "业务分析实习生"),
        ("software engineer", "软件工程实习生"),
        ("software developer", "软件开发实习生"),
        ("data analyst", "数据分析实习生"),
        ("python", "Python 后端实习生"),
        ("java", "Java 后端实习生"),
        ("react|frontend|front end", "前端开发实习生"),
        ("machine learning|deep learning", "机器学习实习生"),
        ("qa|quality|test", "测试开发实习生"),
        ("cloud|devops", "云平台开发实习生"),
    ]:
        if re.search(pattern, lower):
            return role
    return random.choice(ROLES)[0]


def keywords_from_text(text: str) -> list[str]:
    """从公开 JD 文本抽取技术关键词。"""
    bank = [
        "Python", "Java", "JavaScript", "React", "Spring Boot", "FastAPI", "SQL", "MySQL",
        "RAG", "LLM", "Agent", "LangGraph", "API", "REST", "Docker", "Kubernetes",
        "AWS", "Azure", "PyTorch", "TensorFlow", "NLP", "Transformer", "Selenium", "Excel",
        "Tableau", "Machine Learning", "Git", "Linux", "Redis", "MongoDB", "PostgreSQL",
        "Spark", "Airflow", "C++", "C#",
    ]
    lower = text.lower()
    found = [keyword for keyword in bank if keyword.lower() in lower]
    return found[:6] or random.choice(ROLES)[1]


def natural_language_value_text(value: Any, parent_key: str = "") -> str:
    """抽取自然语言字段，用于估算中文语料占比；排除 id/hash/status/source 等技术标识。"""
    excluded = {
        "sampleId",
        "jobId",
        "fieldId",
        "sourceTextHash",
        "evidenceId",
        "evidenceIds",
        "status",
        "split",
        "score",
        "confidence",
        "source",
        "sourceAttribution",
        "sourceDataset",
        "sourceLicense",
        "datasetMeta",
        "provenance",
        "steps",
        "testedNode",
        "langGraphFlow",
        "qualityAction",
        "basis",
        "language",
        "dataset",
        "license",
        "type",
    }
    if parent_key in excluded:
        return ""
    if isinstance(value, dict):
        return " ".join(natural_language_value_text(item, key) for key, item in value.items())
    if isinstance(value, list):
        return " ".join(natural_language_value_text(item, parent_key) for item in value)
    if isinstance(value, str):
        return value
    return ""


def zh_ratio(text: str) -> float:
    """计算中文字符在中英文字符中的占比。"""
    zh = sum("\u4e00" <= char <= "\u9fff" for char in text)
    latin = sum(("A" <= char <= "Z") or ("a" <= char <= "z") for char in text)
    return zh / (zh + latin) if zh + latin else 1.0


def validate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """校验数量、污染、字段和 evidence 引用。"""
    errors: list[str] = []
    seen: set[str] = set()
    strength_summary: dict[str, int] = {}
    ratios: list[float] = []
    for row in rows:
        serialized = json.dumps(row, ensure_ascii=False)
        sample_id = row.get("sampleId", "")
        if "????" in serialized or "�" in serialized:
            errors.append(f"polluted:{sample_id}")
        if sample_id in seen:
            errors.append(f"duplicate:{sample_id}")
        seen.add(sample_id)
        strength = row.get("input", {}).get("evidenceStrength", "UNKNOWN")
        strength_summary[strength] = strength_summary.get(strength, 0) + 1
        ratios.append(zh_ratio(natural_language_value_text(row)))
        allowed = {item.get("evidenceId") for item in row.get("input", {}).get("evidenceCandidates", [])}
        fields = {item.get("fieldId"): item for item in row.get("input", {}).get("resumeFields", [])}
        for patch in row.get("output", {}).get("patches", []):
            for key in ["fieldId", "sourceTextHash", "newText", "rewriteReason", "evidenceIds", "confidence", "riskFlags", "status"]:
                if key not in patch:
                    errors.append(f"{sample_id}:missing:{key}")
            if patch.get("fieldId") not in fields:
                errors.append(f"{sample_id}:bad_field")
            for evidence_id in patch.get("evidenceIds", []):
                if evidence_id not in allowed:
                    errors.append(f"{sample_id}:bad_evidence:{evidence_id}")
            if strength == "NONE":
                if patch.get("evidenceIds"):
                    errors.append(f"{sample_id}:none_has_evidence")
                if "MISSING_EVIDENCE" not in patch.get("riskFlags", []):
                    errors.append(f"{sample_id}:none_no_missing_flag")
    if len(rows) != 500:
        errors.append(f"count:{len(rows)}")
    if len(seen) != 500:
        errors.append(f"unique:{len(seen)}")
    below_threshold = sum(ratio < 0.60 for ratio in ratios)
    if below_threshold:
        errors.append(f"natural_language_zh_ratio_below_60:{below_threshold}")
    if errors:
        raise SystemExit("\n".join(errors[:80]))
    strength_summary["__avgNaturalLanguageZhRatioPct"] = round(sum(ratios) / len(ratios) * 100, 2)
    strength_summary["__minNaturalLanguageZhRatioPct"] = round(min(ratios) * 100, 2)
    return strength_summary


def main() -> None:
    """主入口：生成并覆盖主训练文件。"""
    if TARGET.exists() and not BACKUP.exists():
        BACKUP.write_text(TARGET.read_text(encoding="utf-8"), encoding="utf-8")

    samples = load_jsonl(SOURCE_SIM)[:50]
    for index, row in enumerate(samples, start=1):
        row = localize_existing_sample(row)
        row["sampleId"] = f"resume_patch_clean_{index:04d}"
        row["split"] = "train"
        row.setdefault("input", {}).setdefault(
            "langGraphSimulation",
            {"steps": ["jd_summary_agent", "evidence_summary_agent", "patch_suggestion_node"], "testedNode": "patch_suggestion_node"},
        )
        row.setdefault("datasetMeta", {}).update(
            {
                "source": "previous_simulated_agent_samples",
                "qualityAction": "kept_first_50_clean_samples",
                "langGraphFlow": "jd_summary -> evidence_summary -> patch_suggestion",
            }
        )

    next_index = len(samples) + 1

    for row in fetch_hf_rows():
        if next_index > 260:
            break
        text = clean_text(row.get("text", ""))
        parts = re.split(r"\s+SEP\s+", text, maxsplit=1, flags=re.I)
        resume_text = parts[0] if parts else text[:2000]
        jd_text = parts[1] if len(parts) == 2 else text[-2000:]
        role = role_from_text(jd_text)
        keywords = keywords_from_text(jd_text)
        lower_resume = resume_text.lower()
        hits = [keyword for keyword in keywords if keyword.lower() in lower_resume]
        strength = "STRONG" if len(hits) >= 3 else ("PARTIAL" if hits else "NONE")
        requirements = [f"岗位强调 {keyword} 能力" for keyword in keywords[:3]]
        source_text = f"参与{role}相关项目，接触 {'、'.join((hits or keywords)[:3])}。"
        if strength == "NONE":
            evidence_items: list[tuple[str, str, str, str, list[str]]] = []
        else:
            evidence_items = [
                (
                    "EV-HF-ATS",
                    "Hugging Face resume-ATS-score-v1-en 脱敏样本",
                    "公开简历技能片段",
                    f"公开脱敏简历片段显示候选人接触 {'、'.join((hits or keywords)[:3])}，可作为岗位技能匹配证据。",
                    hits or keywords,
                )
            ]
        samples.append(
            build_sample(
                next_index,
                role,
                keywords,
                requirements,
                evidence_items,
                source_text,
                strength,
                "Hugging Face / 0xnbk resume-ATS-score-v1-en / apache-2.0 sanitized",
                {"type": "public_dataset_sanitized", "dataset": "0xnbk/resume-ats-score-v1-en", "license": "apache-2.0"},
            )
        )
        next_index += 1

    while next_index <= 500:
        role, keywords, requirements = random.choice(ROLES)
        strength = random.choices(["STRONG", "PARTIAL", "NONE"], weights=[45, 35, 20], k=1)[0]
        matching = [item for item in PROJECT_EVIDENCE_POOL if any(keyword in item[4] for keyword in keywords)]
        if strength == "NONE":
            evidence_items = []
        elif strength == "PARTIAL":
            evidence_items = [random.choice(matching or PROJECT_EVIDENCE_POOL)]
        else:
            pool = matching or PROJECT_EVIDENCE_POOL
            evidence_items = random.sample(pool, k=min(2, len(pool)))
        source_text = random.choice(
            [
                f"参与学迹智配项目中{role}相关模块开发。",
                f"围绕学习证据库和岗位适配功能完成{role}相关实践。",
                f"在课程项目中实现与{role}相关的接口、页面或数据处理功能。",
                f"参与 AI 求职辅助平台的{role}方向功能设计与验证。",
            ]
        )
        samples.append(
            build_sample(
                next_index,
                role,
                keywords,
                requirements,
                evidence_items,
                source_text,
                strength,
                "self_constructed_project_aligned",
                {"type": "self_constructed", "basis": "project_langgraph_flow_and_resume_patch_schema"},
            )
        )
        next_index += 1

    strength_summary = validate(samples)
    write_jsonl(TARGET, samples)
    print(json.dumps({"target": str(TARGET), "count": len(samples), "polluted": 0, "evidenceStrength": strength_summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
