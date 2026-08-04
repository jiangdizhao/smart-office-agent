import type { VoiceLanguage } from '../voice/realtimeAgentRuntime'

export type VoiceDeliveryStyle =
  | 'warm_confident'
  | 'curious_discovery'
  | 'confident_recommendation'
  | 'light_playful'
  | 'calm_reassuring'
  | 'verified_success'
  | 'neutral_exact'

export type VoiceDeliveryPlan = {
  schema_version?: 'voice-delivery-v1'
  style: VoiceDeliveryStyle
  pace: 'measured' | 'natural' | 'natural_brisk'
  energy: 'low' | 'medium' | 'medium_high'
  question_tone: 'none' | 'curious' | 'inviting'
  emphasis_terms: string[]
  humour_delivery: 'none' | 'light_smile'
  pause_before_question: boolean
}

export type VoiceOutputContext = {
  delivery: VoiceDeliveryPlan
  purpose: string
  replyMode: string
  expectUserResponse: boolean
  questionField: string | null
}

const contexts = new Map<string, VoiceOutputContext[]>()

function key(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

export function defaultVoiceDelivery(): VoiceDeliveryPlan {
  return {
    schema_version: 'voice-delivery-v1',
    style: 'neutral_exact',
    pace: 'natural',
    energy: 'medium',
    question_tone: 'none',
    emphasis_terms: [],
    humour_delivery: 'none',
    pause_before_question: false,
  }
}

export function inferVoiceOutputContext(
  text: string,
  language: VoiceLanguage,
): VoiceOutputContext {
  const clean = key(text)
  const opening = /数字管理员与企业解决方案顾问|Digital Manager and Enterprise Solution Consultant/i.test(clean)
  const privacy = /隐私|人脸|录音|保存.*数据|privacy|face data|recording/i.test(clean)
  const failure = /没有成功|失败|无法|未完成|did not open|failed|could not/i.test(clean)
  const question = /[？?]\s*$/.test(clean)
  const humour = /咖啡|工位|停车位|冰箱|年假|键盘|coffee|desk|parking|annual leave|keyboard/i.test(clean)
  if (opening) {
    return {
      delivery: {
        schema_version: 'voice-delivery-v1',
        style: humour ? 'light_playful' : 'warm_confident',
        pace: 'natural_brisk',
        energy: 'medium_high',
        question_tone: 'curious',
        emphasis_terms: language === 'zh'
          ? ['数字管理员', '企业解决方案顾问']
          : ['Digital Manager', 'Enterprise Solution Consultant'],
        humour_delivery: humour ? 'light_smile' : 'none',
        pause_before_question: true,
      },
      purpose: 'sales_opening',
      replyMode: 'opening',
      expectUserResponse: true,
      questionField: /行业|industry/i.test(clean) ? 'industry' : 'interested_capabilities',
    }
  }
  if (privacy || failure) {
    return {
      delivery: {
        ...defaultVoiceDelivery(),
        style: 'calm_reassuring',
        pace: 'measured',
        energy: 'low',
      },
      purpose: privacy ? 'privacy_answer' : 'failure_answer',
      replyMode: 'exact_operational',
      expectUserResponse: false,
      questionField: null,
    }
  }
  return {
    delivery: {
      ...defaultVoiceDelivery(),
      style: question ? 'curious_discovery' : 'neutral_exact',
      question_tone: question ? 'curious' : 'none',
      pause_before_question: question,
    },
    purpose: 'general_voice_output',
    replyMode: 'general',
    expectUserResponse: false,
    questionField: null,
  }
}

export function registerVoiceOutputContext(
  text: string,
  context: VoiceOutputContext,
): void {
  const clean = key(text)
  if (!clean) return
  const values = contexts.get(clean) ?? []
  values.push(context)
  contexts.set(clean, values.slice(-4))
}

export function consumeVoiceOutputContext(
  text: string,
  language: VoiceLanguage,
): VoiceOutputContext {
  const clean = key(text)
  const values = contexts.get(clean)
  if (values?.length) {
    const context = values.shift()
    if (!values.length) contexts.delete(clean)
    if (context) return context
  }
  return inferVoiceOutputContext(clean, language)
}

export function deliveryInstruction(
  delivery: VoiceDeliveryPlan,
  language: VoiceLanguage,
): string {
  const emphasis = delivery.emphasis_terms.filter(Boolean).join('、')
  if (language === 'en') {
    const style = {
      warm_confident: 'Use a warm, confident and welcoming delivery.',
      curious_discovery: 'Sound genuinely curious and conversational, not like a questionnaire.',
      confident_recommendation: 'Sound confident and helpful, with clear customer value.',
      light_playful: 'Use a light smile in the voice for the approved humorous line, without laughing.',
      calm_reassuring: 'Use a calm, slower and reassuring delivery without humour.',
      verified_success: 'Sound slightly brighter while remaining precise and professional.',
      neutral_exact: 'Use a clear, neutral and concise delivery.',
    }[delivery.style]
    return [
      style,
      delivery.pace === 'natural_brisk' ? 'Keep a natural, slightly brisk pace.' : delivery.pace === 'measured' ? 'Use a measured pace.' : 'Use a natural conversational pace.',
      emphasis ? `Gently emphasize: ${emphasis}.` : '',
      delivery.pause_before_question ? 'Make a short natural pause before the final question.' : '',
      delivery.question_tone !== 'none' ? 'Use a sincere, inviting intonation for the final question.' : '',
      'Do not sound like a newsreader and do not add any words.',
    ].filter(Boolean).join(' ')
  }
  const style = {
    warm_confident: '声音温暖、自信、有欢迎感，不要像播报新闻。',
    curious_discovery: '使用真诚、自然的好奇语气，不要像填写问卷。',
    confident_recommendation: '表达自信、清楚，突出对客户的实际价值。',
    light_playful: '审核过的幽默句带轻微笑意，但不要发出笑声，也不要夸张。',
    calm_reassuring: '语速稍慢，平静、明确、让人安心，不使用幽默语气。',
    verified_success: '声音略微明亮，但必须保持准确和专业。',
    neutral_exact: '使用清楚、中性、简洁的表达。',
  }[delivery.style]
  return [
    style,
    delivery.pace === 'natural_brisk' ? '整体语速自然并稍微明快。' : delivery.pace === 'measured' ? '使用较为从容的语速。' : '使用自然对话语速。',
    emphasis ? `自然地轻微强调：${emphasis}。` : '',
    delivery.pause_before_question ? '在最后的问题前做一次短而自然的停顿。' : '',
    delivery.question_tone !== 'none' ? '最后一句使用真诚、邀请式的询问语调。' : '',
    '不得添加、删除、总结或改写任何文字。',
  ].filter(Boolean).join('')
}
