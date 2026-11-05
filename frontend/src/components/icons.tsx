import type { ReactNode } from "react";

/** Small inline icon set (Feather-style outlines) so the UI has no icon dependency. */
function Svg({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

type IconProps = { className?: string };

export const IconSend = ({ className }: IconProps) => (
  <Svg className={className}><line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" /></Svg>
);
export const IconPlus = ({ className }: IconProps) => (
  <Svg className={className}><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></Svg>
);
export const IconX = ({ className }: IconProps) => (
  <Svg className={className}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></Svg>
);
export const IconTrash = ({ className }: IconProps) => (
  <Svg className={className}><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6M14 11v6" /><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /></Svg>
);
export const IconSearch = ({ className }: IconProps) => (
  <Svg className={className}><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></Svg>
);
export const IconFileText = ({ className }: IconProps) => (
  <Svg className={className}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /></Svg>
);
export const IconClipboard = ({ className }: IconProps) => (
  <Svg className={className}><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><rect x="8" y="2" width="8" height="4" rx="1" /></Svg>
);
export const IconInfo = ({ className }: IconProps) => (
  <Svg className={className}><circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" /></Svg>
);
export const IconEdit = ({ className }: IconProps) => (
  <Svg className={className}><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z" /></Svg>
);
export const IconArchive = ({ className }: IconProps) => (
  <Svg className={className}><polyline points="21 8 21 21 3 21 3 8" /><rect x="1" y="3" width="22" height="5" /><line x1="10" y1="12" x2="14" y2="12" /></Svg>
);
export const IconShield = ({ className }: IconProps) => (
  <Svg className={className}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></Svg>
);
export const IconBot = ({ className }: IconProps) => (
  <Svg className={className}>
    <rect x="4" y="8" width="16" height="12" rx="3.5" />
    <path d="M12 8V5.2" />
    <circle cx="12" cy="3.8" r="1.3" />
    <path d="M2 12.5v3.5M22 12.5v3.5" />
    <circle cx="9" cy="13.4" r=".7" fill="currentColor" />
    <circle cx="15" cy="13.4" r=".7" fill="currentColor" />
    <path d="M9.5 16.6h5" />
  </Svg>
);
export const IconRestore = ({ className }: IconProps) => (
  <Svg className={className}><polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" /></Svg>
);
