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
    style: 'warm_confident',
    pace: 'natural',
    energy: 'medium',
    question_tone: 'none',
    emphasis_terms: [],
    humour_delivery: 'light_smile',
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
  const verifiedOperation = /已经(?:打开|关闭|停止|完成)|通过状态验证|已保存|预约成功|登记成功|is (?:open|closed|complete)|verified|saved successfully/i.test(clean)
  const question = /[？?]\s*$/.test(clean)
  const humour = /咖啡|工位|停车位|冰箱|年假|键盘|文件名都开始|不同版本|重复录入很有耐心|啊|哦|嗯哼|好嘞|搞定|漂亮|coffee|desk|parking|annual leave|keyboard|file names appear to join|ah|oh|mm-hm|there we go|now we're talking/i.test(clean)
  if (opening) {
    return {
      delivery: {
        schema_version: 'voice-delivery-v1',
        style: 'light_playful',
        pace: 'natural_brisk',
        energy: 'medium_high',
        question_tone: 'curious',
        emphasis_terms: language === 'zh'
          ? ['数字管理员', '企业解决方案顾问']
          : ['Digital Manager', 'Enterprise Solution Consultant'],
        humour_delivery: 'light_smile',
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
        humour_delivery: 'none',
      },
      purpose: privacy ? 'privacy_answer' : 'failure_answer',
      replyMode: 'exact_operational',
      expectUserResponse: false,
      questionField: null,
    }
  }
  if (verifiedOperation) {
    return {
      delivery: {
        ...defaultVoiceDelivery(),
        style: 'verified_success',
        energy: 'medium_high',
        humour_delivery: humour ? 'light_smile' : 'none',
      },
      purpose: 'verified_operational_result',
      replyMode: 'exact_operational',
      expectUserResponse: false,
      questionField: null,
    }
  }
  return {
    delivery: {
      ...defaultVoiceDelivery(),
      style: question ? 'curious_discovery' : 'light_playful',
      question_tone: question ? 'curious' : 'none',
      humour_delivery: 'light_smile',
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
      warm_confident: 'Use a warm, confident and welcoming delivery with natural vocal movement.',
      curious_discovery: 'Sound genuinely interested, with a lightly rising and inviting tone rather than a questionnaire voice.',
      confident_recommendation: 'Sound confident and helpful, with clear customer value and natural emphasis.',
      light_playful: 'Use a relaxed, bright tone with a subtle smile and one small change in vocal energy. Do not add words or exaggerate a joke.',
      calm_reassuring: 'Use a warm, calm recovery tone with natural human variation. Be clear about the limitation without sounding flat or defensive.',
      verified_success: 'Sound pleased and lightly celebratory while remaining precise and professional.',
      neutral_exact: 'Use a clear, concise delivery with natural spoken rhythm rather than a flat announcement.',
    }[delivery.style]
    return [
      style,
      delivery.pace === 'natural_brisk' ? 'Keep a natural, slightly brisk pace.' : delivery.pace === 'measured' ? 'Use a measured pace.' : 'Use a natural conversational pace.',
      emphasis ? `Gently emphasize: ${emphasis}.` : '',
      delivery.pause_before_question ? 'Make a short natural pause before the final question.' : '',
      delivery.question_tone !== 'none' ? 'Use a sincere, inviting intonation for the final question.' : '',
      'Do not sound like a newsreader, advertisement or telephone menu. Do not add any words.',
    ].filter(Boolean).join(' ')
  }
  const style = {
    warm_confident: '声音温暖、自信、有欢迎感，并带自然的语气起伏，不要像播报新闻。',
    curious_discovery: '表现出真诚兴趣，询问时语调轻微上扬、有邀请感，不要像填写问卷。',
    confident_recommendation: '表达自信、清楚，突出对客户的实际价值，并自然强调重点。',
    light_playful: '使用放松、明亮、带轻微笑意的语气，并有一次自然的能量变化；不要额外加词，也不要夸张表演笑话。',
    calm_reassuring: '使用温暖、平静的恢复语气，带自然的人类语气变化；明确说明限制，但不要冷冰冰或防御性地说话。',
    verified_success: '声音带一点满意和轻微庆祝感，但必须保持准确、专业。',
    neutral_exact: '表达清楚、中性、简洁，同时保留自然口语节奏，不要机械平读。',
  }[delivery.style]
  return [
    style,
    delivery.pace === 'natural_brisk' ? '整体语速自然并稍微明快。' : delivery.pace === 'measured' ? '使用较为从容的语速。' : '使用自然对话语速。',
    emphasis ? `自然地轻微强调：${emphasis}。` : '',
    delivery.pause_before_question ? '在最后的问题前做一次短而自然的停顿。' : '',
    delivery.question_tone !== 'none' ? '最后一句使用真诚、邀请式的询问语调。' : '',
    '不要像新闻播音、广告或电话菜单。不得添加、删除、总结或改写任何文字。',
  ].filter(Boolean).join('')
}
