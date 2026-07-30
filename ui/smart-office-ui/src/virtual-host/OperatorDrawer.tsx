import type { ProximityGreetingController } from '../vision/useProximityGreeting'
import type { VoiceOutputProvider } from '../voice/voiceOutputManager'
import type {
  OfficeActor,
  OfficeAsrProvider,
  OfficeVoiceController,
} from '../voice/useOfficeVoiceController'

type OperatorDrawerProps = {
  controller: OfficeVoiceController
  proximity: ProximityGreetingController
  onClose: () => void
}

function proximityLabel(
  status: ProximityGreetingController['status'],
  zh: boolean,
): string {
  const labels: Record<ProximityGreetingController['status'], { zh: string; en: string }> = {
    disabled: { zh: '已关闭', en: 'Disabled' },
    starting: { zh: '正在启动本地视觉检测', en: 'Starting local vision detection' },
    watching: { zh: '本地视觉正在等待访客', en: 'Local vision is watching for a visitor' },
    blocked: { zh: '浏览器摄像头权限被拒绝', en: 'Browser camera permission denied' },
    unsupported: { zh: '当前浏览器不支持本地视觉', en: 'Local vision is unsupported by this browser' },
    error: { zh: '访客感应出现错误', en: 'Visitor detection error' },
    stopped: { zh: '已停止', en: 'Stopped' },
    connecting: { zh: '正在连接 RTX 视觉服务器', en: 'Connecting to the RTX vision server' },
    connected: { zh: 'RTX 视觉服务器已连接', en: 'RTX vision server connected' },
    reconnecting: { zh: 'RTX 视觉连接中断，正在重连', en: 'RTX vision disconnected; reconnecting' },
    offline: { zh: 'RTX 视觉服务器不可用', en: 'RTX vision server unavailable' },
    fallback: { zh: '正在使用浏览器 MediaPipe 备用路径', en: 'Using the browser MediaPipe fallback' },
  }
  return labels[status][zh ? 'zh' : 'en']
}

