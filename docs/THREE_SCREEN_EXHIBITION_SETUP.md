# Smart Office 三屏展会设置（Windows 11）

## 屏幕角色

Windows 显示设置必须使用“扩展这些显示器”，并按物理位置拖成：

1. 左屏：网站与触摸交互窗口
2. 中屏：Smart Office Agent 浏览器窗口
3. 右屏：PowerPoint 放映、Outlook 和其他 Office 窗口

不要依赖 Windows 显示器编号代表物理左右顺序。本项目按显示器桌面坐标选择最左、中间和最右屏。

## 浏览器触摸窗口

建议使用最新版 Microsoft Edge 或 Google Chrome，并允许 `http://127.0.0.1:5173` 弹出窗口和窗口管理权限。

可选前端配置：

```env
VITE_INTERACTION_SCREEN_INDEX=0
VITE_INTERACTION_WINDOW_LEFT=-1920
VITE_INTERACTION_WINDOW_TOP=0
VITE_INTERACTION_WINDOW_WIDTH=1920
VITE_INTERACTION_WINDOW_HEIGHT=1080
```

`VITE_INTERACTION_SCREEN_INDEX=0` 表示按屏幕横坐标排序后的最左屏。其余坐标仅在浏览器 Window Management API 不可用或未授权时作为 fallback。

## Office 右屏配置

Backend 默认选择物理最右屏：

```env
SMART_OFFICE_PRESENTATION_MONITOR_POSITION=rightmost
SMART_OFFICE_MOVE_OUTLOOK_TO_PRESENTATION_MONITOR=true
SMART_OFFICE_OUTLOOK_MONITOR_POLL_SECONDS=1.0
```

启动 Backend 后检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/display-layout | ConvertTo-Json -Depth 8
```

确认：

- `display_count` 为 3
- `office_role` 为 `rightmost`
- `office_target_device` 等于最右屏的设备名
- PowerPoint 实际状态中的 `monitor_placement_enforced` 为 `true`

## 展会启动顺序

1. 在 Windows 显示设置中完成三屏物理排列。
2. 启动 Backend。
3. 访问 `/api/display-layout` 验证角色。
4. 在中屏打开 Agent 页面并全屏。
5. 在左屏打开网站。
6. 首次点击 Agent 右侧任一触摸功能按钮，允许浏览器弹窗和窗口管理权限。
7. 测试 PowerPoint 放映和 Outlook 窗口是否进入右屏。

语音命令打开触摸窗口受浏览器 popup 策略约束。首次必须通过界面按钮建立并授权命名窗口；之后语音指令可以复用该窗口。若浏览器仍阻止，请为本地站点显式允许弹窗。
