# ShiftGuard Enterprise Design System

A modern, high-contrast, evidence-first design system for ShiftGuard HR and timecard review workflows. Built strictly with semantic CSS custom properties and accessible primitives.

## 1. Design Tokens

### Color Scales

#### Brand (Blue)
- `--brand-50`: `#F1F7FF` (Soft blue surfaces)
- `--brand-100`: `#DCEBFF` (Selected and highlighted backgrounds)
- `--brand-200`: `#BFD8FA` (Subtle active borders)
- `--brand-600`: `#1879D1` (Charts and interactive states)
- `--brand-700`: `#0D63B5` (Brand links and focus accents)
- `--brand-800`: `#094F92` (Hover states)
- `--brand-900`: `#07396D` (High-contrast brand accents)

#### Navy, Blue & Green Application Accents
- Sidebar: `#07072F` with white text
- Active navigation: `#218AF1`
- Form controls and alternating table rows: `#DCEBFF`
- Primary actions: `#008000`, with `#006B00` hover

#### Cool Blue Neutrals
- `--bg-page`: `#F5F8FD` (Page canvas background)
- `--bg-surface`: `#FFFFFF` (Card and panel backgrounds)
- `--bg-subtle`: `#EFF5FD` (Secondary surfaces)
- `--bg-muted`: `#E4EEFC` (Track fills & muted badges)
- `--border-light`: `#E2EAF5` (Subtle dividers & inner card borders)
- `--border-default`: `#CBD8E9` (Standard card & control borders)
- `--border-strong`: `#9EB2CF` (Active inputs & table dividers)
- `--text-primary`: `#111B3A` (Headings & strong metrics)
- `--text-secondary`: `#3D4D6B` (Body copy & labels)
- `--text-muted`: `#697894` (Secondary captions & timestamp metadata)

#### Semantic States
- **Critical / High Severity**: Text `#B42318`, Background `#FEF3F2`, Border `#FECDCA`, Icon `▲`
- **Medium / Warning**: Text `#B54708`, Background `#FFFAEB`, Border `#FEDF89`, Icon `●`
- **Low / Info**: Text `#175CD3`, Background `#EFF8FF`, Border `#B2DDFF`, Icon `○`
- **Success / Resolved**: Text `#067647`, Background `#ECFDF3`, Border `#A6F4C5`, Icon `✓`
- **Neutral / Open**: Text `#344054`, Background `#F2F4F7`, Border `#E4E7EC`

### Typography
- **Primary Font**: `Inter`, `-apple-system`, `BlinkMacSystemFont`, `sans-serif`
- **Monospace Font**: `JetBrains Mono`, `SFMono-Regular`, `Consolas`, monospace (used for rule IDs, punch IDs, employee references, and timestamps)
- **Tabular Figures**: `font-variant-numeric: tabular-nums` applied across all numerical counts, hours, percentages, and metrics.
- **Hierarchy Scale**:
  - `24px / 1.2` (Page headings)
  - `18px / 1.3` (Card titles)
  - `14px / 1.5` (Body default)
  - `13px / 1.4` (Table content & secondary text)
  - `11-12px / 1.4` (Captions, timestamps & uppercase eyebrow badges)

### Shape, Elevation & Focus
- **Radii**: 6px (`--radius-sm`), 8px (`--radius-md`), 12px (`--radius-lg`), 9999px (`--radius-full`)
- **Elevation**:
  - `var(--shadow-xs)`: `0 1px 2px rgba(16, 24, 40, 0.04)`
  - `var(--shadow-sm)`: `0 1px 3px rgba(16, 24, 40, 0.06), 0 1px 2px rgba(16, 24, 40, 0.03)`
  - `var(--shadow-md)`: `0 4px 12px -2px rgba(16, 24, 40, 0.08)`
  - `var(--shadow-lg)`: `0 12px 24px -4px rgba(16, 24, 40, 0.12)`
- **Focus Rings**: `0 0 0 2px #FFFFFF, 0 0 0 4px #218AF1` (meets WCAG 2.1 non-color indicator and 3:1 boundary contrast standards).

---

## 2. Component Inventory

1. **AppShell & Navigation**:
   - `AppSidebar`: Slim, collapsible 148px desktop sidebar with a navy background, white navigation text, and blue active state.
   - `AppHeader`: Breadcrumb trails, LLM health pill with status dot, manager switcher, and demo tools trigger.
   - `DemoToolsPopover`: Compact popover containing fault simulation dropdown, re-run detection, and confirmation modal for reset demo data.
2. **Dashboard & KPIs**:
   - `StatCard`: Responsive KPI card with tabular numbers, muted state for zero values, and semantic accents for attention states.
   - `SeverityBreakdown`: Segmented distribution bar with percentages and color-coded dots.
   - `FilterBar`: Aligned row with Status, Severity, Rule selects, employee search input, active-filter chips, and "Clear all".
3. **Data Display**:
   - `ExceptionTable`: High-density enterprise table with employee initials avatar, mono IDs, sortable headers, keyboard row focus, and clear review actions.
   - `TimecardTimeline`: Auto-fitting SVG timeline with 3 aligned lanes (`Schedule`, `Worked`, `Breaks`), minimum 22px bar heights, direct anomaly callout, safe window band, and missing punch indicators.
   - `WeeklyHours`: Progress meter with brand-blue fill, threshold tick at 40h, and semantic threshold transitions.
   - `PolicyPanel`: Collapsible cards with rule ID badges and section references.
   - `AuditDrawer`: Right-side slide-over drawer with cryptographic hash chain verification.
4. **Interactive Controls & Feedback**:
   - `SeverityBadge` & `StatusBadge`: Contrast-compliant chips pairing colors with non-color symbols (`▲`, `●`, `✓`, `✕`).
   - `ErrorBanner`: Accessible alert with actionable retry trigger.
   - `LoadingState` & `EmptyState`: Clean state illustrations with clear guidance.
