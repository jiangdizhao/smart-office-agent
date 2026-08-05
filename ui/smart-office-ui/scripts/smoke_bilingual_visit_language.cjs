const fs = require('node:fs')
const path = require('node:path')

function read(relative) {
  return fs.readFileSync(path.join(process.cwd(), relative), 'utf8')
}

function requireText(source, text, label) {
  if (!source.includes(text)) throw new Error(`${label} is missing: ${text}`)
}

const manager = read('src/voice/visitLanguagePreference.ts')
const main = read('src/main.tsx')
const host = read('src/virtual-host/VirtualHostApp.tsx')
const rail = read('src/interaction/InteractionActionRail.tsx')
const railCss = read('src/interaction/InteractionActionRail.css')
const panel = read('src/interaction/InteractionPanelHost.tsx')
const phase2b = read('src/sales/salesPhase2BClient.ts')
const introduction = JSON.parse(read('../../config/sales_phase2a.json'))

requireText(manager, "let preferredLanguage: VoiceLanguage = 'en'", 'English-first Visit default')
requireText(manager, "window.addEventListener('smartoffice:visit-activated', reset)", 'Visit activation language reset')
requireText(manager, "window.addEventListener('smartoffice:visit-revoked', reset)", 'Visit revocation language reset')
requireText(manager, "if (preferredLanguage === 'en' && predominantlyChinese(clean))", 'One-way automatic Chinese switch')
requireText(manager, "return this.force('en', 'explicit_english_request')", 'Explicit English override')
requireText(manager, 'SHORT_CHINESE_GREETING', 'Short Chinese greeting allowance')
requireText(manager, 'teams|onenote|outlook|powerpoint|ppt|word|excel|smart|office|sara', 'Product-name exclusion')

const languageInstall = main.indexOf('installVisitLanguagePreference()')
const phase2bInstall = main.indexOf('installSalesPhase2BEngagementOrchestrator()')
if (languageInstall < 0 || phase2bInstall < 0 || languageInstall > phase2bInstall) {
  throw new Error('Visit language preference must be installed before Phase 2B orchestration.')
}

requireText(host, 'I speak English and Chinese', 'Bilingual welcome text')
requireText(host, '我会英语和中文', 'Chinese bilingual welcome text')
requireText(host, 'VISIT_LANGUAGE_CHANGED_EVENT', 'Host language synchronization')
requireText(host, 'Digital Manager · Solution Consultant / 数字管理员 · 解决方案顾问', 'Bilingual host identity')
requireText(host, 'READY · 就绪', 'Bilingual host status')

for (const [english, chinese] of [
  ['REGISTRATION', '登记信息'],
  ['BOOK A MEETING', '预约会议'],
  ['LIVE RECORDING', '实时录音'],
  ['SESSION SUMMARY', '对话总结'],
  ['RESULT CENTER', '结果中心'],
]) {
  requireText(rail, english, `Service rail English label ${english}`)
  requireText(rail, chinese, `Service rail Chinese label ${chinese}`)
}
requireText(rail, 'visitLanguagePreference.current()', 'Interaction panel Visit language')
requireText(railCss, '@media (max-width: 920px)', 'English-only compact fallback breakpoint')
requireText(railCss, '.interaction-action-copy em { display: none; }', 'Compact Chinese secondary hiding')
requireText(panel, 'Registration ·', 'English-first panel title rendering')
requireText(panel, 'titleZh', 'Panel Chinese subtitle')
requireText(phase2b, 'visitLanguagePreference.current()', 'Phase 2B proactive Visit language')
requireText(phase2b, 'visitLanguagePreference.resolve(input.text', 'Phase 2B observation language resolution')

if (!introduction.self_introduction.en.includes('both English and Chinese')) {
  throw new Error('Canonical English introduction must announce both supported languages.')
}
if (!introduction.self_introduction.zh.includes('英语和中文')) {
  throw new Error('Canonical Chinese introduction must announce both supported languages.')
}

console.log('PASS: The exhibition UI is English-first bilingual, new Visits default to English, substantial Chinese speech switches the Visit to Chinese, explicit language requests override it, and Phase 2B follows the same Visit language.')
