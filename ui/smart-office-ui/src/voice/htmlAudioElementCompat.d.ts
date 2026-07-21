export {}

declare global {
  interface HTMLAudioElement {
    /** Compatibility property used by the Realtime remote-audio element. */
    playsInline: boolean
  }
}
