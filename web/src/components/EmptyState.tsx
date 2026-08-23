function ClipboardArt() {
  return (
    <svg viewBox="0 0 120 120" width="96" height="96" fill="none" aria-hidden className="empty-art">
      <g stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <rect x="28" y="24" width="64" height="76" rx="7" />
        <rect x="35" y="31" width="50" height="62" rx="3" opacity="0.55" />
        <path d="M47 24v-4a3 3 0 0 1 3-3h6a4 4 0 0 1 8 0h6a3 3 0 0 1 3 3v4z" />
      </g>
      <rect
        x="45"
        y="48"
        width="30"
        height="24"
        rx="2"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray="1 7"
        opacity="0.7"
      />
    </svg>
  )
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="empty-state">
      <ClipboardArt />
      <p className="empty-state-title">{title}</p>
      {hint ? <p className="empty-state-hint">{hint}</p> : null}
      {action}
    </div>
  )
}
