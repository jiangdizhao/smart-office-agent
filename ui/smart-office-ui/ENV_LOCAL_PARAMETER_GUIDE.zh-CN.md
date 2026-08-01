# Smart Office i5 `.env.local` 参数说明与调节参考

适用目录：`ui/smart-office-ui/.env.local`

修改 `.env.local` 后，需要停止并重新启动 Vite；生产构建需要重新执行 `npm run build`。这些变量由 Vite 在启动或构建时注入，不是运行时热加载配置。

## 1. 推荐基线

```env
VITE_PROXIMITY_BODY_AREA_RATIO=0.10
VITE_PROXIMITY_MIN_BODY_CONFIDENCE=0.60
VITE_PROXIMITY_MIN_FACE_CONFIDENCE=0.10
VITE_PROXIMITY_DEBUG=true
VITE_PROXIMITY_REARM_ABSENCE_SECONDS=1

VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_VISION_SOURCE=remote
VITE_VISION_SERVER_HTTP=http://192.168.110.13:8015
VITE_VISION_SERVER_WS=ws://192.168.110.13:8015/ws/v1/events

VITE_REMOTE_VISION_ENTRY_MIN_BODY_AREA_RATIO=0.10
VITE_REMOTE_VISION_HOLD_MIN_BODY_AREA_RATIO=0.045

VITE_REALTIME_VAD_THRESHOLD=0.72
VITE_REALTIME_VAD_SILENCE_MS=650
VITE_REALTIME_VAD_PREFIX_MS=420
VITE_REALTIME_BARGE_IN_GRACE_MS=350
VITE_PRIMARY_MIC_DEVICE_ID=

enable_greet=true
```

请把示例中的 RTX IP 地址替换为现场 Vision Edge Server 的实际局域网地址。

## 2. Remote Vision 主路径参数

### `VITE_REMOTE_VISION_ENTRY_MIN_BODY_AREA_RATIO`

含义：RTX 返回的 Primary Visitor 人体框面积占整幅摄像头画面的最小比例。新访客只有达到这个阈值，i5 才允许主动欢迎并建立接待会话。

默认值：`0.10`，即人体框约占画面面积 10%。

推荐范围：

- 普通办公室、摄像头距离约 1.5–2.5 米：`0.08–0.12`
- 展会现场、需要排除远处路人：`0.10–0.16`
- 摄像头视角很广，正常访客人体框较小：`0.06–0.10`

调整规律：

- 提高：更不容易把远处行人当作访客，但正常访客需要更靠近。
- 降低：更早触发欢迎，但更容易被远处人员触发。

现场判断：让一名测试者从远处逐步靠近，观察 Operator Drawer 中的 `body_area_ratio`。将阈值设在“正常交互位置”数值的约 70%–85%。

### `VITE_REMOTE_VISION_HOLD_MIN_BODY_AREA_RATIO`

含义：会话建立后，用于维持当前访客的较低面积阈值。它形成进入/保持双阈值 hysteresis，避免访客轻微后退时会话立即结束。

默认值：`0.045`。

推荐范围：`0.03–0.08`。

必须满足：`HOLD <= ENTRY`。代码会自动把错误配置限制到不高于 ENTRY。

调整规律：

- 提高：访客后退时更快被判定为离场。
- 降低：访客可在更大范围内移动，但可能让远处残留轨迹继续维持会话。

建议起点：ENTRY 为 `0.10` 时，HOLD 先使用 `0.04–0.05`。

低于 HOLD 的远程 Primary 会在浏览器控制台产生：

```text
[ProximityDebug] remote-primary-below-hold-area
```

### `VITE_VISION_SOURCE`

- `remote`：正式路径，只使用 RTX Vision Edge Server。
- `remote-with-fallback`：RTX 长时间不可用后启用浏览器 MediaPipe。
- `mediapipe`：仅浏览器本地视觉。
- `disabled`：关闭视觉触发。

展会正式部署建议：`remote`。

### `VITE_VISION_SERVER_HTTP` / `VITE_VISION_SERVER_WS`

RTX Vision Edge Server 的 HTTP 与 WebSocket 地址。两台电脑必须处于可互通的局域网内；RTX 服务需监听 `0.0.0.0:8015`。

## 3. 手持麦克风与 GPT Realtime VAD 参数

### `VITE_REALTIME_VAD_THRESHOLD`

含义：GPT Realtime Server VAD 的语音活动阈值。它是当前“最小声音触发灵敏度”的主要部署参数。

默认值：`0.72`。

推荐范围：`0.55–0.88`。

- 安静办公室：`0.60–0.72`
- 一般展会噪声：`0.72–0.82`
- 背景人声很强、使用近讲手持麦克风：`0.78–0.88`

调整规律：

