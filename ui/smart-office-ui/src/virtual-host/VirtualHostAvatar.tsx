export type VirtualHostVisualState =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'executing'
  | 'waiting-approval'
  | 'error'

type VirtualHostAvatarProps = {
  state: VirtualHostVisualState
}

export default function VirtualHostAvatar({ state }: VirtualHostAvatarProps) {
  return (
    <div
      className={`virtual-host-avatar avatar-${state}`}
      role="img"
      aria-label="成熟职业女性虚拟办公助手"
    >
      <div className="avatar-portrait-backdrop" aria-hidden="true" />
      <div className="avatar-orbit avatar-orbit-outer" />
      <div className="avatar-orbit avatar-orbit-inner" />
      <div className="avatar-audio-bars" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
        <span />
      </div>

      <svg
        className="avatar-portrait"
        viewBox="0 0 620 840"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="professionalSuit" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#24364d" />
            <stop offset="0.5" stopColor="#14263b" />
            <stop offset="1" stopColor="#0b1727" />
          </linearGradient>
          <linearGradient id="professionalBlouse" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#f6f0e9" />
            <stop offset="1" stopColor="#d7d0c9" />
          </linearGradient>
          <linearGradient id="professionalHair" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#755b48" />
            <stop offset="0.44" stopColor="#4b352b" />
            <stop offset="1" stopColor="#211713" />
          </linearGradient>
          <radialGradient id="professionalSkin" cx="46%" cy="31%" r="74%">
            <stop offset="0" stopColor="#f8d9c1" />
            <stop offset="0.72" stopColor="#e8b798" />
            <stop offset="1" stopColor="#ca8f72" />
          </radialGradient>
          <linearGradient id="professionalLip" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#b95f6d" />
            <stop offset="1" stopColor="#8f3f4f" />
          </linearGradient>
          <filter id="professionalShadow" x="-30%" y="-30%" width="160%" height="175%">
            <feDropShadow
              dx="0"
              dy="24"
              stdDeviation="20"
              floodColor="#02070e"
              floodOpacity="0.4"
            />
          </filter>
          <filter id="portraitRim" x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow
              dx="0"
              dy="0"
              stdDeviation="7"
              floodColor="#e9f2f4"
              floodOpacity="0.28"
            />
          </filter>
        </defs>

        <ellipse cx="310" cy="796" rx="200" ry="27" fill="#08101a" opacity="0.32" />

        <g className="avatar-body" filter="url(#professionalShadow)">
          <path
            d="M109 784c13-142 49-239 111-291 27-22 58-34 90-34 33 0 64 12 91 34 62 52 98 149 111 291H109Z"
            fill="url(#professionalSuit)"
            stroke="#567089"
            strokeOpacity="0.22"
            strokeWidth="2"
          />

          <path
            d="M226 488c24 29 52 45 84 45s60-16 84-45l-27-49H253l-27 49Z"
            fill="url(#professionalSkin)"
          />
          <path
            d="m244 489 66 51 66-51-27 295h-78l-27-295Z"
            fill="url(#professionalBlouse)"
          />
          <path d="m226 491 84 49-68 91-66-101 50-39Z" fill="#2d435b" />
          <path d="m394 491-84 49 68 91 66-101-50-39Z" fill="#21364e" />
          <path
            d="M183 559c31 6 72 33 116 83l-34 47c-55-36-101-60-139-70 11-26 30-46 57-60Z"
            fill="#172b42"
          />
          <path
            d="M437 559c-31 6-72 33-116 83l34 47c55-36 101-60 139-70-11-26-30-46-57-60Z"
            fill="#12253a"
          />
          <path
            d="M196 618c40 16 83 37 126 64-16 22-35 41-56 57-45-25-87-45-126-58 11-26 30-47 56-63Z"
            fill="#1b3047"
          />
          <path
            d="M424 618c-40 16-83 37-126 64 16 22 35 41 56 57 45-25 87-45 126-58-11-26-30-47-56-63Z"
            fill="#14283d"
          />
          <path
            d="M248 668c21 8 41 18 60 30-9 19-23 34-42 44-22-10-43-23-61-38 10-20 24-32 43-36Z"
            fill="url(#professionalSkin)"
          />
          <path
            d="M372 668c-21 8-41 18-60 30 9 19 23 34 42 44 22-10 43-23 61-38-10-20-24-32-43-36Z"
            fill="url(#professionalSkin)"
          />

          <g className="avatar-head" filter="url(#portraitRim)">
            <path
              d="M156 258C156 116 218 44 310 44s154 73 154 215c0 89-18 164-54 215-22 31-52 49-91 52-44 4-80-14-105-49-39-54-58-128-58-219Z"
              fill="url(#professionalHair)"
            />
            <path
              d="M199 229c0-91 42-148 111-148 71 0 113 58 113 151 0 109-42 184-113 184-69 0-111-75-111-187Z"
              fill="url(#professionalSkin)"
            />
            <path
              d="M185 221c13-108 64-162 132-157 64 4 106 56 115 145-25-48-57-81-95-97-43 39-93 62-152 68v41Z"
              fill="url(#professionalHair)"
            />
            <path
              d="M171 248c-16-7-29 10-22 36 6 25 21 41 38 34l-5-66-11-4Zm278 0c16-7 29 10 22 36-6 25-21 41-38 34l5-66 11-4Z"
              fill="#dfa98b"
            />

            <path
              d="M202 189c-15 54-19 113-12 176 7 61 27 111 59 151-41-11-72-37-92-77-19-39-28-89-26-151 1-51 13-88 36-112l35 13Z"
              fill="url(#professionalHair)"
              opacity="0.94"
            />
            <path
              d="M418 189c15 54 19 113 12 176-7 61-27 111-59 151 41-11 72-37 92-77 19-39 28-89 26-151-1-51-13-88-36-112l-35 13Z"
              fill="url(#professionalHair)"
              opacity="0.94"
            />

            <path d="M238 245c17-12 37-13 56-2" fill="none" stroke="#6b443a" strokeWidth="7" strokeLinecap="round" />
            <path d="M328 243c19-11 39-10 55 3" fill="none" stroke="#6b443a" strokeWidth="7" strokeLinecap="round" />

            <g className="avatar-eyes">
              <g className="avatar-eye avatar-eye-left">
                <path d="M238 271c16-14 38-14 55 0-18 11-38 11-55 0Z" fill="#f9f7f3" />
                <ellipse cx="266" cy="271" rx="10" ry="11" fill="#607f86" />
                <ellipse cx="266" cy="272" rx="5" ry="7" fill="#202427" />
                <circle cx="269" cy="267" r="2.5" fill="#fff" opacity="0.88" />
              </g>
              <g className="avatar-eye avatar-eye-right">
                <path d="M328 271c17-14 39-14 55 0-17 11-37 11-55 0Z" fill="#f9f7f3" />
                <ellipse cx="356" cy="271" rx="10" ry="11" fill="#607f86" />
                <ellipse cx="356" cy="272" rx="5" ry="7" fill="#202427" />
                <circle cx="359" cy="267" r="2.5" fill="#fff" opacity="0.88" />
              </g>
            </g>

            <path d="M311 278c-4 23-7 40-3 49 5 6 13 8 21 5" fill="none" stroke="#bd806a" strokeWidth="4" strokeLinecap="round" />
            <path d="M263 346c28 17 65 17 94 0" fill="none" stroke="#d18e84" strokeWidth="4" strokeLinecap="round" opacity="0.45" />
            <path
              className="avatar-mouth avatar-mouth-closed"
              d="M276 356c20 12 48 12 68 0-18 22-50 22-68 0Z"
              fill="url(#professionalLip)"
            />
            <ellipse
              className="avatar-mouth avatar-mouth-open"
              cx="310"
              cy="363"
              rx="28"
              ry="14"
              fill="#7d3140"
            />
            <path d="M267 411c27 12 57 12 85 0" fill="none" stroke="#c78c73" strokeWidth="4" strokeLinecap="round" opacity="0.42" />
            <circle cx="189" cy="318" r="7" fill="#d5b48f" />
            <circle cx="431" cy="318" r="7" fill="#d5b48f" />
          </g>
        </g>
      </svg>
      <div className="avatar-stage-glow" aria-hidden="true" />
    </div>
  )
}
