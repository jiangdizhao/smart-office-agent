from app.models import PlannedStep


def plan_task(user_text: str) -> list[PlannedStep]:
    text = user_text.lower()

    if any(k in text for k in ["meeting", "会议", "zoom", "prepare", "准备"]):
        return [
            PlannedStep(
                index=1,
                title="打开 Smart Office Agent Dashboard",
                tool_name="open_edge",
                args={"url": "http://localhost:5173"},
            ),
            PlannedStep(
                index=2,
                title="打开会议议程示例文件",
                tool_name="open_sample_document",
                args={},
            ),
            PlannedStep(
                index=3,
                title="打开 Zoom",
                tool_name="open_zoom",
                args={},
            ),
            PlannedStep(
                index=4,
                title="打开 OneNote，用于查看会议记录",
                tool_name="open_onenote",
                args={},
            ),
            PlannedStep(
                index=5,
                title="生成会议准备建议：议程、风险点、后续邮件草稿",
                tool_name=None,
                args={},
                requires_confirmation=True,
            ),
        ]

    if any(k in text for k in ["word", "文档", "proposal", "合同"]):
        return [
            PlannedStep(
                index=1,
                title="打开 Word",
                tool_name="open_word",
                args={},
            ),
            PlannedStep(
                index=2,
                title="打开示例文档",
                tool_name="open_sample_document",
                args={},
            ),
            PlannedStep(
                index=3,
                title="准备文档摘要与风险点分析",
                tool_name=None,
                args={},
                requires_confirmation=True,
            ),
        ]

    if any(k in text for k in ["excel", "表格"]):
        return [
            PlannedStep(
                index=1,
                title="打开 Excel",
                tool_name="open_excel",
                args={},
            ),
            PlannedStep(
                index=2,
                title="准备表格分析工作流",
                tool_name=None,
                args={},
                requires_confirmation=True,
            ),
        ]

    return [
        PlannedStep(
            index=1,
            title="打开 Smart Office Agent Dashboard",
            tool_name="open_edge",
            args={"url": "http://localhost:5173"},
        ),
        PlannedStep(
            index=2,
            title="当前请求还没有专用工具，返回通用计划",
            tool_name=None,
            args={},
        ),
    ]