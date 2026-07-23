"""演示知识库资料的唯一事实清单；本模块不生成最终知识文件。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeDocumentSpec:
    folder: str
    filename: str
    document_code: str
    title: str
    summary: str
    sections: tuple[str, ...]
    table_sheets: tuple[str, ...]


@dataclass(frozen=True)
class QuestionSpec:
    question: str
    kind: str
    expected_points: tuple[str, ...]
    source_files: tuple[str, ...]
    pass_criteria: str


OPS, LEARN, ENTERPRISE = "01-项目使用与故障排查", "02-CSharp转AI学习笔记", "03-模拟企业资料"


def _doc(folder: str, filename: str, code: str, title: str, facts: str, relation: str,
         sheets: tuple[str, ...] = ()) -> KnowledgeDocumentSpec:
    """为后续多格式生成器提供同一份中文、可检索的内容来源。"""
    sections = (
        f"一、资料用途与阅读边界。{title}的文档代号是 {code}，它是一份专为演示知识库准备的虚构中文资料。"
        f"资料面向学习者、演示人员和维护人员，目的不是替代真实业务制度，而是让提问者能够根据原文核对术语、"
        f"数字、日期和状态。回答前应先识别问题的范围，再从本文件或明确关联的文件中检索证据；资料没有写出的"
        f"结论必须说明知识库缺少答案，不能靠常识补写。内容不含真实账号、密钥、客户信息或公司机密，后续生成时"
        f"应保持 UTF-8 中文可读、标题清晰，并保留文件名以便验证引用来源。",
        f"二、核心规则与可核对事实。{facts}。这些事实需要作为完整流程理解：执行前检查前提，执行中记录状态，"
        f"执行后根据结果决定下一步。数字问题必须带上单位和条件，日期问题必须带上对应事项，状态问题必须区分"
        f"可继续处理和需要排查两种结果。引用本资料时，应同时给出文件名与能定位规则的摘录，不能只留下脱离语境"
        f"的短句。演示人员可以先针对一个精确事实提问，再把本文件与关联文件组合提问，以观察检索、重排序和回答"
        f"是否保持一致。",
        f"三、执行提示与关联资料。{relation}。当发现服务未就绪、资料处理失败或规则冲突时，应保留错误原因并"
        f"按排查路径处理，而不是猜测成功结果。每次演示都应把测试问题与知识资料分开保存，测试问题及预期答案"
        f"不得上传到知识库，避免答案文本反向污染检索。此段同时提醒使用者：示例中的名称、价格、日期和时限仅为"
        f"教学用虚构数据；若问题超出本资料范围，正确的通过标准是明确承认缺少依据，并请提问者补充可检索的材料。"
        f"为便于复盘，每次问答还应记录问题、命中的资料代号、引用摘录和未覆盖的边界，作为下一轮资料改进的依据。"
        f"记录应采用清晰、可复现的中文描述，避免把推测写成事实；需要多人协作时，应标明负责角色、处理时间和复核结果。"
    )
    return KnowledgeDocumentSpec(folder, filename, code, title, f"{title}的演示检索资料（{code}）。", sections, sheets)


_ROWS = (
    (OPS, "项目快速开始.md", "OPS-START-2026", "项目快速开始",
     "默认所有者为 ai-knowledge-assistant；本地地址为 http://127.0.0.1:8080；就绪门禁是 /api/ready 返回 HTTP 200；启动前至少保留 2 GiB 可用内存",
     "它与《部署端口与健康检查.xlsx》共同说明入口和健康状态，故障时可查《常见故障排查手册.pdf》。", ()),
    (OPS, "知识库上传规范.txt", "OPS-UPLOAD-20M", "知识库上传规范",
     "支持 .txt/.md/.pdf/.docx/.xlsx；单文件最大 20 MB；ready 文档可以被搜索；失败文档保留错误原因",
     "上传前可使用《项目快速开始.md》的就绪检查，异常时结合《常见故障排查手册.pdf》。", ()),
    (OPS, "RAG问答与引用指南.docx", "RAG-CITE-2026", "RAG问答与引用指南",
     "必须先检索再生成；引用包含文件名和摘录；未知答案不得编造；测试问题不得上传",
     "文档状态由《知识库上传规范.txt》保证，端口状态可交叉查看《部署端口与健康检查.xlsx》。", ()),
    (OPS, "部署端口与健康检查.xlsx", "DEPLOY-HEALTH-8080", "部署端口与健康检查",
     "gateway 端口为 8080；API 内部端口为 8000；PostgreSQL 内部端口为 5432；ready 路径为 /api/ready；健康服务为 gateway、api、worker、postgres",
     "外部访问方式与《项目快速开始.md》一致，超时排查参照《常见故障排查手册.pdf》。", ("服务端口表", "健康检查表", "排查说明")),
    (OPS, "常见故障排查手册.pdf", "OPS-TROUBLE-180", "常见故障排查手册",
     "ready 超时默认 180 秒；检查端口 8080 冲突；需要 Docker Desktop；停止后保留 3 个持久卷",
     "本手册补充《部署端口与健康检查.xlsx》的健康信息，并解释《知识库上传规范.txt》的失败状态。", ()),
    (LEARN, "Python与CSharp语法对照.md", "CS-PY-ASYNC-01", "Python与CSharp语法对照",
     "Python async/await 对应 C# async/await；list 对应 List<T>；dict 对应 Dictionary<TKey,TValue>；uv.lock 对应 NuGet 的锁定和 restore 概念",
     "异步与集合基础可与《FastAPI与ASP.NET-Core对照.docx》的依赖注入一起学习。", ()),
    (LEARN, "RAG检索流程.txt", "RAG-PIPELINE-06", "RAG检索流程",
     "六阶段为清洗、切块、向量化、检索、重排序、生成；推荐切块 600 字符；重叠 80 字符；引用在重排序后生成",
     "参数实验参照《向量检索参数实验.xlsx》，引用规则参照《RAG问答与引用指南.docx》。", ()),
    (LEARN, "FastAPI与ASP.NET-Core对照.docx", "API-COMPARE-422", "FastAPI与ASP.NET-Core对照",
     "依赖注入可映射；Pydantic 对应模型绑定；验证失败为 HTTP 422，ASP.NET Core 常见为 400；中间件概念可映射",
     "语言迁移可查看《Python与CSharp语法对照.md》，将校验和依赖注入放进同一请求链路理解。", ()),
    (LEARN, "向量检索参数实验.xlsx", "VECTOR-EXP-K6", "向量检索参数实验",
     "实验 top-k 为 3、6、10；最佳 MRR 为 0.82 且在 top-k 6；中位延迟 148 ms；切块 600、重叠 80",
     "实验设置与《RAG检索流程.txt》的切块建议一致，可讨论质量与延迟平衡。", ("实验结果", "参数说明", "结论与复现")),
    (LEARN, "AI-Agent学习路线.pdf", "AGENT-ROADMAP-12", "AI-Agent学习路线",
     "路线共 12 周；第 1-3 周工具调用；第 4-6 周状态图；第 7-9 周记忆与评估；第 10-12 周作品集；每周日复盘",
     "路线以《Python与CSharp语法对照.md》为迁移基础，并可结合《RAG检索流程.txt》练习检索应用。", ()),
    (ENTERPRISE, "客服退款FAQ.md", "REFUND-7D-2026", "客服退款FAQ",
     "标准申请在 7 个自然日内；审核在 1 个工作日内；退款在 3 个工作日内；紧急升级在 2 小时内响应",
     "套餐承诺参照《产品套餐与服务时效.xlsx》，账号风险遵从《IT账号安全规范.txt》。", ()),
    (ENTERPRISE, "IT账号安全规范.txt", "SEC-MFA-14", "IT账号安全规范",
     "密码最少 14 个字符；MFA 在 24 小时内启用；设备丢失在 30 分钟内报告；离职账号在 4 小时内禁用",
     "员工状态可与《员工手册与休假制度.docx》交叉确认，但本规范不记录真实账号。", ()),
    (ENTERPRISE, "员工手册与休假制度.docx", "HR-LEAVE-10", "员工手册与休假制度",
     "满一年年假 10 天；病假超过 2 天需要证明；调休 90 天后失效；请假在前一工作日 16:00 前提交",
     "行动安排可与《项目会议纪要与行动项.pdf》核对，账号处置仍以《IT账号安全规范.txt》为准。", ()),
    (ENTERPRISE, "产品套餐与服务时效.xlsx", "PLAN-SLA-2026", "产品套餐与服务时效",
     "Basic 为 ¥99/月且响应 8 小时；Pro 为 ¥399/月且响应 2 小时；Enterprise 为定制且响应 30 分钟；续费提前 15 天通知",
     "退款审核和退款时限以《客服退款FAQ.md》为准，套餐响应时间不能替代退款规则。", ("套餐价格", "服务时效", "续费规则")),
    (ENTERPRISE, "项目会议纪要与行动项.pdf", "PM-MEETING-0915", "项目会议纪要与行动项",
     "会议日期 2026-08-28；Beta 截止 2026-09-15；安全评审 2026-09-08；数据演练 2026-09-12",
     "人员安排尊重《员工手册与休假制度.docx》，服务承诺补充查看《产品套餐与服务时效.xlsx》。", ()),
)

KNOWLEDGE_DOCUMENTS = tuple(_doc(*row) for row in _ROWS)


def _question(question: str, kind: str, points: tuple[str, ...], sources: tuple[str, ...]) -> QuestionSpec:
    criterion = "回答应覆盖预期要点，并引用指定文件名和相关摘录。"
    if kind == "unanswerable":
        criterion = "必须明确说明知识库缺少该答案，不得编造，也不应给出来源。"
    return QuestionSpec(question, kind, points, sources, criterion)


def _set(
    folder: str,
    points: tuple[tuple[str, ...], ...],
    single_sources: tuple[str, ...],
) -> tuple[QuestionSpec, ...]:
    files = tuple(doc.filename for doc in KNOWLEDGE_DOCUMENTS if doc.folder == folder)
    assert len(points) == len(single_sources) == 6
    singles = tuple(
        _question(
            f"请根据《{source}》说明其中一项关键精确规则。",
            "single_document",
            expected_points,
            (source,),
        )
        for expected_points, source in zip(points, single_sources, strict=True)
    )
    cross = (
        _question("请综合前两份资料说明两项相关规则。", "cross_document", points[1] + points[2], (files[1], files[2])),
        _question("请综合后两份资料说明时间或状态规则。", "cross_document", points[3] + points[4], (files[3], files[4])),
    )
    citation = _question(f"请引用《{files[0]}》并给出文件名和摘录。", "citation_check", points[0], (files[0],))
    unknown = _question("该主题资料中规定的真实客户银行卡号是什么？", "unanswerable", ("知识库缺少答案",), ())
    return singles + cross + (citation, unknown)


QUESTION_SETS = {
    OPS: _set(
        OPS,
        (("ai-knowledge-assistant", "HTTP 200"), ("20 MB", "ready"), ("先检索再生成", "不得编造"), ("8080", "8000", "5432"), ("180 秒", "3 个持久卷"), ("错误原因",)),
        ("项目快速开始.md", "知识库上传规范.txt", "RAG问答与引用指南.docx", "部署端口与健康检查.xlsx", "常见故障排查手册.pdf", "知识库上传规范.txt"),
    ),
    LEARN: _set(
        LEARN,
        (("List<T>", "Dictionary<TKey,TValue>"), ("六阶段", "600 字符", "80 字符"), ("HTTP 422", "400"), ("top-k 6", "0.82", "148 ms"), ("12 周", "周日"), ("async/await",)),
        ("Python与CSharp语法对照.md", "RAG检索流程.txt", "FastAPI与ASP.NET-Core对照.docx", "向量检索参数实验.xlsx", "AI-Agent学习路线.pdf", "Python与CSharp语法对照.md"),
    ),
    ENTERPRISE: _set(
        ENTERPRISE,
        (("7 个自然日", "3 个工作日"), ("14 个字符", "24 小时"), ("10 天", "90 天"), ("¥399/月", "2 小时"), ("2026-09-15", "2026-09-08"), ("2 小时",)),
        ("客服退款FAQ.md", "IT账号安全规范.txt", "员工手册与休假制度.docx", "产品套餐与服务时效.xlsx", "项目会议纪要与行动项.pdf", "客服退款FAQ.md"),
    ),
}
