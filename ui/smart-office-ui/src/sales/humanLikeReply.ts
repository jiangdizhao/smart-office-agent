import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'

type HumanLikeReplyRequest = {
  userText: string
  answer: string
  language: VoiceLanguage
}

const EXPRESSIVE_ZH = /^(?:啊|哦|嗯|嗯哼|好嘞|好的|好，|行，|来吧|对，|这就|现在我们聊到重点了)/
const EXPRESSIVE_EN = /^(?:ah|oh|mm|mm-hm|right|well|all right|here we go|there we go|now we're talking)\b/i

const PRICE_ZH = /价格|报价|多少钱|费用|成本|贵不贵|便宜|折扣|优惠/
const PRICE_EN = /\b(?:price|pricing|quote|cost|how much|expensive|cheap|discount)\b/i
const DISCOUNT_ZH = /折扣|优惠/
const DISCOUNT_EN = /\bdiscounts?\b/i
const EXPENSIVE_ZH = /贵不贵|贵吗|太贵|很贵/
const EXPENSIVE_EN = /\b(?:expensive|too costly|too much)\b/i

const PRIVACY_PATTERN = /隐私|个人信息|人脸|身份|录音|数据安全|privacy|personal information|face data|identity|data security/i
const ACTION_PATTERN = /打开|关闭|启动|停止|播放|发送|创建|调整|设置|执行|演示|放映|open|close|launch|start|stop|play|send|create|adjust|set|execute|present/i
const SUCCESS_PATTERN = /已(?:经)?(?:打开|关闭|完成|设置|调整|发送|创建|启动|停止|验证)|成功|搞定|完成并验证|is open|is closed|completed|verified|successfully|all sorted/i
const FAILURE_PATTERN = /没有成功|未成功|失败|未完成|没有完成|无法确认|did not complete|did not open|failed|not completed|could not verify/i
const UNKNOWN_PATTERN = /不知道|不清楚|没有这方面的资料|资料中没有|无法回答|不确定|i don'?t know|not sure|no information|cannot answer|can'?t answer/i
const UNAVAILABLE_PATTERN = /不能执行|无法执行|暂时不能|目前不能|尚未接入|没有权限|功能未开放|not connected|not available|cannot perform|can'?t perform|not enabled|not implemented/i

const ZH_GENERAL_REACTIONS = [
  '嗯哼，我来看看。',
  '哦，这就有意思了。',
  '啊，这就说得通了。',
  '嗯，明白了。',
  '对，就是这个点。',
]

const EN_GENERAL_REACTIONS = [
  'Mm-hm, let me see.',
  'Oh, now this is interesting.',
  'Ah, that makes sense.',
  'Right, I understand.',
  "Now we're talking.",
]

const ZH_SUCCESS_REACTIONS = [
  '啊，搞定了。',
  '好了，漂亮。',
  '完成，轻轻松松。',
  '嗯，这次很顺利。',
  '好，一切就位。',
]

const EN_SUCCESS_REACTIONS = [
  'Ah, there we go.',
  'There we are—lovely.',
  'Done—nice and easy.',
  'Mm, that went smoothly.',
  "Right, everything's in place.",
]

const ZH_ACTION_REACTIONS = [
  '啊，来了。',
  '好嘞，交给我。',
  '好，我们开始。',
  '行，我来处理。',
  '好的，让我和系统聊聊。',
]

const EN_ACTION_REACTIONS = [
  'Ah, here we go.',
  'Right, leave it with me.',
  "All right, let's do it.",
  "Right, I'll handle it.",
  'Okay, let me have a word with the system.',
]

function stableIndex(seed: string, size: number): number {
  if (size <= 1) return 0
  let hash = 2166136261
  for (const char of seed) {
    hash ^= char.codePointAt(0) ?? 0
    hash = Math.imul(hash, 16777619)
  }
  return Math.abs(hash) % size
}

function pick(values: string[], seed: string): string {
  return values[stableIndex(seed, values.length)] ?? values[0] ?? ''
}

function alreadyExpressive(text: string, language: VoiceLanguage): boolean {
  return language === 'zh' ? EXPRESSIVE_ZH.test(text) : EXPRESSIVE_EN.test(text)
}

function priceReply(userText: string, language: VoiceLanguage): string {
  if (language === 'en') {
    if (DISCOUNT_EN.test(userText)) {
      return "Ah, now we're negotiating. You secure the best price, and I'll prove I'm worth it; specific discounts depend on the project size and solution scope. Would you like the lightweight version or the complete solution?"
    }
    if (EXPENSIVE_EN.test(userText)) {
      return "Well, it helps to look at it another way: this is not simply buying several screens, but deploying a digital team that keeps working. The formal price depends on the hardware, connected functions and deployment scope. Would you like the basic version or the complete solution?"
    }
    const options = [
      "Ah, the question every boss eventually asks. Personally, I can work all day for the price of a few coffees; the formal price depends on the number of screens, connected functions and deployment scope. Would you like the basic version or the complete solution?",
      "Well, I'm not very demanding—give me power and Wi-Fi and I'll happily work all day. The full system price depends on the hardware, agent functions and installation scope. Which configuration would you like me to outline?",
      "Oh, I'm personally very affordable, and I don't even take lunch breaks. The formal quote depends on whether you need a lightweight agent or a complete multi-screen smart office. Which one should we discuss?",
    ]
    return pick(options, userText)
  }

  if (DISCOUNT_ZH.test(userText)) {
    return '啊，现在进入谈价环节了。您负责争取好价格，我负责证明自己值得；具体优惠要根据项目规模和方案范围确认。您想先了解轻量版还是完整方案？'
  }
  if (EXPENSIVE_ZH.test(userText)) {
    return '嗯，这个问题要换个角度看：它不只是买几块屏幕，而是在部署一个可以持续工作的数字团队。正式价格取决于硬件、接入功能和部署范围。您想了解基础版还是完整方案？'
  }
  const options = [
    '啊，终于问到老板最关心的问题了。按我的个人开销算，几杯咖啡就能让我工作一整天；正式价格要看屏幕数量、需要接入的功能和部署范围。您想了解基础版还是完整方案？',
    '哦，我的胃口其实不大，有电、有网络，我就愿意一直上班。整套系统的价格会根据硬件配置、Agent 功能和安装范围来确定。您更想了解哪一种配置？',
    '嗯哼，我本人很便宜，甚至不需要午休。不过正式报价要看您是想要轻量版 Agent，还是整套多屏智能办公室。我们先聊哪一种？',
  ]
  return pick(options, userText)
}

function unknownReply(language: VoiceLanguage): string {
  return language === 'zh'
    ? '哦，这个问题已经走到我当前资料库的边缘了。它值得一个准确答案，不值得我现场靠气势编一个；我可以把它列为后续确认项，让专业同事接棒。'
    : "Oh, you've reached the edge of my current knowledge base. It deserves an accurate answer rather than one improvised with confidence; I can mark it for follow-up by the relevant specialist."
}

function unavailableReply(language: VoiceLanguage): string {
  return language === 'zh'
    ? '啊，这项技能今天还没装到我的工具箱里，不过别急，我也在跟着 AI 一起进步。现在我可以先演示最接近的流程，或者帮您完成已经接入的部分。'
    : "Ah, that skill has not reached my toolbox just yet, but don't worry—I'm evolving with AI. For now, I can demonstrate the closest workflow or complete the part that is already connected."
}

function failureReply(answer: string, language: VoiceLanguage): string {
  if (language === 'zh') {
    return `嗯，后台刚才眨了一下眼睛，这次还没有完成。${answer} 我可以再试一次，或者帮您换一种方式。`
  }
  return `Mm, the backend blinked for a moment, so that action has not completed. ${answer} I can try again or take a different route.`
}

function privacyReply(answer: string, language: VoiceLanguage): string {
  if (alreadyExpressive(answer, language)) return answer
  return language === 'zh'
    ? `嗯，我明白您的顾虑。${answer}`
    : `Mm, I understand the concern. ${answer}`
}

function reactionFor(
  userText: string,
  answer: string,
  language: VoiceLanguage,
): string {
  const seed = `${userText}|${answer}`
  if (SUCCESS_PATTERN.test(answer)) {
    return pick(language === 'zh' ? ZH_SUCCESS_REACTIONS : EN_SUCCESS_REACTIONS, seed)
  }
  if (ACTION_PATTERN.test(userText)) {
    return pick(language === 'zh' ? ZH_ACTION_REACTIONS : EN_ACTION_REACTIONS, seed)
  }
  return pick(language === 'zh' ? ZH_GENERAL_REACTIONS : EN_GENERAL_REACTIONS, seed)
}

export function ensureHumanLikeReply({
  userText,
  answer,
  language,
}: HumanLikeReplyRequest): string {
  const cleanUser = userText.trim()
  const cleanAnswer = answer.replace(/\s+/g, ' ').trim()

  if ((language === 'zh' ? PRICE_ZH : PRICE_EN).test(cleanUser)) {
    return priceReply(cleanUser, language)
  }
  if (!cleanAnswer) return unknownReply(language)
  if (PRIVACY_PATTERN.test(cleanUser) || PRIVACY_PATTERN.test(cleanAnswer)) {
    return privacyReply(cleanAnswer, language)
  }
  if (FAILURE_PATTERN.test(cleanAnswer)) return failureReply(cleanAnswer, language)
  if (UNAVAILABLE_PATTERN.test(cleanAnswer) || (ACTION_PATTERN.test(cleanUser) && UNKNOWN_PATTERN.test(cleanAnswer))) {
    return unavailableReply(language)
  }
  if (UNKNOWN_PATTERN.test(cleanAnswer)) return unknownReply(language)
  if (alreadyExpressive(cleanAnswer, language)) return cleanAnswer

  const reaction = reactionFor(cleanUser, cleanAnswer, language)
  return `${reaction} ${cleanAnswer}`.trim()
}
