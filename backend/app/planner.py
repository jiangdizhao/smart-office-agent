from app.models import PlannedStep


def _single_step(title: str, tool_name: str) -> list[PlannedStep]:
    return [
        PlannedStep(
            index=1,
            title=title,
            tool_name=tool_name,
            args={},
        )
    ]


def plan_task(user_text: str) -> list[PlannedStep]:
    text = " ".join(user_text.casefold().split())

    music_mentioned = any(
        token in text
        for token in ["音乐", "歌曲", "歌", "music", "song"]
    )
    close_mentioned = any(
        token in text
        for token in [
            "关闭",
            "关掉",
            "停止",
            "退出",
            "结束",
            "close",
            "stop",
            "quit",
            "exit",
        ]
    )
    open_mentioned = any(
        token in text
        for token in [
            "打开",
            "启动",
            "开启",
            "播放",
            "放一首",
            "放点",
            "open",
            "launch",
            "start",
            "play",
        ]
    )

    if music_mentioned and close_mentioned:
        return _single_step("停止音乐并关闭受控媒体播放器", "system_music_stop")
    if music_mentioned and open_mentioned:
        return _single_step("随机选择并播放一首本地音乐", "system_music_play_random")

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

    powerpoint_mentioned = any(
        token in text
        for token in ["powerpoint", "power point", "ppt", "演示文稿", "幻灯片"]
    )
    if powerpoint_mentioned and close_mentioned:
        return _single_step("不保存修改并关闭 PowerPoint", "presentation_close")
    if powerpoint_mentioned and open_mentioned:
        return _single_step("打开配置的 PowerPoint 演示文稿", "presentation_open_configured")

    teams_mentioned = "teams" in text or "微软团队" in text
    if teams_mentioned and close_mentioned:
        return _single_step("关闭 Microsoft Teams", "system_close_teams")
    if teams_mentioned and open_mentioned:
        return _single_step("打开 Microsoft Teams", "system_open_teams")

    onenote_mentioned = "onenote" in text or "one note" in text or "微软笔记" in text
    if onenote_mentioned and close_mentioned:
        return _single_step("关闭 OneNote", "system_close_onenote")
    if onenote_mentioned and open_mentioned:
        return _single_step("打开 OneNote", "system_open_onenote")

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

    # Unknown text must never launch a browser or any desktop application. The old
    # fallback opened http://localhost:5173, which turned a slightly mis-transcribed
    # “关闭 PPT” into a visible wrong action. Return a non-executable plan instead.
    return [
        PlannedStep(
            index=1,
            title="当前请求没有匹配到确定性桌面工具",
            tool_name=None,
            args={},
        )
    ]
