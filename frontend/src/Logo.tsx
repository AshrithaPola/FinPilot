/** FinPilot's mark: a heading needle in a ring, the way a pilot sets a course. */
export default function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="16" cy="16" r="14" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="M16 5 L20 17 L16 15 L12 17 Z" fill="currentColor" />
      <path d="M16 27 L12 15 L16 17 L20 15 Z" fill="currentColor" opacity="0.35" />
    </svg>
  );
}
