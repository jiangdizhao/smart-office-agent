# Smart Office：Teams、OneNote 与本地音乐控制配置

本功能运行在 i5 Windows Backend 中，不属于 Vite 前端配置。环境变量应在启动 Backend 的 PowerShell 会话、启动脚本或 Windows 用户环境中设置。

## 支持的语音意图

- 播放音乐 / 随机播放一首歌 / play music
- 关闭音乐 / 停止音乐 / stop music
- 打开 Teams / open Microsoft Teams
- 关闭 Teams / exit Teams
- 打开 OneNote / open OneNote
- 关闭 OneNote / exit OneNote

这些高频命令走确定性匹配，不依赖模型自由生成应用名称或系统命令。

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

执行“播放音乐”时，Backend 会递归扫描目录，随机选择一首，并通过 Windows 当前默认音频文件关联打开。因此，部署前应确认一首音频文件可以通过双击正常在目标“媒体播放器”中播放。

## 可选应用启动命令

通常无需设置。自动发现顺序为：显式命令、PATH、常见安装路径、Windows 应用 URI。

必要时可指定：

```powershell
$env:SMART_OFFICE_TEAMS_COMMAND = "C:\Path\To\ms-teams.exe"
$env:SMART_OFFICE_ONENOTE_COMMAND = "C:\Program Files\Microsoft Office\root\Office16\ONENOTE.EXE"
```

变量既可以是可执行文件路径，也可以包含固定启动参数。

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

默认窗口关键词包括随机歌曲文件名、`Media Player`、`Windows Media Player` 和 `VLC`。也可以覆盖：

```powershell
$env:SMART_OFFICE_MEDIA_PLAYER_WINDOW_KEYWORDS = "媒体播放器,Media Player"
```

## 关闭策略

系统先向匹配窗口发送正常关闭请求。若应用仍在后台运行，展会模式默认使用 `taskkill /T /F` 完成关闭并再次验证。

禁止强制关闭：

```powershell
$env:SMART_OFFICE_FORCE_CLOSE_MANAGED_APPS = "false"
```

关闭 OneNote 前应避免在演示环境中保留未同步的重要内容。

## 结果验证范围

每项工具只检查自己的必要状态：

- Teams：Teams 进程或窗口
- OneNote：OneNote 进程或窗口
- 音乐：所选歌曲、媒体播放器进程或窗口
- 关闭操作：对应进程和窗口是否消失

不会读取 PowerPoint、屏幕亮度、Outlook 或摘要文件状态。

## 部署检查

```powershell
cd D:\smart-office-agent

# 可选：指定音乐目录
$env:SMART_OFFICE_MUSIC_DIRECTORY = "D:\SmartOfficeMedia\Music"

# 启动 Backend 后，在另一个终端测试
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/agent/run `
  -ContentType "application/json" `
  -Body '{"text":"播放音乐","execute":true}' |
  ConvertTo-Json -Depth 20
```

依次测试播放/关闭音乐、打开/关闭 Teams、打开/关闭 OneNote，并检查返回结果中的：

```text
ok
data.verified
data.application
data.action
data.selected_track_name
data.remaining_processes
data.remaining_windows
```
