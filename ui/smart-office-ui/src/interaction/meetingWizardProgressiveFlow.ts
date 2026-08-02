import './MeetingWizardProgressiveFlow.css'

type MeetingWizardStep = 'date' | 'availability' | 'confirm' | 'success'
type TimelineSlot = {
  slot_id: string
  start_label: string
  end_label: string
  start_at?: string
  end_at?: string
  contact_address?: string
  staff_address?: string
  slot_state?: 'available' | 'booked' | 'past'
  available?: boolean
}

const MEETING_ROUTE = '/interaction/meeting'
const PROGRESS_CLASS = 'meeting-wizard-progress'
const BACK_DATE_CLASS = 'meeting-wizard-back-date'
const RESTART_CLASS = 'meeting-wizard-restart'
const nativeFetch = window.fetch.bind(window)

let latestSlots: TimelineSlot[] = []
let selectedSlot: TimelineSlot | null = null
let autoConfirmPending = false
let autoConfirmStarted = false

function isMeetingRoute(): boolean {
  const path = window.location.pathname.replace(/\/+$/, '')
  return path === MEETING_ROUTE || path.startsWith(`${MEETING_ROUTE}/`)
}

function requestUrl(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input)
}

function installMeetingResponseCapture(): void {
  if (!isMeetingRoute()) return
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await nativeFetch(input, init)
    const url = requestUrl(input)
    if (url.includes('/api/visitor-experience/meeting-availability') && response.ok) {
      try {
        const payload = await response.clone().json() as { slots?: TimelineSlot[] }
        latestSlots = Array.isArray(payload.slots) ? payload.slots : []
        selectedSlot = null
        autoConfirmPending = false
        autoConfirmStarted = false
        scheduleSynchronise()
      } catch {
        latestSlots = []
      }
    }
    return response
  }
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
      ['2', '选择时间'],
      ['3', '预约成功'],
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

function ensureAvailabilityHeader(layout: HTMLElement, step: MeetingWizardStep): void {
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

  const hint = header.querySelector<HTMLElement>('span')
  if (step === 'availability' && hint) {
    hint.textContent = '请选择会面时间'
  }
}

function stateLabel(slot: TimelineSlot): string {
  if (slot.slot_state === 'past') return '已过时间'
  if (slot.slot_state === 'booked') return '已预约'
  return '选择'
}

function ensureTimelineRows(layout: HTMLElement, step: MeetingWizardStep): void {
  if (step !== 'availability') return
  const buttons = Array.from(
    layout.querySelectorAll<HTMLButtonElement>('.availability-list > button'),
  )

  buttons.forEach((button, index) => {
    const slot = latestSlots[index]
    if (!slot) return
    button.classList.add('meeting-hour-row')
    button.dataset.slotState = slot.slot_state ?? (slot.available ? 'available' : 'booked')
    button.dataset.slotId = slot.slot_id

    const time = button.querySelector<HTMLElement>('strong')
    const name = button.querySelector<HTMLElement>('span')
    const role = button.querySelector<HTMLElement>('small')
    const status = button.querySelector<HTMLElement>('em')
    if (time) time.textContent = `${slot.start_label}–${slot.end_label}`
    if (name) name.textContent = ''
    if (role) role.textContent = ''
    if (status) status.textContent = stateLabel(slot)

    Array.from(button.querySelectorAll('.meeting-hour-address')).forEach((node) => node.remove())

    if (!button.dataset.timelineSelectionBound) {
      button.dataset.timelineSelectionBound = 'true'
      button.addEventListener('click', () => {
        const slotId = button.dataset.slotId
        const current = latestSlots.find((item) => item.slot_id === slotId) ?? null
        if (!current?.available) return
        selectedSlot = current
        autoConfirmPending = true
        autoConfirmStarted = false
      }, { capture: true })
    }
  })
}

function autoSubmitConfirmation(layout: HTMLElement, step: MeetingWizardStep): void {
  if (step !== 'confirm' || !autoConfirmPending || autoConfirmStarted) return
  const button = layout.querySelector<HTMLButtonElement>(
    '.booking-confirmation button.primary, .booking-confirmation .primary',
  )
  if (!button || button.disabled) return
  autoConfirmStarted = true
  window.requestAnimationFrame(() => button.click())
}

function ensureSuccessContent(layout: HTMLElement, step: MeetingWizardStep): void {
  const success = layout.querySelector<HTMLElement>('.booking-success')
  if (!success || step !== 'success') return

  const heading = success.querySelector<HTMLElement>('strong')
  if (heading) heading.textContent = '已经预约成功'

  let message = success.querySelector<HTMLElement>('.meeting-success-message')
  if (!message) {
    message = document.createElement('p')
    message.className = 'meeting-success-message'
    success.insertBefore(message, success.querySelector('small'))
  }
  message.textContent = '我们会安排公司员工与您联系。'

  const addressValue = selectedSlot?.contact_address || selectedSlot?.staff_address || ''
  let details = success.querySelector<HTMLElement>('.meeting-success-details')
  if (!details) {
    details = document.createElement('div')
    details.className = 'meeting-success-details'
    success.insertBefore(details, success.querySelector('small'))
  }
  details.innerHTML = ''

  const timeRow = document.createElement('p')
  timeRow.textContent = selectedSlot
    ? `会面时间：${selectedSlot.start_label}–${selectedSlot.end_label}`
    : '会面时间：已确认'
  details.appendChild(timeRow)

  const addressRow = document.createElement('p')
  addressRow.textContent = `联系地址：${addressValue || '公司员工联系您时确认'}`
  details.appendChild(addressRow)

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
  layout.dataset.meetingWizardStep = step
  ensureProgress(layout, step)
  ensureAvailabilityHeader(layout, step)
  ensureTimelineRows(layout, step)
  autoSubmitConfirmation(layout, step)
  ensureSuccessContent(layout, step)
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
  installMeetingResponseCapture()
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
    attributeFilter: ['class', 'hidden', 'disabled'],
  })
}
