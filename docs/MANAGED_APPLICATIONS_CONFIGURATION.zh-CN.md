# Smart Office：桌面应用、右侧内容屏幕与本地音乐配置

本功能运行在 i5 Windows Backend 中，不属于 Vite 前端配置。环境变量应在启动 Backend 的 PowerShell 会话、启动脚本或 Windows 用户环境中设置。

## 支持的语音意图

- 播放音乐 / 随机播放一首歌 / play music
- 关闭音乐 / 停止音乐 / stop music
- 打开 Teams / open Microsoft Teams
- 关闭 Teams / exit Teams
- 打开 OneNote / open OneNote
- 关闭 OneNote / exit OneNote
- 打开 PowerPoint / 打开 PPT
- 关闭 PowerPoint / 关闭 PPT

这些高频命令走确定性匹配。中文会话中的 Teams、OneNote、PowerPoint、Outlook 等英文产品名不会单独触发英文回复。已知的 `关闭 Teams -> one B Teams` 等有限领域误识别会在应用名明确时恢复为中文命令；应用名明确但动作不明确时，Sara 会询问“打开还是关闭”，不会进入普通英文聊天。

## 右侧内容显示器

Teams、OneNote、媒体播放器、PowerPoint 编辑窗口、PowerPoint 放映窗口和 Outlook 草稿窗口都会执行以下后置条件：

```text
找到真实顶层窗口
→ 恢复最小化窗口
→ 移动到内容显示器
→ 最大化
→ 激活到前台
→ 验证窗口可见、未最小化、已最大化且位于目标显示器
```

默认不依赖 `DISPLAY1/2/3` 编号，而是根据 Windows 虚拟桌面坐标选择物理位置最右侧的显示器。

需要固定到特定显示器时设置：

```powershell
$env:SMART_OFFICE_CONTENT_MONITOR_DEVICE = "\\.\DISPLAY3"
```

可通过 Windows 显示设置、PowerShell 或 Backend 返回结果确认实际设备名。若不设置该变量，系统自动使用物理最右侧显示器。

窗口发现和放置的默认等待时间为 10 秒。可调整：

```powershell
$env:SMART_OFFICE_WINDOW_PLACEMENT_TIMEOUT_SECONDS = "12"
```

打开操作只有在窗口位置和最大化状态通过验证后才报告成功。仅发现后台进程或任务栏图标不再视为成功。

## 音乐目录

默认目录：

```text
<repository>\data\music
```

也可以设置：

```powershell
$env:SMART_OFFICE_MUSIC_DIRECTORY = "D:\SmartOfficeMedia\Music"
```

支持：

```text
.mp3 .wav .m4a .flac .wma .aac .ogg
```

执行“播放音乐”时，Backend 会递归扫描目录，随机选择一首，通过 Windows 当前默认音频文件关联打开，并将播放器窗口移动到右侧内容屏幕后最大化。部署前应确认音频文件可以通过双击在目标“媒体播放器”中正常播放。

## 可选应用启动命令

通常无需设置。自动发现顺序为：显式命令、PATH、常见安装路径、Windows 应用 URI。

必要时可指定：

```powershell
$env:SMART_OFFICE_TEAMS_COMMAND = "C:\Path\To\ms-teams.exe"
$env:SMART_OFFICE_ONENOTE_COMMAND = "C:\Program Files\Microsoft Office\root\Office16\ONENOTE.EXE"
```

变量既可以是可执行文件路径，也可以包含固定启动参数。Teams 或 OneNote 只有后台进程、没有可见窗口时，系统会重新调用其 Windows 应用 URI，再次寻找、移动并最大化真实主窗口。

## 媒体播放器识别

默认识别以下进程：

```text
Microsoft.Media.Player.exe
MediaPlayer.exe
Music.UI.exe
wmplayer.exe
vlc.exe
```

现场播放器进程名不在列表中时：

```powershell
$env:SMART_OFFICE_MEDIA_PLAYER_PROCESS_NAMES = "YourPlayer.exe,OtherPlayer.exe"
```

默认窗口关键词包括随机歌曲文件名、`Media Player`、`Windows Media Player`、`VLC` 和“媒体播放器”。也可以覆盖：

```powershell
$env:SMART_OFFICE_MEDIA_PLAYER_WINDOW_KEYWORDS = "媒体播放器,Media Player"
```

## 关闭策略

Teams、OneNote 和媒体播放器先收到正常窗口关闭请求。若仍在后台运行，展会模式默认使用 `taskkill /T /F` 完成关闭并再次验证。

禁止这些应用使用强制关闭：

```powershell
$env:SMART_OFFICE_FORCE_CLOSE_MANAGED_APPS = "false"
```

关闭 OneNote 前应避免在演示环境中保留未同步的重要内容。

### PowerPoint 特殊规则

“关闭 PowerPoint”是明确的展会命令，语义为：

```text
退出所有幻灯片放映
→ 将所有打开的演示文稿标记为无需保存
→ 关闭演示文稿
→ 退出 PowerPoint
→ 必要时强制结束残留 POWERPNT.EXE
→ 验证进程已退出
```

未保存修改会直接丢弃，不显示保存提示。该规则只用于用户明确要求关闭 PowerPoint 的场景。

## Outlook 草稿窗口

Outlook 草稿仍经过原有收件人白名单和审批流程。草稿通过 COM 保存和重新验证后，Inspector 草稿窗口会被移动到右侧内容屏幕并最大化。草稿存在但窗口未成功显示在目标屏幕时，整个可见性后置条件会报告失败，而不会仅凭草稿 EntryID 声称演示完成。

## 结果验证范围

每项工具只检查自己的必要状态：

- Teams：真实可见主窗口、目标显示器、最大化状态；关闭时检查进程和窗口消失。
- OneNote：真实可见主窗口、目标显示器、最大化状态；关闭时检查进程和窗口消失。
- 音乐：随机歌曲、播放器窗口、目标显示器、最大化状态；关闭时检查受控播放器退出。
- PowerPoint：演示文稿/放映状态、窗口位置和最大化状态；明确关闭时检查 PowerPoint 退出。
- Outlook：草稿保存与收件人验证、草稿窗口位置和最大化状态。

各工具不会查询与本操作无关的屏幕亮度、邮件、演示文稿或摘要状态。

## 部署检查

```powershell
cd D:\smart-office-agent

# 可选：指定音乐目录
$env:SMART_OFFICE_MUSIC_DIRECTORY = "D:\SmartOfficeMedia\Music"

# 可选：明确指定右侧内容显示器；不设置则自动选物理最右侧
# $env:SMART_OFFICE_CONTENT_MONITOR_DEVICE = "\\.\DISPLAY3"

# 启动 Backend 后，在另一个终端测试
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/agent/run `
  -ContentType "application/json" `
  -Body '{"text":"打开 Teams","execute":true}' |
  ConvertTo-Json -Depth 30
```

依次测试：

```text
打开 Teams
关闭 Teams
打开 OneNote
关闭 OneNote
播放音乐
关闭音乐
打开 PowerPoint
关闭 PowerPoint
```

重点检查：

```text
ok
data.verified
data.window_placement_verified
data.content_monitor_device
data.window_placement.observed_monitor_device
data.window_placement.window_maximized
data.remaining_processes
data.remaining_windows
data.remaining_powerpoint_pids
```
