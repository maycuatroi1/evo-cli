export function BrandMark({ size = 22, title }: { size?: number; title?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      className="brand-mark"
    >
      <defs>
        <linearGradient id="harness-mark" x1="6" y1="4" x2="26" y2="28" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--active)" />
          <stop offset="1" stopColor="var(--ok)" />
        </linearGradient>
      </defs>
      <g stroke="url(#harness-mark)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 17.5V8h6.5" />
        <circle cx="8" cy="23" r="4.2" />
        <circle cx="24" cy="8" r="4.2" />
        <circle cx="24" cy="23" r="4.2" />
      </g>
      <g fill="url(#harness-mark)">
        <path d="M14.5 5.7 18.6 8l-4.1 2.3z" />
        <path d="M21.7 13.8 24 17.4l2.3-3.6z" />
        <path d="M13.8 20.7 17.9 23l-4.1 2.3z" />
      </g>
    </svg>
  )
}
