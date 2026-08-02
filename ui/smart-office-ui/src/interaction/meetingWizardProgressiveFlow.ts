import './MeetingWizardProgressiveFlow.css'

type MeetingWizardStep = 'date' | 'availability' | 'confirm' | 'success'
type TimelineSlot = {
  slot_id: string
  start_label: string
  end_label: string
  staff_name?: string
  staff_role?: string
  staff_address?: string
  staff_email?: string
  staff_phone?: string
  slot_state?: 'available' | 'unavailable' | 'booked' | 'past'
  available?: boolean
}

const MEETING_ROUTE = '/interaction/meeting'
const PROGRESS_CLASS = 'meeting-wizard-progress'
const BACK_DATE_CLASS = 'meeting-wizard-back-date'
const RESTART_CLASS = 'meeting-wizard-restart'
const nativeFetch = window.fetch.bind(window)

let latestSlots: TimelineSlot[] = []
let selectedSlot: TimelineSlot | null = null

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

  const hint = header.querySelector<HTMLElement>('span')
  if (step === 'availability' && hint) {
    hint.textContent = '09:00–18:00 · 绿色表示员工可预约'
  }
}

function stateLabel(slot: TimelineSlot): string {
  if (slot.available) return '可预约'
  if (slot.slot_state === 'past') return '已过时间'
  if (slot.slot_state === 'booked') return '已预约'
  return '暂无员工可预约'
}

function ensureAddressNode(button: HTMLButtonElement, slot: TimelineSlot): void {
  let address = button.querySelector<HTMLElement>('.meeting-hour-address')
  if (!slot.staff_address) {
    address?.remove()
    return
  }
  if (!address) {
    address = document.createElement('small')
    address.className = 'meeting-hour-address'
    const status = button.querySelector('em')
    button.insertBefore(address, status)
  }
  address.textContent = slot.staff_address
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
    button.dataset.slotState = slot.slot_state ?? (slot.available ? 'available' : 'unavailable')
    button.dataset.slotId = slot.slot_id

    const time = button.querySelector<HTMLElement>('strong')
    const name = button.querySelector<HTMLElement>('span')
    const role = button.querySelector<HTMLElement>('small:not(.meeting-hour-address)')
    const status = button.querySelector<HTMLElement>('em')
    if (time) time.textContent = `${slot.start_label}–${slot.end_label}`
    if (name) {
      name.textContent = slot.available
        ? slot.staff_name || '可预约员工'
        : slot.slot_state === 'booked' && slot.staff_name
          ? `${slot.staff_name}（已预约）`
          : '—'
    }
    if (role) role.textContent = slot.available ? slot.staff_role || '' : ''
    if (status) status.textContent = stateLabel(slot)
    ensureAddressNode(button, slot)

    if (!button.dataset.timelineSelectionBound) {
      button.dataset.timelineSelectionBound = 'true'
      button.addEventListener('click', () => {
        const slotId = button.dataset.slotId
        selectedSlot = latestSlots.find((item) => item.slot_id === slotId) ?? null
      }, { capture: true })
    }
  })
}

function ensureConfirmationAddress(layout: HTMLElement, step: MeetingWizardStep): void {
  if (step !== 'confirm') return
  const back = layout.querySelector<HTMLButtonElement>('.booking-confirmation .secondary')
  if (back) back.textContent = '← 返回时间表'

  const details = layout.querySelector<HTMLDListElement>('.booking-confirmation dl')
  if (!details) return
  let row = details.querySelector<HTMLElement>('.meeting-confirm-address')
  if (!selectedSlot?.staff_address) {
    row?.remove()
    return
  }
  if (!row) {
    row = document.createElement('div')
    row.className = 'meeting-confirm-address'
    row.innerHTML = '<dt>地址</dt><dd></dd>'
    const topic = details.lastElementChild
    if (topic) details.insertBefore(row, topic)
    else details.appendChild(row)
  }
  const value = row.querySelector<HTMLElement>('dd')
  if (value) value.textContent = selectedSlot.staff_address
}

function ensureSuccessRestart(layout: HTMLElement, step: MeetingWizardStep): void {
  const success = layout.querySelector<HTMLElement>('.booking-success')
  if (!success || step !== 'success') return
  if (selectedSlot?.staff_address) {
    let address = success.querySelector<HTMLElement>('.meeting-success-address')
    if (!address) {
      address = document.createElement('p')
      address.className = 'meeting-success-address'
      const bookingId = success.querySelector('small')
      if (bookingId) success.insertBefore(address, bookingId)
      else success.appendChild(address)
    }
    address.textContent = `会议地址：${selectedSlot.staff_address}`
  }
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
  ensureTimelineRows(layout, step)
  ensureConfirmationAddress(layout, step)
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
    attributeFilter: ['class', 'hidden'],
  })
}
