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
        viewBox="0 0 520 760"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="hostSuit" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#273b5a" />
            <stop offset="0.55" stopColor="#152642" />
            <stop offset="1" stopColor="#0a172a" />
          </linearGradient>
          <linearGradient id="hostBlouse" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#f7fbff" />
            <stop offset="1" stopColor="#cdd8e8" />
          </linearGradient>
          <linearGradient id="hostHair" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#352923" />
            <stop offset="0.5" stopColor="#211a18" />
            <stop offset="1" stopColor="#0c0b0c" />
          </linearGradient>
          <radialGradient id="hostSkin" cx="48%" cy="34%" r="70%">
            <stop offset="0" stopColor="#f6cfb6" />
            <stop offset="0.72" stopColor="#e5ae91" />
            <stop offset="1" stopColor="#c88770" />
          </radialGradient>
          <filter id="hostShadow" x="-30%" y="-30%" width="160%" height="170%">
            <feDropShadow dx="0" dy="25" stdDeviation="22" floodColor="#020814" floodOpacity="0.48" />
          </filter>
        </defs>

        <ellipse cx="260" cy="718" rx="178" ry="27" fill="#07101f" opacity="0.48" />

        <g className="avatar-body" filter="url(#hostShadow)">
          <path
            d="M95 704c13-123 45-211 101-258 18-15 39-25 64-25s46 10 64 25c56 47 88 135 101 258H95Z"
            fill="url(#hostSuit)"
          />
          <path
            d="m198 447 62 51 62-51-18 257h-88l-18-257Z"
            fill="url(#hostBlouse)"
          />
          <path d="m190 451 70 47-53 72-55-82 38-37Z" fill="#2f476b" />
          <path d="m330 451-70 47 53 72 55-82-38-37Z" fill="#213858" />
          <path
            d="M188 458c22 22 46 33 72 33s50-11 72-33l-17-45H205l-17 45Z"
            fill="url(#hostSkin)"
          />

          <path
            d="M133 262c0-121 53-193 128-193 79 0 132 76 132 196 0 109-57 186-132 186-72 0-128-76-128-189Z"
            fill="url(#hostHair)"
          />
          <path
            d="M174 241c1-87 35-139 89-139 58 0 94 54 94 142 0 99-35 166-95 166-57 0-89-67-88-169Z"
            fill="url(#hostSkin)"
          />
          <path
            d="M163 230c14-98 59-137 111-134 47 2 80 38 91 105-21-33-43-52-68-59-37 31-81 47-134 49v39Z"
            fill="url(#hostHair)"
          />
          <path
            d="M156 246c-14-6-25 8-19 31 5 22 18 36 33 30l-4-58-10-3Zm209 0c14-6 25 8 19 31-5 22-18 36-33 30l4-58 10-3Z"
            fill="#dfa78c"
          />

          <g className="avatar-eyes">
            <path d="M203 250c13-11 29-11 43 0" fill="none" stroke="#5a3830" strokeWidth="6" strokeLinecap="round" />
            <path d="M277 250c14-11 31-11 44 0" fill="none" stroke="#5a3830" strokeWidth="6" strokeLinecap="round" />
            <ellipse cx="224" cy="259" rx="7" ry="8" fill="#251a18" />
            <ellipse cx="299" cy="259" rx="7" ry="8" fill="#251a18" />
            <circle cx="226" cy="256" r="2" fill="#fff" opacity="0.8" />
            <circle cx="301" cy="256" r="2" fill="#fff" opacity="0.8" />
          </g>

          <path d="M259 264c-3 19-5 32-2 40 4 5 10 7 17 5" fill="none" stroke="#bd806b" strokeWidth="4" strokeLinecap="round" />
          <path d="M210 322c31 17 67 17 99 0" fill="none" stroke="#d69086" strokeWidth="5" strokeLinecap="round" opacity="0.55" />
          <path
            className="avatar-mouth avatar-mouth-closed"
            d="M229 337c18 11 43 11 62 0-17 17-45 18-62 0Z"
            fill="#a94f58"
          />
          <ellipse
            className="avatar-mouth avatar-mouth-open"
            cx="260"
            cy="341"
            rx="27"
            ry="13"
            fill="#7e313e"
          />
          <path d="M223 383c24 12 50 12 75 0" fill="none" stroke="#c48670" strokeWidth="4" strokeLinecap="round" opacity="0.55" />
        </g>
      </svg>
      <div className="avatar-stage-glow" aria-hidden="true" />
    </div>
  )
}
