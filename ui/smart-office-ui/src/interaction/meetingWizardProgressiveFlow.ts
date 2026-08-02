import './MeetingWizardProgressiveFlow.css'

type MeetingWizardStep = 'date' | 'availability' | 'confirm' | 'success'

const MEETING_ROUTE = '/interaction/meeting'
const PROGRESS_CLASS = 'meeting-wizard-progress'
const BACK_DATE_CLASS = 'meeting-wizard-back-date'
const RESTART_CLASS = 'meeting-wizard-restart'

function isMeetingRoute(): boolean {
  const path = window.location.pathname.replace(/\/+$/, '')
  return path === MEETING_ROUTE || path.startsWith(`${MEETING_ROUTE}/`)
}

function resolveStep(layout: HTMLElement): MeetingWizardStep {
  if (layout.querySelector('.booking-success')) return 'success'
  if (layout.querySelector('.booking-confirmation')) return 'confirm'
  if (
    layout.querySelector('.availability-list button')
    || layout.querySelector('.availability-card > .experience-empty')
    || layout.querySelector('.availability-card > .experience-error')
  ) return 'availability'
  return 'date'
}

function stepIndex(step: MeetingWizardStep): number {
  if (step === 'date') return 0
  if (step === 'availability') return 1
  return 2
}

function ensureProgress(layout: HTMLElement, step: MeetingWizardStep): void {
  const parent = layout.parentElement
  if (!parent) return
  let progress = parent.querySelector<HTMLElement>(`:scope > .${PROGRESS_CLASS}`)
  if (!progress) {
    progress = document.createElement('nav')
    progress.className = PROGRESS_CLASS
    progress.setAttribute('aria-label', '预约步骤')
    progress.innerHTML = [
      ['1', '选择日期'],
      ['2', '选择人员与时间'],
      ['3', '确认预约'],
    ].map(([number, label]) => (
      `<div><b>${number}</b><span>${label}</span></div>`
    )).join('')
    parent.insertBefore(progress, layout)
  }

  const currentIndex = stepIndex(step)
  Array.from(progress.children).forEach((child, index) => {
    const item = child as HTMLElement
    item.classList.toggle('active', index === currentIndex)
    item.classList.toggle('complete', index < currentIndex || step === 'success')
    if (index === currentIndex) item.setAttribute('aria-current', 'step')
    else item.removeAttribute('aria-current')
  })
}

function restartAtDateSelection(): void {
  window.location.reload()
}

function ensureAvailabilityBackButton(layout: HTMLElement, step: MeetingWizardStep): void {
  const header = layout.querySelector<HTMLElement>('.availability-card > header')
  if (!header) return
  let button = header.querySelector<HTMLButtonElement>(`.${BACK_DATE_CLASS}`)
  if (!button) {
    button = document.createElement('button')
    button.type = 'button'
    button.className = BACK_DATE_CLASS
    button.textContent = '← 返回选择日期'
    button.addEventListener('click', restartAtDateSelection)
    header.insertBefore(button, header.firstChild)
  }
  button.hidden = step !== 'availability'

  const title = header.querySelector<HTMLElement>('strong')
  if (step === 'availability' && title && !title.dataset.meetingWizardTitle) {
    title.dataset.meetingWizardTitle = title.textContent ?? ''
  }
}

function ensureConfirmationLabels(layout: HTMLElement, step: MeetingWizardStep): void {
  if (step !== 'confirm') return
  const back = layout.querySelector<HTMLButtonElement>('.booking-confirmation .secondary')
  if (back && back.textContent !== '← 返回选择人员') {
    back.textContent = '← 返回选择人员'
  }
}

function ensureSuccessRestart(layout: HTMLElement, step: MeetingWizardStep): void {
  const success = layout.querySelector<HTMLElement>('.booking-success')
  if (!success || step !== 'success') return
  let button = success.querySelector<HTMLButtonElement>(`.${RESTART_CLASS}`)
  if (!button) {
    button = document.createElement('button')
    button.type = 'button'
    button.className = RESTART_CLASS
    button.textContent = '重新预约'
    button.addEventListener('click', restartAtDateSelection)
    success.appendChild(button)
  }
}

let previousStep: MeetingWizardStep | null = null

function synchroniseMeetingWizard(): void {
  if (!isMeetingRoute()) return
  const layout = document.querySelector<HTMLElement>('.meeting-layout')
  if (!layout) return

  const step = resolveStep(layout)
  if (layout.dataset.meetingWizardStep !== step) {
    layout.dataset.meetingWizardStep = step
  }
  ensureProgress(layout, step)
  ensureAvailabilityBackButton(layout, step)
  ensureConfirmationLabels(layout, step)
  ensureSuccessRestart(layout, step)
  document.documentElement.dataset.meetingWizardEnhanced = 'true'

  if (previousStep !== step) {
    previousStep = step
    window.dispatchEvent(new CustomEvent('smartoffice:meeting-wizard-step', {
      detail: { step },
    }))
  }
}

let scheduled = false
function scheduleSynchronise(): void {
  if (scheduled) return
  scheduled = true
  window.requestAnimationFrame(() => {
    scheduled = false
    synchroniseMeetingWizard()
  })
}

if (isMeetingRoute()) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scheduleSynchronise, { once: true })
  } else {
    scheduleSynchronise()
  }
  const observer = new MutationObserver(scheduleSynchronise)
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'hidden'],
  })
}
