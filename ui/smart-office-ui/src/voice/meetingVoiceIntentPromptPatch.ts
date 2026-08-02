import { realtimeAgent } from './realtimeAgentRuntime'

let installed = false

export function installMeetingVoiceIntentPromptPatch(): void {
  if (installed) return
  installed = true

  const originalInstructions = realtimeAgent.transcriptionInstructions.bind(realtimeAgent)
  realtimeAgent.transcriptionInstructions = () => `${originalInstructions()}
Meeting-booking commands are supported and must be transcribed faithfully. Relevant Chinese phrases include: 预约会议、会议预约、我想预约会议、帮我预约一下、安排一次会议、安排产品演示、打开预约日历、约个时间、什么时候可以见面. Do not rewrite these requests as questions about whether the feature exists. Relevant English phrases include: book a meeting, schedule a meeting, arrange a demo, open the booking calendar.`
}

installMeetingVoiceIntentPromptPatch()
