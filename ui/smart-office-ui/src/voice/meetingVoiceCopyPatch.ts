import { voiceOutputManager } from './voiceOutputManager'

const ZH_OLD = '我已经在 Sara 左侧打开会议预约日历。请选择日期，再点击绿色的可用时间段。'
const ZH_NEW = '我已经在 Sara 左侧打开会议预约日历。请先选择日期，然后在下一页选择会面时间。'
const EN_OLD = 'The meeting-booking calendar is open beside Sara. Select a date and then choose a green available time slot.'
const EN_NEW = 'The meeting-booking calendar is open beside Sara. Select a date, then choose a meeting time on the next page.'

let installed = false

export function installMeetingVoiceCopyPatch(): void {
  if (installed) return
  installed = true

  const originalSpeak = voiceOutputManager.speak.bind(voiceOutputManager)
  voiceOutputManager.speak = async (text, language, options) => {
    const corrected = text
      .replace(ZH_OLD, ZH_NEW)
      .replace(EN_OLD, EN_NEW)
    return await originalSpeak(corrected, language, options)
  }
}

installMeetingVoiceCopyPatch()