export default function OperatorDrawer({
  controller,
  proximity,
  onClose,
}: OperatorDrawerProps) {
  const zh = controller.language === 'zh'
  const settingsDisabled = controller.listening || controller.busy
  const voiceActive = controller.runtime.outputActive || controller.panel === 'speaking'
  const serviceReady = controller.runtime.connected

  return (
    <div className="operator-drawer-layer">
      <button
        type="button"
        className="drawer-backdrop"
        aria-label={zh ? '关闭设置' : 'Close settings'}
        onClick={onClose}
      />
      <aside className="operator-drawer exhibition-operator-drawer" aria-label={zh ? '设置' : 'Settings'}>
        <div className="drawer-heading exhibition-drawer-heading">
          <div>
            <span>{zh ? '展会控制' : 'Exhibition controls'}</span>
            <strong>{zh ? '语音、视觉与界面设置' : 'Voice, vision and interface settings'}</strong>
          </div>
          <button type="button" onClick={onClose} aria-label={zh ? '关闭' : 'Close'}>
            ×
          </button>
        </div>

        <section className="drawer-section" aria-labelledby="language-setting-title">
          <div className="drawer-section-heading">
            <strong id="language-setting-title">{zh ? '界面语言' : 'Interface language'}</strong>
            <span>{zh ? '切换后立即生效' : 'Applies immediately'}</span>
          </div>
          <div className="drawer-segmented-control">
            <button
              type="button"
              className={controller.language === 'zh' ? 'selected' : ''}
              disabled={settingsDisabled}
              onClick={() => controller.setLanguage('zh')}
            >
              中文
            </button>
            <button
              type="button"
              className={controller.language === 'en' ? 'selected' : ''}
              disabled={settingsDisabled}
              onClick={() => controller.setLanguage('en')}
            >
              English
            </button>
          </div>
        </section>

        <section className="drawer-section" aria-labelledby="actor-setting-title">
          <div className="drawer-section-heading">
            <strong id="actor-setting-title">{zh ? '演示身份' : 'Demonstration role'}</strong>
            <span>{zh ? '办公控制请使用 Employee' : 'Use Employee for Office control'}</span>
          </div>
          <label className="drawer-field">
            <span>{zh ? '当前身份' : 'Current role'}</span>
            <select
              value={controller.actor}
              disabled={settingsDisabled}
              onChange={(event) => controller.setActor(event.target.value as OfficeActor)}
            >
              <option value="visitor">Visitor</option>
              <option value="employee">Employee</option>
              <option value="operator">Operator</option>
            </select>
          </label>
        </section>

        <section className="drawer-section" aria-labelledby="proximity-setting-title">
          <div className="drawer-section-heading">
            <strong id="proximity-setting-title">
              {zh ? '空闲访客主动问候' : 'Idle visitor greeting'}
            </strong>
            <span>
              {proximity.source === 'remote'
                ? zh
                  ? '由 RTX 视觉服务器的 Primary、engaged、face 和 visitor session 状态驱动'
                  : 'Driven by RTX Primary, engaged, face and visitor-session state'
                : zh
                  ? '当前使用浏览器本地视觉备用路径'
                  : 'Currently using the browser-local vision fallback'}
            </span>
          </div>
          <div className="drawer-segmented-control">
            <button
              type="button"
              className={proximity.enabled ? 'selected' : ''}
              disabled={settingsDisabled}
              onClick={() => proximity.setEnabled(true)}
            >
              {zh ? '开启' : 'On'}
            </button>
            <button
              type="button"
              className={!proximity.enabled ? 'selected' : ''}
              disabled={settingsDisabled}
              onClick={() => proximity.setEnabled(false)}
            >
              {zh ? '关闭' : 'Off'}
            </button>
          </div>
          <p className="drawer-inline-note">
            {proximityLabel(proximity.status, zh)}
            {proximity.lastDetection
              ? ` · ${zh ? '人体' : 'person'} ${(proximity.lastDetection.body_area_ratio * 100).toFixed(0)}% · ${proximity.lastDetection.face_inside_body ? (zh ? '有人脸' : 'face found') : (zh ? '未检测到人脸' : 'no face')}`
              : ''}
          </p>
          {proximity.endpoint ? (
            <p className="drawer-inline-note">{zh ? '远程端点' : 'Remote endpoint'}: {proximity.endpoint}</p>
          ) : null}
          {proximity.detail ? <p className="drawer-inline-note">{proximity.detail}</p> : null}
        </section>

        <section className="drawer-section" aria-labelledby="asr-setting-title">
          <div className="drawer-section-heading">
            <strong id="asr-setting-title">{zh ? '语音识别' : 'Speech recognition'}</strong>
            <span>{zh ? '不会自动切换识别方式' : 'No automatic provider switching'}</span>
          </div>
          <label className="drawer-field">
            <span>{zh ? '识别方式' : 'Recognition provider'}</span>
            <select
              value={controller.asr}
              disabled={settingsDisabled}
              onChange={(event) => controller.setAsr(event.target.value as OfficeAsrProvider)}
            >
              <option value="realtime">GPT Realtime</option>
              <option value="browser" disabled={!controller.browserAsrAvailable}>
                {zh ? '浏览器语音识别' : 'Browser speech recognition'}
              </option>
            </select>
          </label>
          {!controller.browserAsrAvailable ? (
            <p className="drawer-inline-note">
              {zh
                ? '当前浏览器不支持备用的浏览器语音识别。'
                : 'Browser speech recognition is unavailable in this browser.'}
            </p>
          ) : null}
        </section>

        <section className="drawer-section" aria-labelledby="voice-output-title">
          <div className="drawer-section-heading">
            <strong id="voice-output-title">{zh ? '语音输出' : 'Playback mode'}</strong>
            <span>{zh ? '默认使用 GPT Realtime' : 'GPT Realtime by default'}</span>
          </div>
          <label className="drawer-field">
            <span>{zh ? '朗读方式' : 'Playback mode'}</span>
            <select
              value={controller.voice}
              disabled={settingsDisabled}
              onChange={(event) =>
                void controller.setVoice(event.target.value as VoiceOutputProvider)
              }
            >
              <option value="realtime">GPT Realtime</option>
              <option value="none">{zh ? '仅显示文字' : 'Text only'}</option>
            </select>
          </label>
        </section>

        <section className="drawer-section drawer-service-section" aria-label={zh ? '当前状态' : 'Current status'}>
          <div className={`drawer-service-status ${serviceReady ? 'ready' : ''}`}>
            <i aria-hidden="true" />
            <div>
              <strong>
                {serviceReady
                  ? zh
                    ? '语音服务已就绪'
                    : 'Voice service is ready'
                  : zh
                    ? '语音服务将在首次使用时连接'
                    : 'Voice connects on first use'}
              </strong>
              <span>
                {controller.runtime.microphoneAttached
                  ? zh
                    ? '麦克风正在使用'
                    : 'Microphone in use'
                  : zh
                    ? '麦克风处于空闲状态'
                    : 'Microphone is idle'}
              </span>
            </div>
          </div>

          {!serviceReady ? (
            <button
              type="button"
              className="drawer-primary-action"
              disabled={settingsDisabled}
              onClick={() => void controller.connect()}
            >
              {controller.panel === 'connecting'
                ? zh
                  ? '正在连接…'
                  : 'Connecting…'
                : zh
                  ? '提前连接语音服务'
                  : 'Connect voice service'}
            </button>
          ) : null}

          <div className="drawer-runtime-actions">
            <button
              type="button"
              disabled={!voiceActive}
              onClick={() => void controller.stopSpeaking()}
            >
              {zh ? '停止朗读' : 'Stop speaking'}
            </button>
            <button
              type="button"
              className="drawer-danger-action"
              disabled={!controller.active}
              onClick={() => void controller.approve('cancel')}
            >
              {zh ? '取消当前任务' : 'Cancel current task'}
            </button>
          </div>
        </section>

        <div className="drawer-footer">
          <span>{zh ? '主屏不会显示任务编号、路由或验证详情。' : 'Task IDs, routes, and verification details stay off the main screen.'}</span>
          <button type="button" onClick={() => window.location.assign('/debug')}>
            {zh ? '维护入口' : 'Maintenance'}
          </button>
        </div>
      </aside>
    </div>
  )
}