- 提高：减少背景声音和远处说话触发，但可能漏掉较轻的近讲语音。
- 降低：更容易听到轻声访客，也更容易被环境声误触发。

重要限制：该阈值不是声纹识别，也不能证明说话者就是 Primary Guest。Primary Speaker 仍主要依赖手持近讲麦克风、麦克风方向性和现场使用纪律。

### `VITE_REALTIME_VAD_SILENCE_MS`

含义：检测到多少毫秒连续静音后，认为本轮访客发言结束。

默认值：`650` ms。

推荐范围：`500–1000` ms。

- 太低：正常停顿会被切成两轮话。
- 太高：访客说完后等待时间变长。
- 中文自然对话建议：`600–800` ms。

### `VITE_REALTIME_VAD_PREFIX_MS`

含义：VAD 触发前保留的音频前缀，用于避免吞掉第一个字或第一个英文音节。

默认值：`420` ms。

推荐范围：`300–600` ms。

- 开头经常缺字：提高到 `480–600`。
- 背景声音经常被带入：降低到 `300–400`。

### `VITE_REALTIME_BARGE_IN_GRACE_MS`

含义：Sara 开始播放语音后的短暂保护时间。在这段时间内出现的 VAD 触发不会立即被视为访客打断，主要用于防止扬声器刚起声时自触发。

默认值：`350` ms。

推荐范围：`250–600` ms。

- Sara 经常刚开口就被自己打断：提高。
- 用户需要非常快速地打断 Sara：降低。

### `VITE_PRIMARY_MIC_DEVICE_ID`

含义：指定首选麦克风的浏览器 `deviceId`。留空时使用系统或浏览器默认输入设备。

建议：现场固定手持麦克风后填写设备 ID，避免 Windows 更新或插拔设备后错误选择笔记本内置麦克风。

注意：浏览器设备 ID 只有在授予麦克风权限后才稳定可见。

## 4. Browser MediaPipe fallback 参数

以下参数只控制浏览器本地 MediaPipe fallback，不控制 RTX remote 主路径：

- `VITE_PROXIMITY_BODY_AREA_RATIO`
- `VITE_PROXIMITY_MIN_BODY_CONFIDENCE`
- `VITE_PROXIMITY_MIN_FACE_CONFIDENCE`
- `VITE_PROXIMITY_REARM_ABSENCE_SECONDS`

正式使用 `VITE_VISION_SOURCE=remote` 时，现场距离主要调节新的 `VITE_REMOTE_VISION_ENTRY_MIN_BODY_AREA_RATIO` 和 `VITE_REMOTE_VISION_HOLD_MIN_BODY_AREA_RATIO`。

`VITE_PROXIMITY_DEBUG=true` 会将 `[ProximityDebug]` 日志转发到 Backend terminal，适合部署调试。展会稳定后可改为 `false` 以减少日志量。

## 5. 建议的现场调节顺序

1. 先固定摄像头位置、焦距、分辨率和人与屏幕的目标站位。
2. 保持 `ENTRY=0.10`、`HOLD=0.045`，观察正常站位和远处路人的 `body_area_ratio`。
3. 先调 ENTRY，使远处路人不触发、正常访客可靠触发。
4. 再调 HOLD，确认访客轻微后退或侧身时会话不会抖动。
5. 固定手持麦克风，先用 `VAD_THRESHOLD=0.72`。
6. 在无访客、旁人交谈和访客近讲三种场景下测试。
7. 背景误触发时每次增加约 `0.03–0.05`；近讲漏识别时每次降低约 `0.03`。
8. 最后微调 `SILENCE_MS`、`PREFIX_MS` 和 `BARGE_IN_GRACE_MS`。

## 6. 两套参考配置

### 安静办公室

```env
VITE_REMOTE_VISION_ENTRY_MIN_BODY_AREA_RATIO=0.08
VITE_REMOTE_VISION_HOLD_MIN_BODY_AREA_RATIO=0.035
VITE_REALTIME_VAD_THRESHOLD=0.65
VITE_REALTIME_VAD_SILENCE_MS=650
VITE_REALTIME_VAD_PREFIX_MS=420
VITE_REALTIME_BARGE_IN_GRACE_MS=300
```

### 噪声较大的展会 + 手持近讲麦克风

```env
VITE_REMOTE_VISION_ENTRY_MIN_BODY_AREA_RATIO=0.12
VITE_REMOTE_VISION_HOLD_MIN_BODY_AREA_RATIO=0.05
VITE_REALTIME_VAD_THRESHOLD=0.80
VITE_REALTIME_VAD_SILENCE_MS=720
VITE_REALTIME_VAD_PREFIX_MS=460
VITE_REALTIME_BARGE_IN_GRACE_MS=420
```

这些值是部署起点，不是绝对标准。最终数值必须在实际屏幕、摄像头高度、镜头视角、麦克风增益和现场噪声条件下确定。
