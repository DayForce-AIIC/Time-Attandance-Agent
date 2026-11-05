/** Clock glyph used as the product mark in the header and on the sign-in page. */
export function BrandMark({ className = "brand-mark" }: { className?: string }) {
  return (
    <span className={className} aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" focusable="false">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7.2V12l3.2 2" />
      </svg>
    </span>
  );
}
