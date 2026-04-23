# Daily-Stock Design System

> **Version**: 1.0 · **Date**: 2026-04-20 · **Maintainer**: [@Cthloveross](https://github.com/Cthloveross)
>
> **Status**: This document is the source of truth for all visual and interaction decisions in `apps/dsa-web`. AI coding tools (Cursor, Claude Code) should treat every token, component signature, and page blueprint here as contract. Deviations require updating this document first.

---

## 目录

- [0. Meta: How to Use This Document](#0-meta-how-to-use-this-document)
- [1. Design Philosophy](#1-design-philosophy)
- [2. Reference Systems](#2-reference-systems)
- [3. Anti-Patterns (Current UI Problems)](#3-anti-patterns-current-ui-problems)
- [4. Visual Tokens](#4-visual-tokens)
- [5. Typography](#5-typography)
- [6. Spacing, Radius, Elevation](#6-spacing-radius-elevation)
- [7. Motion](#7-motion)
- [8. tokens.css (Full File)](#8-tokenscss-full-file)
- [9. tailwind.config.ts (Full File)](#9-tailwindconfigts-full-file)
- [10. Component Library](#10-component-library)
- [11. Page Blueprints](#11-page-blueprints)
- [12. Charts](#12-charts)
- [13. Interaction Patterns](#13-interaction-patterns)
- [14. File Structure](#14-file-structure)
- [15. Implementation Roadmap](#15-implementation-roadmap)
- [16. Before/After Gallery](#16-beforeafter-gallery)
- [Appendix A: Icon Inventory](#appendix-a-icon-inventory)
- [Appendix B: Copywriting Rules](#appendix-b-copywriting-rules)

---

## 0. Meta: How to Use This Document

**Audience**: AI coding assistants (Cursor, Claude Code) executing UI work on `apps/dsa-web`.

**Rules of engagement**:

1. **Tokens are immutable contracts.** Never introduce a color, font size, or spacing value that is not in Section 4–7. If you need a new value, add it to this doc first.
2. **Components are the unit of reuse.** When a page blueprint says `<DataTable>`, use the exact component defined in Section 10 — do not inline a one-off table.
3. **TypeScript props are the API.** Every component interface in Section 10 is binding. Do not rename props, do not add required props, do not remove props without updating this doc.
4. **ASCII wireframes define layout.** Section 11 wireframes are proportional — treat column widths and vertical ordering as specified.
5. **When uncertain, read Section 3 (Anti-Patterns).** Most design mistakes fall into the same 10 categories.

**What this document does NOT cover** (scope boundary):

- Backend API design → see `PROJECT_VISION.md`
- Business logic / analysis algorithms → see existing Python code
- Third-party service integration → see `.env.example`
- Testing strategy → out of scope here

---

## 1. Design Philosophy

### 1.1 We are building a Terminal, not a Dashboard

The difference is not cosmetic — it drives every decision.

| Dimension | Dashboard (wrong) | Terminal (right) |
|---|---|---|
| Primary audience | Executives reviewing metrics | Operators making decisions |
| Time per glance | 3 seconds | 30 seconds |
| Information density | Low (1 number = 1 card) | High (1 table = 50 numbers) |
| Visual hierarchy | From cards and colors | From typography and position |
| Decoration | Gauges, meters, radar | None — data is the decoration |
| Color palette | 6–10 colors for vibes | 4 colors for semantics |
| Navigation | Hub with summary tiles | List → detail drilldown |
| Font | Friendly sans (Inter, Poppins) | Neutral sans + tabular mono |

Our user is an experienced trader who spends hours looking at this UI. Every pixel of decoration is a pixel stolen from information.

### 1.2 Three non-negotiable rules

**Rule 1: Typography creates hierarchy, not cards.**
> A page that looks good in black-and-white (no borders, no tints — only font size / weight / opacity) has correct hierarchy. If removing all boxes makes the page confusing, the hierarchy was fake.

**Rule 2: Numbers are tabular, always.**
> Every digit in this product — prices, percentages, counts, timestamps — uses a monospace font with tabular figures. Non-tabular numbers in a trading UI are a defect, not a style choice.

**Rule 3: Color has semantics, not decoration.**
> Green = up/long/positive. Red = down/short/negative. Amber = warning/caution. Accent = interactive/selected. Gray = neutral/disabled. No other colors carry meaning. If you need a 6th color, you are decorating.

### 1.3 Aesthetic commitment: Linear Dark, trading-hardened

We commit to **Linear Dark** as our base aesthetic, with three trading-specific deviations:

- **Added mono typeface** (Geist Mono) for all numbers. Linear does not need this; we do.
- **Added semantic green/red** alongside Linear's violet accent. These are controlled, muted versions — not neon.
- **Tables-first layout** (Koyfin-influenced) rather than Linear's list-view cards. Linear manages 20 issues per screen; we manage 200 data points.

Everything else — background palette, border discipline, text contrast ratios, animation restraint, keyboard-first interaction — follows Linear's playbook as-is.

### 1.4 What this product is not

Explicit non-goals (do not build these, reject requests that slide toward them):

- ❌ A "beautiful" product in the consumer-app sense. This is a tool; tools optimize for operator throughput, not first impressions.
- ❌ A mobile-first product. Primary target is 1440px+ desktop. Mobile is a best-effort read-only viewport.
- ❌ An onboarded product. No tooltips explaining what MA3 means. The user knows.
- ❌ A light-mode product. Dark only. Do not build `@media (prefers-color-scheme: light)` support.
- ❌ A branded product. No logo flex, no marketing affordances, no "by Daily-Stock" chrome. It's a private tool.

---

## 2. Reference Systems

Study these. Do not clone them. Commit to one aesthetic synthesis.

| System | What we borrow | What we reject |
|---|---|---|
| **Linear** (linear.app) | Color depth, near-black backgrounds, subtle borders, violet accent, ⌘K palette, keyboard shortcuts, opacity-based hierarchy | Their app-like density; we need denser tables |
| **Koyfin** (koyfin.com) | Multi-pane layout, table-first data presentation, inline sparklines, sector heatmap patterns | Their light mode variant |
| **Bloomberg Terminal** | Color semantic discipline (green/red/white/amber), density, function-key mental model | Their 1990s visual style, fixed-width everything |
| **TradingView** | Candlestick rendering, drawing tools mental model, dark theme greens/reds | Their social/community UI |
| **Vercel Dashboard** | Zero-chrome pages, Geist typography, understated elevation | Its marketing-driven density (too sparse for us) |
| **GitHub Primer Dark** | Semantic color naming, issue-list density | Their medium blue accent (too cold) |
| **Notion** | None | Everything. Notion is the wrong reference for a terminal. |

### 2.1 Visual moodboard keywords

Commit to these; reject anything outside them:

- Dark, but not black-hole black — has slight blue undertone
- Confident small type — 13px body, 11px labels
- Tables with subtle horizontal rules, no verticals
- Numbers that line up perfectly (tabular-nums)
- Muted greens and reds — closer to `#3fb950` than `#00ff00`
- Single accent color used sparingly (selected row, primary button, active link)
- Borders thinner than you think — 1px at 8% white
- No gradients. Ever. (One exception: Section 12.4 chart area fill at 4% opacity.)
- No drop shadows. Elevation = background color delta, not blur.
- No emoji in UI chrome. Icons are lucide-react, 14–16px, stroke 1.5.

---

## 3. Anti-Patterns (Current UI Problems)

These are the specific defects in the current UI (Images 1 and 2 in user's message). When refactoring, check against this list.

### 3.1 Emoji as icons → lucide-react

```tsx
// ❌ WRONG (current codebase)
<span>📊 六维度归因详解</span>
<span>💡 Regime Score 方法论</span>
<span>🎯 狙击点位</span>

// ✅ RIGHT
import { ChartBar, Lightbulb, Target } from 'lucide-react'
<span><ChartBar size={14} /> Attribution detail</span>
```

Emojis render differently across OS (Apple vs. Google vs. Windows), break the mono/sans typographic system, and signal "amateur tool." Ban from all UI chrome. Allowed only in user-generated content (journal entries).

### 3.2 Stacked rounded cards → flat tables with rules

```tsx
// ❌ WRONG — current market snapshot
<div className="grid grid-cols-3 gap-4">
  <Card><Label>SPY close</Label><Value>$708.72</Value></Card>
  <Card><Label>VIX</Label><Value>18.87</Value></Card>
  <Card><Label>前日结构</Label><Value>—</Value></Card>
  {/* 3 more cards */}
</div>

// ✅ RIGHT — StatBar
<StatBar items={[
  { label: 'SPY', value: '708.72', delta: '-0.12%', sub: 'MA20 669.62' },
  { label: 'VIX', value: '18.87', delta: '-2.30%' },
  { label: 'Breadth', value: '62%', sub: '>MA20' },
  { label: 'Premkt', value: '+0.08%', sub: 'SPY' },
  { label: 'Updated', value: '09:10:16', sub: 'v1' },
]} />
```

Six cards for six numbers is a 6x overhead in borders, padding, and cognitive load. StatBar puts all 6 in a single horizontal strip at half the vertical height.

### 3.3 Radar chart → sorted contribution list

Radar charts (spider charts) have a known pathology: the enclosed *area* depends on the *order* of axes, which is meaningless. Two traders with identical scores but different axis orders get different visual impressions. Also — you cannot visually rank 6 numbers on a radar; you have to read labels.

```tsx
// ❌ WRONG — radar chart as primary visualization
<RadarChart data={dimensions} />

// ✅ RIGHT — sorted horizontal bars
<ContributionList items={[
  { label: 'Direction', value: 17, weight: 'MA slope + 50D trend' },
  { label: 'Sector',    value: 11, weight: 'Breadth of watchlist' },
  { label: 'Volatility',value: 10, weight: 'VIX level + term structure' },
  { label: 'Macro',     value: 0 },
  { label: 'Prev Day',  value: 0 },
  { label: 'Premarket', value: 0 },
]} />
```

The current UI *already has this bar list* on the right side of the radar — delete the radar, the bars alone are enough.

### 3.4 Gauge arc → number + label + sparkline

```tsx
// ❌ WRONG — 250×250px gauge showing a single number
<GaugeArc value={38} max={100} label="CAUTIOUS" />

// ✅ RIGHT — RegimeScore component, ~180×80px
<RegimeScore
  score={38}
  state="cautious"
  note="Half size; retests only; no 0DTE"
  history={last60Days}   // inline sparkline
/>
```

A gauge shows 1 number + 1 state in 60,000 pixels. A `RegimeScore` with sparkline shows 1 number + 1 state + 60 days of context in 14,400 pixels. Same information density would require ~4x less real estate.

### 3.5 Rainbow badge colors → one neutral badge system

The current UI uses 4+ colors for action badges (观望 yellow, 加仓 orange, 买入 green, 持有 blue, 减仓 red). This is Las Vegas, not Linear.

```tsx
// ❌ WRONG
<Badge color="yellow">观望 48</Badge>
<Badge color="green">买入 76</Badge>
<Badge color="blue">持有 62</Badge>
<Badge color="orange">加仓 75</Badge>

// ✅ RIGHT — semantic mapping, 3 states only
<Badge variant="bullish">BUY · 76</Badge>      // green, muted
<Badge variant="neutral">WATCH · 48</Badge>    // gray
<Badge variant="bearish">SELL · 25</Badge>     // red, muted
```

Consolidate to 3 stances (`bullish` / `neutral` / `bearish`). "加仓" and "买入" collapse into `bullish`; "观望" and "持有" collapse into `neutral`.

### 3.6 Decorative sentiment dial → inline key-value

The purple gradient 恐慌-贪婪 dial (Image 2, right side) is 400×400px for a single number (48). Replace with:

```tsx
<KeyValue label="Sentiment" value="48 · Neutral" />
```

A 14px line of text. The dial adds zero information.

### 3.7 Seven-color palette → four-color semantic system

Counted in current screenshots: teal (sidebar), purple (gauges), orange (CAUTIOUS), green (up/buy), red (down/sell), blue (MA5), amber (warnings), pink (sparklines). 7–8 colors with no rule.

Enforced palette (also see Section 4):
- 1 background family (5 shades of near-black)
- 1 text family (4 shades of off-white)
- 1 accent (Linear-ish violet)
- 3 semantic (green=up, red=down, amber=warn)

### 3.8 Over-decorated headings → uppercase labels

```tsx
// ❌ WRONG
<h2 className="text-xl font-bold text-cyan-400">📊 市场快照</h2>

// ✅ RIGHT — Section label style
<div className="text-[11px] font-medium uppercase tracking-[0.08em] text-text-3">
  Market snapshot
</div>
```

Small uppercase labels are the Linear/Koyfin standard for section headers. They recede visually, letting the data be the focal point.

### 3.9 Feature-stuffed home → list → detail pattern

Current NFLX page (Image 2) crams: history list (left), stock detail (center), sentiment dial (right), plan (below), news (below), trace (bottom). One screen, six responsibilities.

Correct model:
- **Home** (Regime page) = tactical status + watchlist table + recent signals list
- **Detail** (`/stocks/:code`) = full-screen, chart-first, with the current right-column content inlined

Rule: a page has one primary responsibility. If you can name two, split it.

### 3.10 Shadow-based elevation → background-delta elevation

Current UI uses `shadow-xl` / `shadow-lg` to lift cards. This creates a "floating cards on fog" aesthetic that is quintessentially plastic.

```tsx
// ❌ WRONG
<div className="bg-gray-800 rounded-xl shadow-xl p-6">...</div>

// ✅ RIGHT — elevation via bg delta + 1px border
<div className="bg-bg-1 border border-border-subtle rounded-md p-4">...</div>
// Hover state lifts by changing bg to bg-2, NOT by adding shadow
<div className="bg-bg-1 hover:bg-bg-2 border border-border-subtle rounded-md p-4">...</div>
```

Linear, Vercel, Koyfin — none use drop shadows. Elevation is a stack of lighter backgrounds, not blur.

---

## 4. Visual Tokens

All tokens are defined as CSS custom properties in `tokens.css` (full file in Section 8). This section documents the semantic meaning of each.

### 4.1 Background scale (5 steps)

| Token | Hex | Usage |
|---|---|---|
| `--bg-0` | `#08090a` | Page background (document body) |
| `--bg-1` | `#0e0f11` | Primary surface (main content regions, table backgrounds) |
| `--bg-2` | `#16171a` | Elevated surface (hovered row, modal, popover, dropdown) |
| `--bg-3` | `#1e1f23` | Highest elevation (selected row, active button state) |
| `--bg-accent-subtle` | `rgba(113,112,255,0.08)` | Accent-tinted background for selected states |

**Why 5 steps?** Linear uses 4; we add one for selected-row states which need to be clearly distinct from hover. Pages must never use more than 3 levels simultaneously — too many elevations flatten the hierarchy.

### 4.2 Border scale (3 steps)

| Token | Hex | Usage |
|---|---|---|
| `--border-subtle` | `#1d1e22` | Default border (table rules, card outlines, input borders) |
| `--border-default` | `#26272c` | Stronger border (focused input, active tab underline) |
| `--border-strong` | `#3a3b41` | Emphatic border (only for disabled states or separators between major regions) |

### 4.3 Text scale (4 steps)

| Token | Hex | Usage |
|---|---|---|
| `--text-1` | `#e9e9ec` | Primary text (body copy, prices, headings) |
| `--text-2` | `#a5a5ad` | Secondary text (metadata, timestamps, supporting labels) |
| `--text-3` | `#6f6f78` | Tertiary text (section labels, placeholder, disabled inline) |
| `--text-4` | `#4a4a52` | Quaternary text (disabled, not-yet-loaded) |

**Contrast verification** (against `--bg-0`):
- `--text-1`: 15.1:1 ✓ AAA
- `--text-2`: 7.4:1 ✓ AAA large / AA normal
- `--text-3`: 3.8:1 ✓ AA large only — use for 11px+ labels, never for body copy
- `--text-4`: 2.0:1 — disabled only

### 4.4 Accent (single color)

| Token | Hex | Usage |
|---|---|---|
| `--accent` | `#7170ff` | Primary interactive color (primary button, active link, focus ring, selected row tint) |
| `--accent-hover` | `#8584ff` | Accent on hover |
| `--accent-active` | `#5e5dff` | Accent on pressed/active |
| `--accent-subtle-bg` | `rgba(113,112,255,0.12)` | Accent tinted background |
| `--accent-subtle-border` | `rgba(113,112,255,0.24)` | Accent tinted border |

**Why violet-blue?** Linear's signature. Cooler than blue (which is overused in finance — everyone uses GitHub `#58a6ff`), warmer than pure indigo (which reads as corporate). One accent color, not two — never introduce a "secondary accent."

### 4.5 Semantic colors (3 pairs)

```
UP / LONG / POSITIVE
  --up-strong: #3fb950          (price text, significant change)
  --up-muted:  #238636           (badge background, bar fills)
  --up-subtle: rgba(63,185,80,0.12)  (cell tint, row accent)

DOWN / SHORT / NEGATIVE
  --down-strong: #f85149         (price text, significant change)
  --down-muted:  #da3633          (badge background, bar fills)
  --down-subtle: rgba(248,81,73,0.12) (cell tint, row accent)

WARN / CAUTION
  --warn-strong: #d29922         (regime caution, risk warnings)
  --warn-muted:  #9e6a03          (badge background)
  --warn-subtle: rgba(210,153,34,0.12)

FLAT / NEUTRAL — no dedicated color, use --text-3
```

**Deliberate decisions**:
- Greens and reds are *muted* (`#3fb950` not `#22c55e`, `#f85149` not `#ef4444`). Terminals run 8+ hours/day; oversaturated colors cause fatigue.
- No "info blue" token. Info messages use `--accent`.
- No "success green" token. Success is the same green as `--up-strong` — reusing the semantic is correct.
- No gradients at token level. If a chart area fill is needed (Section 12.4), use `--accent` at 4% opacity, linear top-to-bottom, not a multi-stop gradient.

### 4.6 Data viz colors (for multi-series charts only)

Used only in rare multi-series line charts (e.g., comparing 5 stocks). Never in UI chrome.

```
--chart-1: #7170ff  (accent — primary series)
--chart-2: #3fb950  (up-green — second series)
--chart-3: #f85149  (down-red — third series)
--chart-4: #d29922  (amber — fourth series)
--chart-5: #06b6d4  (cyan — fifth series, rarely needed)
--chart-6: #a855f7  (purple, rarely needed)
```

If you need more than 6 series, the chart is wrong — build a small-multiples grid instead.

---

## 5. Typography

### 5.1 Font stack

**Primary (sans)**: [Geist Sans](https://vercel.com/font)
```css
font-family: "Geist", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
```

**Numeric (mono)**: [Geist Mono](https://vercel.com/font)
```css
font-family: "Geist Mono", ui-monospace, "SF Mono", "JetBrains Mono", Consolas, monospace;
font-variant-numeric: tabular-nums;
font-feature-settings: "tnum" 1, "ss01" 1, "zero" 1;   /* tabular + slashed zero */
```

**Why Geist?** It is designed for developer-facing dark interfaces, has tabular figures, pairs Sans + Mono with identical design language, is free under OFL license, and is not Inter (per aesthetic brief). Supreme (Fontshare) is the acceptable alternative if Geist cannot be loaded.

**Installation**: use `@vercel/font` npm package or self-host the WOFF2 files under `apps/dsa-web/public/fonts/`. Load via `@font-face` with `font-display: swap`.

### 5.2 Type scale

| Name | Size | Line | Weight | Letter-spacing | Use |
|---|---|---|---|---|---|
| `display` | 28px | 1.15 | 600 | -0.02em | Page titles (rarely — most pages use `h1`) |
| `h1` | 20px | 1.25 | 600 | -0.01em | Page section header ("Regime Score") |
| `h2` | 16px | 1.3 | 600 | -0.005em | Sub-section header |
| `h3` | 13px | 1.4 | 600 | 0 | Component header |
| `body` | 13px | 1.5 | 400 | 0 | Body copy, default text size |
| `body-sm` | 12px | 1.4 | 400 | 0 | Secondary info, table cells |
| `label` | 11px | 1.3 | 500 | 0.08em · uppercase | Section labels, tab headers |
| `caption` | 11px | 1.3 | 400 | 0 | Metadata, timestamps |
| `mono-lg` | 20px | 1.2 | 500 | 0 | Large numbers (regime score, prices in detail header) |
| `mono-md` | 13px | 1.4 | 450 | 0 | Default number size (table cells, inline prices) |
| `mono-sm` | 12px | 1.3 | 450 | 0 | Dense tables |
| `mono-xs` | 11px | 1.2 | 450 | 0 | Extreme-density tables (rare) |

**Weight mapping**: Geist offers 100–900. We use only 400, 450, 500, 600. No bolder than 600 (bold in dark UIs looks aggressive). No 300 or lighter (unreadable at 12–13px on dark bg).

### 5.3 Type rules

1. **Body is 13px, not 16px.** Standard web defaults to 16px; terminals default to 12–14px. 13px keeps readability while maximizing density.
2. **All numbers use Geist Mono with `tabular-nums`.** Prices, percentages, counts, IDs, timestamps. Non-tabular numbers in a table are a visual defect.
3. **Labels are uppercase with 0.08em tracking.** This is the Linear/Koyfin convention; it creates a clear "this is a label, not content" signal without using color.
4. **No text smaller than 11px.** Below 11px, anti-aliasing makes numbers unreadable on dark backgrounds.
5. **Headings never use color for emphasis.** Weight and size handle it. A colored heading signals either a link or a problem with the design.

### 5.4 Example: typography composition

```tsx
// Page header — stock detail
<div>
  {/* Label */}
  <div className="font-sans text-label text-text-3">NASDAQ</div>

  {/* Ticker + name */}
  <div className="mt-1 flex items-baseline gap-3">
    <h1 className="font-sans text-h1 font-semibold text-text-1">NFLX</h1>
    <span className="font-sans text-body text-text-2">Netflix, Inc.</span>
  </div>

  {/* Price row — monospace with semantic color */}
  <div className="mt-3 flex items-baseline gap-4">
    <span className="font-mono text-mono-lg text-text-1 tabular-nums">93.82</span>
    <span className="font-mono text-mono-md text-down-strong tabular-nums">−3.58%</span>
    <span className="font-mono text-mono-md text-down-strong tabular-nums">−3.48</span>
    <span className="font-sans text-caption text-text-3">04/20 12:23 ET</span>
  </div>
</div>
```

---

## 6. Spacing, Radius, Elevation

### 6.1 Spacing scale (4px base)

```
--space-0:   0
--space-1:   4px     /* between label and value */
--space-2:   8px     /* table cell padding, button internal */
--space-3:   12px    /* component internal padding */
--space-4:   16px    /* card padding, default gap */
--space-5:   20px
--space-6:   24px    /* between major components */
--space-8:   32px    /* page-level section spacing */
--space-10:  40px
--space-12:  48px    /* between page sections */
--space-16:  64px    /* rare — top-of-page spacing */
```

**Rules**:
- Inside a component: `--space-2` or `--space-3`.
- Between related components: `--space-4`.
- Between unrelated sections: `--space-8` or `--space-12`.
- Never use 6px, 10px, 14px, 18px. If a design needs these, re-examine the design.

### 6.2 Radius scale

```
--radius-none: 0
--radius-sm:   3px     /* default for buttons, inputs, badges */
--radius-md:   6px     /* cards, popovers */
--radius-lg:   8px     /* MAX — used for modals only */
--radius-full: 9999px  /* pills, avatars (rare in our UI) */
```

**Critical**: radii max out at 8px. `rounded-xl` (12px) and `rounded-2xl` (16px) are banned — they signal "consumer app" not "terminal." The current UI's extensive use of `rounded-xl` / `rounded-2xl` is the single biggest source of its plastic feel.

### 6.3 Elevation (background delta, not shadow)

```
Level 0:  --bg-0   (page)
Level 1:  --bg-1   (default surface)
Level 2:  --bg-2   (hover, dropdown)
Level 3:  --bg-3   (pressed, selected)
```

Shadow tokens exist but are used sparingly:
```
--shadow-sm: 0 1px 2px rgba(0,0,0,0.4)                           /* dropdown */
--shadow-md: 0 4px 12px rgba(0,0,0,0.5), 0 0 0 1px var(--border-default)  /* modal */
```

**Rule**: use shadow ONLY for popovers, dropdowns, modals, and toasts — anything that floats above the main layout. Never use shadow for cards that are part of the page flow.

### 6.4 Border widths

- Hairline: `0.5px` (retina only — rendered as 1px in Tailwind via `border`)
- Standard: `1px` (default)
- Emphasis: `2px` (focused input, active tab underline)

Never thicker than 2px. A 3px+ border is a graphic, not a boundary.

---

## 7. Motion

### 7.1 Principle: motion is feedback, never decoration

Animation is acceptable ONLY for:
- State transitions (hover, focus, active) — 120ms
- Enter/exit of surfaces (dropdowns, modals, toasts) — 180ms
- Position changes driven by data (e.g., a table row being added) — 240ms

Banned:
- Entrance animations on page load ("fade in from bottom") — delays information
- Parallax
- Breathing/pulsing dots (except for "live" connection indicators)
- Bouncing, spinning, or scaling that lasts >300ms

### 7.2 Duration tokens

```
--duration-fast:    120ms   /* hover, focus */
--duration-mid:     180ms   /* dropdowns, popovers */
--duration-slow:    240ms   /* row additions, chart transitions */
```

### 7.3 Easing tokens

```
--ease-out: cubic-bezier(0.16, 1, 0.3, 1)         /* default for entrances */
--ease-in:  cubic-bezier(0.7, 0, 0.84, 0)         /* for exits */
--ease-inout: cubic-bezier(0.87, 0, 0.13, 1)      /* for state changes */
```

### 7.4 Reduced motion

Respect `@media (prefers-reduced-motion: reduce)` by setting all durations to 0ms. The app must be fully functional and visually coherent without any animation.

---

## 8. tokens.css (Full File)

Place at `apps/dsa-web/src/styles/tokens.css`. Import once from `main.tsx` before any other CSS.

```css
/* apps/dsa-web/src/styles/tokens.css */
/* Daily-Stock design tokens — generated from DESIGN_SYSTEM.md v1.0 */
/* Do not edit values here. Edit the doc, then regenerate. */

:root {
  /* ============================================================ */
  /* COLOR — BACKGROUND                                           */
  /* ============================================================ */
  --bg-0: #08090a;
  --bg-1: #0e0f11;
  --bg-2: #16171a;
  --bg-3: #1e1f23;

  /* ============================================================ */
  /* COLOR — BORDER                                               */
  /* ============================================================ */
  --border-subtle:  #1d1e22;
  --border-default: #26272c;
  --border-strong:  #3a3b41;

  /* ============================================================ */
  /* COLOR — TEXT                                                 */
  /* ============================================================ */
  --text-1: #e9e9ec;
  --text-2: #a5a5ad;
  --text-3: #6f6f78;
  --text-4: #4a4a52;

  /* ============================================================ */
  /* COLOR — ACCENT (violet-blue, Linear-inspired)               */
  /* ============================================================ */
  --accent:                #7170ff;
  --accent-hover:          #8584ff;
  --accent-active:         #5e5dff;
  --accent-subtle-bg:      rgba(113, 112, 255, 0.12);
  --accent-subtle-border:  rgba(113, 112, 255, 0.24);

  /* ============================================================ */
  /* COLOR — SEMANTIC                                             */
  /* ============================================================ */
  --up-strong:  #3fb950;
  --up-muted:   #238636;
  --up-subtle:  rgba(63, 185, 80, 0.12);

  --down-strong: #f85149;
  --down-muted:  #da3633;
  --down-subtle: rgba(248, 81, 73, 0.12);

  --warn-strong: #d29922;
  --warn-muted:  #9e6a03;
  --warn-subtle: rgba(210, 153, 34, 0.12);

  /* ============================================================ */
  /* COLOR — CHART SERIES (multi-series only)                    */
  /* ============================================================ */
  --chart-1: #7170ff;
  --chart-2: #3fb950;
  --chart-3: #f85149;
  --chart-4: #d29922;
  --chart-5: #06b6d4;
  --chart-6: #a855f7;

  /* ============================================================ */
  /* TYPOGRAPHY — FONT STACKS                                    */
  /* ============================================================ */
  --font-sans: "Geist", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "Geist Mono", ui-monospace, "SF Mono", "JetBrains Mono", Consolas, monospace;

  /* ============================================================ */
  /* TYPOGRAPHY — SIZE                                           */
  /* ============================================================ */
  --text-display: 28px;
  --text-h1:      20px;
  --text-h2:      16px;
  --text-h3:      13px;
  --text-body:    13px;
  --text-body-sm: 12px;
  --text-label:   11px;
  --text-caption: 11px;

  --text-mono-lg: 20px;
  --text-mono-md: 13px;
  --text-mono-sm: 12px;
  --text-mono-xs: 11px;

  /* ============================================================ */
  /* TYPOGRAPHY — LINE HEIGHT                                    */
  /* ============================================================ */
  --leading-tight:   1.15;
  --leading-snug:    1.25;
  --leading-normal:  1.4;
  --leading-relaxed: 1.5;

  /* ============================================================ */
  /* TYPOGRAPHY — LETTER SPACING                                 */
  /* ============================================================ */
  --tracking-tight:  -0.02em;
  --tracking-normal: 0;
  --tracking-label:  0.08em;

  /* ============================================================ */
  /* SPACING                                                     */
  /* ============================================================ */
  --space-0:  0;
  --space-1:  4px;
  --space-2:  8px;
  --space-3:  12px;
  --space-4:  16px;
  --space-5:  20px;
  --space-6:  24px;
  --space-8:  32px;
  --space-10: 40px;
  --space-12: 48px;
  --space-16: 64px;

  /* ============================================================ */
  /* RADIUS                                                      */
  /* ============================================================ */
  --radius-none: 0;
  --radius-sm:   3px;
  --radius-md:   6px;
  --radius-lg:   8px;
  --radius-full: 9999px;

  /* ============================================================ */
  /* SHADOW (minimal use — popovers/modals only)                 */
  /* ============================================================ */
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.4);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.5), 0 0 0 1px var(--border-default);

  /* ============================================================ */
  /* MOTION                                                      */
  /* ============================================================ */
  --duration-fast:  120ms;
  --duration-mid:   180ms;
  --duration-slow:  240ms;

  --ease-out:   cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in:    cubic-bezier(0.7, 0, 0.84, 0);
  --ease-inout: cubic-bezier(0.87, 0, 0.13, 1);

  /* ============================================================ */
  /* Z-INDEX                                                     */
  /* ============================================================ */
  --z-sticky:   10;
  --z-dropdown: 20;
  --z-popover:  30;
  --z-modal:    40;
  --z-toast:    50;
  --z-cmdk:     60;
}

/* Global base */
html, body {
  background: var(--bg-0);
  color: var(--text-1);
  font-family: var(--font-sans);
  font-size: var(--text-body);
  line-height: var(--leading-relaxed);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* Always show a subtle scrollbar that matches the theme */
* {
  scrollbar-width: thin;
  scrollbar-color: var(--border-default) transparent;
}
*::-webkit-scrollbar { width: 8px; height: 8px; }
*::-webkit-scrollbar-track { background: transparent; }
*::-webkit-scrollbar-thumb {
  background: var(--border-default);
  border-radius: var(--radius-sm);
}
*::-webkit-scrollbar-thumb:hover { background: var(--border-strong); }

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0ms !important;
    transition-duration: 0ms !important;
  }
}

/* Disable browser blue focus ring, replace with accent ring */
:focus { outline: none; }
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
```

---

## 9. tailwind.config.ts (Full File)

Place at `apps/dsa-web/tailwind.config.ts`. This maps tokens into Tailwind utilities so components can use `className="bg-bg-1 text-text-2"` rather than arbitrary values.

```ts
// apps/dsa-web/tailwind.config.ts
import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class', // we always apply .dark on <html>, no class toggling
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    // DO NOT extend the default theme — replace it. We want strict token usage.
    screens: {
      sm: '640px',
      md: '768px',
      lg: '1024px',
      xl: '1280px',
      '2xl': '1440px',    // primary target
      '3xl': '1920px',
    },
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      bg: {
        0: 'var(--bg-0)',
        1: 'var(--bg-1)',
        2: 'var(--bg-2)',
        3: 'var(--bg-3)',
      },
      border: {
        subtle: 'var(--border-subtle)',
        DEFAULT: 'var(--border-default)',
        strong: 'var(--border-strong)',
      },
      text: {
        1: 'var(--text-1)',
        2: 'var(--text-2)',
        3: 'var(--text-3)',
        4: 'var(--text-4)',
      },
      accent: {
        DEFAULT: 'var(--accent)',
        hover:   'var(--accent-hover)',
        active:  'var(--accent-active)',
        'subtle-bg':     'var(--accent-subtle-bg)',
        'subtle-border': 'var(--accent-subtle-border)',
      },
      up: {
        strong: 'var(--up-strong)',
        muted:  'var(--up-muted)',
        subtle: 'var(--up-subtle)',
      },
      down: {
        strong: 'var(--down-strong)',
        muted:  'var(--down-muted)',
        subtle: 'var(--down-subtle)',
      },
      warn: {
        strong: 'var(--warn-strong)',
        muted:  'var(--warn-muted)',
        subtle: 'var(--warn-subtle)',
      },
      chart: {
        1: 'var(--chart-1)',
        2: 'var(--chart-2)',
        3: 'var(--chart-3)',
        4: 'var(--chart-4)',
        5: 'var(--chart-5)',
        6: 'var(--chart-6)',
      },
    },
    fontFamily: {
      sans: 'var(--font-sans)',
      mono: 'var(--font-mono)',
    },
    fontSize: {
      display: ['var(--text-display)', { lineHeight: 'var(--leading-tight)', fontWeight: '600', letterSpacing: 'var(--tracking-tight)' }],
      h1:      ['var(--text-h1)',      { lineHeight: 'var(--leading-snug)',  fontWeight: '600' }],
      h2:      ['var(--text-h2)',      { lineHeight: 'var(--leading-snug)',  fontWeight: '600' }],
      h3:      ['var(--text-h3)',      { lineHeight: 'var(--leading-normal)',fontWeight: '600' }],
      body:    ['var(--text-body)',    { lineHeight: 'var(--leading-relaxed)' }],
      'body-sm': ['var(--text-body-sm)', { lineHeight: 'var(--leading-normal)' }],
      label:   ['var(--text-label)',   { lineHeight: 'var(--leading-normal)', letterSpacing: 'var(--tracking-label)', fontWeight: '500' }],
      caption: ['var(--text-caption)', { lineHeight: 'var(--leading-normal)' }],
      'mono-lg': ['var(--text-mono-lg)', { lineHeight: 'var(--leading-tight)',  fontWeight: '500' }],
      'mono-md': ['var(--text-mono-md)', { lineHeight: 'var(--leading-normal)' }],
      'mono-sm': ['var(--text-mono-sm)', { lineHeight: 'var(--leading-normal)' }],
      'mono-xs': ['var(--text-mono-xs)', { lineHeight: 'var(--leading-snug)' }],
    },
    spacing: {
      0:  'var(--space-0)',
      1:  'var(--space-1)',
      2:  'var(--space-2)',
      3:  'var(--space-3)',
      4:  'var(--space-4)',
      5:  'var(--space-5)',
      6:  'var(--space-6)',
      8:  'var(--space-8)',
      10: 'var(--space-10)',
      12: 'var(--space-12)',
      16: 'var(--space-16)',
    },
    borderRadius: {
      none: 'var(--radius-none)',
      sm:   'var(--radius-sm)',
      md:   'var(--radius-md)',
      lg:   'var(--radius-lg)',
      full: 'var(--radius-full)',
    },
    boxShadow: {
      none: 'none',
      sm: 'var(--shadow-sm)',
      md: 'var(--shadow-md)',
    },
    transitionDuration: {
      fast: 'var(--duration-fast)',
      mid:  'var(--duration-mid)',
      slow: 'var(--duration-slow)',
    },
    transitionTimingFunction: {
      out:   'var(--ease-out)',
      in:    'var(--ease-in)',
      inout: 'var(--ease-inout)',
    },
    zIndex: {
      sticky:   'var(--z-sticky)',
      dropdown: 'var(--z-dropdown)',
      popover:  'var(--z-popover)',
      modal:    'var(--z-modal)',
      toast:    'var(--z-toast)',
      cmdk:     'var(--z-cmdk)',
    },
    extend: {
      fontVariantNumeric: {
        'tabular-nums': 'tabular-nums',
      },
    },
  },
  plugins: [],
} satisfies Config
```

---

## 10. Component Library

Each component spec includes:
- **Purpose**: when to use
- **Anti-use**: when NOT to use
- **File path**: where to place it in `apps/dsa-web/src/components/`
- **TypeScript interface**: exact prop contract
- **Visual spec**: dimensions, states, example render
- **Usage example**: copy-paste starting point

---

### 10.1 `<Button>`

**Purpose**: Primary interactive element for actions.
**Anti-use**: Do not use for navigation (use `<Link>` / router). Do not use for toggles (use `<Toggle>`).
**File**: `src/components/ui/Button.tsx`

```ts
interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'          // no 'lg' — we do not need large buttons
  loading?: boolean
  iconLeft?: LucideIcon
  iconRight?: LucideIcon
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
  fullWidth?: boolean
}
```

**Visual spec**:

```
Size sm:  height 28px, px-3, text-body-sm, gap-1.5 between icon and label
Size md:  height 32px, px-4, text-body,    gap-2   between icon and label

Variant primary:
  bg: accent
  text: white (#ffffff — one of only two places white is used)
  hover: bg accent-hover
  active: bg accent-active
  disabled: bg text-4, text text-3

Variant secondary:
  bg: bg-2
  text: text-1
  border: 1px border-subtle
  hover: bg bg-3
  disabled: bg bg-1, text text-3

Variant ghost:
  bg: transparent
  text: text-2
  hover: bg bg-2, text text-1

Variant danger:
  bg: down-muted
  text: white
  hover: bg down-strong
```

**Example**:
```tsx
<Button variant="primary" size="md" iconLeft={Play}>
  Run analysis
</Button>

<Button variant="secondary" size="sm">
  Cancel
</Button>
```

---

### 10.2 `<IconButton>`

**Purpose**: Square icon-only button, used in toolbars and table row actions.
**File**: `src/components/ui/IconButton.tsx`

```ts
interface IconButtonProps {
  icon: LucideIcon
  variant?: 'ghost' | 'secondary'
  size?: 'sm' | 'md'
  'aria-label': string          // REQUIRED
  onClick?: () => void
  disabled?: boolean
}
```

**Visual spec**: `sm` = 24x24px, `md` = 28x28px. Icon size is 14px for sm, 16px for md.

---

### 10.3 `<Input>` / `<SearchInput>`

**File**: `src/components/ui/Input.tsx`

```ts
interface InputProps {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  size?: 'sm' | 'md'
  iconLeft?: LucideIcon
  iconRight?: LucideIcon
  disabled?: boolean
  autoFocus?: boolean
  type?: 'text' | 'number' | 'search'
}
```

**Visual spec**:
```
Height: 28px (sm) / 32px (md)
Background: bg-1 (or bg-2 if nested inside a card that's already bg-1)
Border: 1px border-subtle
Focus: border accent (2px), no glow
Placeholder: text-3
Text: text-1
```

Do not use `boxShadow` on focus. The 2px border shift is enough.

---

### 10.4 `<Tabs>`

**File**: `src/components/ui/Tabs.tsx`

```ts
interface TabsProps {
  value: string
  onChange: (v: string) => void
  items: { value: string; label: string; count?: number }[]
  variant?: 'underline' | 'pills'    // default: underline
}
```

**Visual spec (underline)**:
```
Container: border-b border-subtle
Tab:       px-3 py-2, text-body, text-text-2
Active:    text-text-1, border-b-2 border-accent, negative margin-bottom -1px to sit on container border
Hover:     text-text-1
Count:     text-text-3, ml-2, font-mono text-mono-sm
```

Pills variant only for rare cases (filter chips).

---

### 10.5 `<DataTable>`

The workhorse of this UI. Used for watchlist, signals, analysis history, portfolio, etc.

**File**: `src/components/ui/DataTable.tsx` (wrapper around `@tanstack/react-table`)

```ts
interface DataTableProps<T> {
  data: T[]
  columns: ColumnDef<T>[]
  density?: 'compact' | 'regular'    // compact = mono-xs, regular = mono-sm
  selection?: 'none' | 'single' | 'multiple'
  onRowClick?: (row: T) => void
  sortable?: boolean
  stickyHeader?: boolean
  emptyState?: React.ReactNode
  loading?: boolean
}
```

**Visual spec**:

```
Table:       w-full, border-collapse
Header:
  Background: bg-1 (sticky top-0 if stickyHeader)
  Height:     28px (compact) / 32px (regular)
  Text:       text-label, uppercase, text-text-3
  Border:     border-b border-subtle
  Padding:    px-3 (first/last cell: pl-4 / pr-4)

Row:
  Height:       28px (compact) / 36px (regular)
  Background:   transparent
  Border:       border-b border-subtle (NOT on last row)
  Hover:        bg-2
  Selected:     bg-accent-subtle-bg, left border-l 2px accent
  onClick:      cursor-pointer

Cell:
  Padding:      px-3, py-0 (rely on row height)
  Alignment:    numbers right, text left, icons center
  Font:         sans for text, mono for numbers
```

**Critical rules**:
1. No vertical cell borders. Horizontal row rules only.
2. No alternating row backgrounds ("zebra"). Research shows they reduce scan speed on numeric tables.
3. Headers are `text-label` (uppercase, 11px, tracking-label). Never bolded sentence case.
4. Numeric columns are right-aligned. Always. This is a law.
5. Sort indicators are small carets (4px), not arrows. Accent colored when active.

**Example**:
```tsx
<DataTable
  data={watchlist}
  columns={[
    { key: 'ticker',  label: 'Ticker', width: 80  },
    { key: 'last',    label: 'Last',   width: 80, align: 'right', cell: (r) => <PriceCell value={r.last} /> },
    { key: 'chgPct',  label: 'Chg%',   width: 80, align: 'right', cell: (r) => <ChangeCell value={r.chgPct} /> },
    { key: 'vol',     label: 'Vol/Avg',width: 80, align: 'right', cell: (r) => <MonoCell value={r.vol} /> },
    { key: 'maSlope', label: 'MA 3/5/13', width: 100, cell: (r) => <MASlopeCell trend={r.maSlope} /> },
    { key: 'event',   label: 'Next',   width: 100, cell: (r) => <EventCell event={r.nextEvent} /> },
  ]}
  onRowClick={(r) => navigate(`/stocks/${r.ticker}`)}
  density="regular"
  stickyHeader
/>
```

---

### 10.6 `<PriceCell>` / `<ChangeCell>`

**Purpose**: Render a price or change value with correct mono font and semantic color.

**File**: `src/components/data/PriceCell.tsx`

```ts
interface PriceCellProps {
  value: number
  currency?: string       // default: none (assume USD)
  decimals?: number       // default: 2
  colorize?: boolean      // color the number green/red based on sign? default: false
  size?: 'sm' | 'md'
}

interface ChangeCellProps {
  value: number          // the change value (absolute or pct)
  mode: 'absolute' | 'percent'
  decimals?: number
  showArrow?: boolean    // show ▲/▼ prefix, default false
  size?: 'sm' | 'md'
}
```

**Visual spec**:

```
PriceCell:
  Font: mono (tabular)
  Color: text-1 (unless colorize=true)
  Format: locale-aware thousands separator (US: 1,234.56)

ChangeCell:
  Font: mono (tabular)
  Positive: text-up-strong, prefix "+", e.g. "+1.82%" or "+2.45"
  Negative: text-down-strong, native "-" sign, e.g. "−2.45%"   (use true minus U+2212, not hyphen)
  Flat:     text-text-3, "0.00%"
  Arrow:    8px triangle, positioned 4px before the number
```

**Why separate components?** Because 80% of our UI is price and change cells, and consistency matters more than flexibility. A global rename of `+` vs `▲` should edit one file.

---

### 10.7 `<Sparkline>`

**Purpose**: Inline 30-day trend visualization, ~80×20px.

**File**: `src/components/data/Sparkline.tsx`

```ts
interface SparklineProps {
  data: number[]
  width?: number       // default 80
  height?: number      // default 20
  colorize?: boolean   // green if trending up, red if down
  showDot?: boolean    // dot on last value
}
```

**Implementation**: SVG `<polyline>`, no library. 80 points × 20px height → 1px per point. Line stroke 1px, color text-2 by default or up/down-strong if colorize=true.

Do not use Recharts for sparklines — it adds 40KB and tooltip complexity we don't need.

---

### 10.8 `<StatBar>`

**Purpose**: Horizontal strip of key-value stats. Replaces "6 tiny cards" anti-pattern.

**File**: `src/components/data/StatBar.tsx`

```ts
interface StatBarItem {
  label: string
  value: string           // pre-formatted (component doesn't know units)
  delta?: string          // optional change indicator
  deltaPositive?: boolean // colors the delta
  sub?: string            // smaller text below
  icon?: LucideIcon
}

interface StatBarProps {
  items: StatBarItem[]
  separator?: 'line' | 'space'    // default 'line' (vertical border between items)
}
```

**Visual spec**:

```
Container: flex, bg-1, border border-subtle, rounded-md, px-4, h-12
Item:     flex-1, pr-6, (first: pl-0), (between: border-r border-subtle)
  Label:  text-label, text-text-3
  Value:  mono-md, text-text-1, mt-0.5
  Delta:  mono-sm, text-up/down/3, ml-2
  Sub:    caption, text-text-3, mt-0.5
```

**Example**:
```tsx
<StatBar items={[
  { label: 'SPY',     value: '708.72', delta: '−0.12%', deltaPositive: false, sub: 'MA20 669.62' },
  { label: 'VIX',     value: '18.87',  delta: '−2.30%', deltaPositive: true,  sub: '5d −4.1%' },
  { label: 'Breadth', value: '62%',    sub: '>MA20' },
  { label: 'Premkt',  value: '+0.08%', deltaPositive: true, sub: 'SPY fut' },
  { label: 'Updated', value: '09:10',  sub: 'v1 · ET' },
]} />
```

Renders at `h-12` (48px) — same visual weight as a single row of data.

---

### 10.9 `<Badge>`

**Purpose**: Inline status tag. Used in tables for stance, regime state, signal type.

**File**: `src/components/ui/Badge.tsx`

```ts
interface BadgeProps {
  variant: 'neutral' | 'bullish' | 'bearish' | 'warn' | 'accent'
  size?: 'sm' | 'md'
  children: React.ReactNode
}
```

**Visual spec**:

```
Size sm: h-5 (20px), px-1.5, text-label
Size md: h-6 (24px), px-2,   text-body-sm, medium weight

neutral: bg-bg-2, text-text-2, border border-subtle
bullish: bg-up-subtle, text-up-strong, (no border)
bearish: bg-down-subtle, text-down-strong
warn:    bg-warn-subtle, text-warn-strong
accent:  bg-accent-subtle-bg, text-accent, border border-accent-subtle-border
```

**Rule**: max 1 badge per table row. Multiple badges in the same row = decoration. If you need multiple states, use separate columns.

---

### 10.10 `<RegimeScore>`

**Purpose**: Display the overall regime score. Replaces the gauge arc.

**File**: `src/components/regime/RegimeScore.tsx`

```ts
interface RegimeScoreProps {
  score: number               // -100 to 100
  state: 'aggressive' | 'standard' | 'cautious' | 'no_trade'
  note?: string               // e.g., "Half size; retests only; no 0DTE"
  history?: number[]          // last 30-60 days, for inline sparkline
  updatedAt?: Date
  version?: string            // e.g., "v1"
}
```

**Visual spec** (180×90px):

```
┌──────────────────────────────────────────────┐
│ REGIME                                       │  label, text-text-3
│                                              │
│ +38    CAUTIOUS     [sparkline 60d]          │  mono-lg | text-warn-strong badge-style | sparkline
│                                              │
│ Half size; retests only; no 0DTE             │  caption, text-text-3
│ v1 · 09:10:16 ET                             │  caption, text-text-4
└──────────────────────────────────────────────┘
```

State mapping:
- `aggressive` → up-strong color, "AGGRESSIVE" label
- `standard` → text-1 color, "STANDARD" label
- `cautious` → warn-strong color, "CAUTIOUS" label
- `no_trade` → down-strong color, "NO TRADE" label

No gauge arc. No circular meter. No animation on score change (too distracting when refreshing).

---

### 10.11 `<ContributionList>`

**Purpose**: Horizontal bar list showing each dimension's contribution to regime score.

**File**: `src/components/regime/ContributionList.tsx`

```ts
interface ContributionItem {
  label: string
  value: number
  description?: string
}
interface ContributionListProps {
  items: ContributionItem[]
  maxAbsValue?: number   // for scaling bars; default: auto-detect
  sortByAbs?: boolean    // default true — largest contributions first
}
```

**Visual spec**:

```
Item row (h-7, py-1.5):
  Label:       w-24, text-body, text-text-1
  Bar:         flex-1, h-1.5, bg-bg-2, rounded-sm, relative
    Fill:      absolute left-1/2, h-full, bg-accent (or up/down), w-[value-mapped]
  Value:       w-10, mono-sm, text-right, text-text-1
  Description: flex-1, text-caption, text-text-3 (optional, shown on hover)
```

Zero values render as empty bars (visible track, no fill) with value "0" in text-3.

**Direction**: positive values extend right from center, negative left. This makes signed contributions visually obvious.

---

### 10.12 `<DataSourceStatus>`

**Purpose**: Show health of upstream data providers. Used in system banner.

**File**: `src/components/system/DataSourceStatus.tsx`

```ts
interface DataSource {
  name: string              // "yfinance" | "Alpaca" | "Finnhub" | "Longbridge"
  status: 'ok' | 'partial' | 'missing' | 'error'
  detail?: string           // "2/4 blocks ok" etc.
}
interface DataSourceStatusProps {
  sources: DataSource[]
  variant?: 'bar' | 'list'  // bar = inline status pills, list = stacked rows
}
```

**Visual spec (bar)**:

```
flex, gap-3, text-caption:

  ● yfinance partial    ● Alpaca missing    ● Finnhub partial

Dot color: ok=up-strong, partial=warn-strong, missing=down-strong, error=down-strong + pulse
```

The status dot is 6px. Text is `text-text-2` for ok, `text-warn-strong` for partial, `text-down-strong` for missing.

---

### 10.13 `<TradePlan>`

**Purpose**: Display entry/stop/target levels for a single setup. Replaces the 4 colored boxes in current NFLX page.

**File**: `src/components/trade/TradePlan.tsx`

```ts
interface TradePlanProps {
  entry: { primary: number; rationale?: string }
  entryAlt?: { price: number; rationale?: string }
  stop: { price: number; rationale?: string }
  target: { price: number; rationale?: string }
  currentPrice?: number       // for R:R calc
  side: 'long' | 'short'
}
```

**Visual spec** (compact table, not colored boxes):

```
┌─ TRADE PLAN · LONG ──────────────────────────────┐
│ ENTRY   primary   90.00    psych + MA20 support  │
│         alt       98.25    reclaim MA20 on vol   │
│ STOP              91.50    close below 2 days    │
│ TARGET            102.50   MA5 resistance        │
│ R:R               1:1.89                          │
└──────────────────────────────────────────────────┘
```

**Formatting**:
- Labels: `text-label`, `text-text-3`
- Prices: `mono-md`, `text-text-1`
- Rationale: `text-body-sm`, `text-text-2`
- Stop price: `text-down-strong` (for long) / `text-up-strong` (for short)
- Target price: `text-up-strong` (for long) / `text-down-strong` (for short)
- R:R value: `mono-md`, `text-text-1`, color-coded if > 1:2 (up-strong) or < 1:1 (warn-strong)

No colored card backgrounds. No emojis. No "理想买入点 / 次优买入点" — just "Entry primary / alt" in English. (Chinese labels used only in content; UI labels stay English for typographic consistency.)

---

### 10.14 `<NewsList>`

**Purpose**: Stacked news/article rows. Replaces current news cards.

**File**: `src/components/content/NewsList.tsx`

```ts
interface NewsItem {
  id: string
  title: string
  source: string
  excerpt?: string
  url: string
  publishedAt: Date
  tickers?: string[]
}
interface NewsListProps {
  items: NewsItem[]
  onItemClick?: (item: NewsItem) => void
}
```

**Visual spec** (rows, not cards):

```
Row (py-3, border-b border-subtle, hover:bg-bg-1):
  ┌────────────────────────────────────────┐
  │ Title · text-body · text-text-1        │
  │ excerpt line 1, line-clamp-2,          │
  │ text-body-sm, text-text-2              │
  │                                        │
  │ SOURCE · 12h ago · NFLX           ↗    │
  │ caption, text-text-3                   │
  └────────────────────────────────────────┘
```

The arrow icon (`ExternalLink` from lucide, 12px) replaces the current "跳转 ↗" button. Click the entire row, not just the arrow.

---

### 10.15 `<NavSidebar>`

**Purpose**: Left nav for top-level sections.

**File**: `src/components/layout/NavSidebar.tsx`

```ts
interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  badge?: number | string
}
```

**Visual spec** (collapsed-rail default, 56px wide):

```
Width:      56px (collapsed) — icon only with tooltip on hover
Width:      200px (expanded, if user toggles)
Background: bg-1
Border:     border-r border-subtle
Padding:    py-3

Item (h-9, collapsed = w-9, mx-auto):
  Icon:     16px, text-text-2
  Label:    text-body, hidden when collapsed, shown when expanded
  Hover:    bg-bg-2, icon text-text-1
  Active:   bg-bg-3, icon text-accent, 2px left border accent
```

**Default state**: collapsed. Power users memorize icons faster than they read labels.

**Icon mapping** (lucide-react):
- Home / Regime → `Gauge`
- Stocks / Watchlist → `CandlestickChart`
- Portfolio → `Briefcase`
- Journal → `NotebookPen`
- Backtest → `Rewind`
- Settings → `Settings`
- Theme → remove this item. App is dark-only.

**Remove the "DSA" logo tile at top of current sidebar.** Replace with 16px app icon or nothing. User knows what app they're in.

---

### 10.16 `<TopBar>`

**Purpose**: Top navigation strip with global search, regime quick-view, user menu.

**File**: `src/components/layout/TopBar.tsx`

```ts
interface TopBarProps {
  regimeScore?: number
  regimeState?: 'aggressive' | 'standard' | 'cautious' | 'no_trade'
  onSearchOpen: () => void     // opens CommandMenu
}
```

**Visual spec** (h-12, full-width, bg-0 border-b border-subtle):

```
┌──────────────────────────────────────────────────────────────┐
│  [search ⌘K]                         Regime +38 CAUTIOUS  ⚙  │
└──────────────────────────────────────────────────────────────┘
```

- Search input on left: 320px wide, opens `<CommandMenu>` on click or `⌘K` keypress
- Right side: regime mini-indicator (clickable, takes to Regime page), user/settings icon
- No logo, no product name, no nav tabs (those are in sidebar)

---

### 10.17 `<CommandMenu>` (⌘K)

**Purpose**: Global action launcher. The Linear signature feature.

**File**: `src/components/layout/CommandMenu.tsx` (use `cmdk` library)

```ts
interface CommandMenuProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}
```

**Commands the menu supports** (minimum viable set):
- Navigate to stock: "nflx", "nvda", etc. → go to detail page
- Go to page: "regime", "watchlist", "journal", etc.
- Run action: "analyze AAPL", "refresh regime", "export report"
- Recent: last 5 stocks viewed

**Visual spec**:
```
Dialog: centered, max-w-lg, bg-2, border border-default, shadow-md, rounded-md
Input:  h-12, px-4, bg-transparent, text-body, border-b border-subtle
Items:  px-4, py-2, text-body, hover:bg-bg-3
  Icon:  16px, text-text-2
  Label: text-text-1
  Meta:  ml-auto, text-caption, text-text-3
```

Keyboard-only navigation. No mouse targets smaller than 24px tall.

---

### 10.18 `<EmptyState>`

**File**: `src/components/ui/EmptyState.tsx`

```ts
interface EmptyStateProps {
  icon?: LucideIcon
  title: string
  description?: string
  action?: { label: string; onClick: () => void }
  size?: 'sm' | 'md'          // sm = inline, md = full-panel
}
```

**Visual spec**:
- Centered, padding `py-12` (md) / `py-6` (sm)
- Icon: `text-4`, 32px (md) / 20px (sm)
- Title: `text-h3`, `text-text-2`
- Description: `text-body-sm`, `text-text-3`, max-w-xs
- Action: `<Button variant="secondary" size="sm">`

No illustrations. No "oops!" or "nothing here yet 😊" copy. Professional and direct: "No breakout signals in scope."

---

### 10.19 `<Skeleton>`

**Purpose**: Loading placeholder that preserves layout.

**File**: `src/components/ui/Skeleton.tsx`

```ts
interface SkeletonProps {
  width?: number | string
  height?: number | string
  variant?: 'text' | 'rect' | 'circle'
  shimmer?: boolean     // default true
}
```

**Visual spec**:
- Background: `bg-2`
- Shimmer: 2s linear gradient sweep, `--accent-subtle-bg` overlay
- No spinners. Skeletons only.

---

### 10.20 `<Toast>` / `<Toaster>`

**File**: `src/components/ui/Toast.tsx` (wrap `sonner` library)

```ts
// usage
toast.success('Analysis complete')
toast.error('Failed to fetch data', { description: 'yfinance returned 429' })
toast.info('New regime score: +42')
```

**Visual spec**:
- Position: bottom-right, offset 24px
- Background: `bg-2`, border `border-default`, shadow-md
- Width: 320px
- Icons: `CheckCircle2` (success, up-strong), `XCircle` (error, down-strong), `Info` (info, accent), `AlertTriangle` (warn, warn-strong)
- Duration: 3s success, 6s error, 4s default

---

## 11. Page Blueprints

### 11.1 Information architecture

```
/                       → redirect /regime
/regime                 → Regime page (home, tactical view)
/watchlist              → Watchlist page (all positions + monitored)
/stocks/:ticker         → Stock detail page
/journal                → Trade journal (existing)
/journal/:id            → Single journal entry
/backtest               → Backtest (existing)
/backtest/:id           → Backtest detail
/settings               → Settings

Removed from current sidebar:
- 首页 (redundant with /regime)
- 问股 (consolidated into CommandMenu — "ask" is a ⌘K action)
- 主题 (dark-only; no theme toggle)
```

Total top-level routes: 5 (Regime, Watchlist, Stocks, Journal, Backtest). Plus Settings.

---

### 11.2 Regime page (`/regime`)

**Purpose**: "What is the market telling me right now? Should I be pressing or pulling back?"

**Layout** (1440px viewport):

```
┌────┬──────────────────────────────────────────────────────────────────────┐
│    │  ┌─ TopBar (h-12) ─────────────────────────────────────────────────┐ │
│    │  │  [⌘K search]                        Regime +38 CAUTIOUS   ⚙     │ │
│    │  └──────────────────────────────────────────────────────────────────┘ │
│    │                                                                       │
│ N  │  ┌─────────────────────┬────────────────────────────────────────────┐│
│ a  │  │ <RegimeScore>       │ <ContributionList>                         ││
│ v  │  │                     │                                            ││
│    │  │ +38 CAUTIOUS        │ Direction    +17   ████████████            ││
│ (56│  │ Half size; retests  │ Sector       +11   ████████                ││
│  px│  │ [sparkline 60d]     │ Volatility   +10   ███████                 ││
│  )│  │ v1 · 09:10 ET       │ Macro         0                            ││
│    │  │                     │ Prev Day      0                            ││
│    │  │                     │ Premarket     0                            ││
│    │  └─────────────────────┴────────────────────────────────────────────┘│
│    │                                                                       │
│    │  <StatBar>                                                            │
│    │  ┌──────────────────────────────────────────────────────────────────┐│
│    │  │ SPY 708.72 -0.12% │ VIX 18.87 -2.30% │ Breadth 62% │ Premkt +0.08││
│    │  └──────────────────────────────────────────────────────────────────┘│
│    │                                                                       │
│    │  <DataSourceStatus variant="bar" />                                   │
│    │  ● yfinance partial    ● Alpaca missing    ● Finnhub partial         │
│    │                                                                       │
│    │  ┌─ WATCHLIST PREMARKET ────────────────────────────── 15 of 15 ─────│
│    │  │  (sort ▼ by Chg%)                                                 ││
│    │  │  TICKER  LAST     CHG%     PMKT    VOL/AVG  MA3/5/13  NEXT       ││
│    │  │  ─────────────────────────────────────────────────────────       ││
│    │  │  NVDA    138.45   +1.82%   -0.05%  2.1x     ↑↑→      Earn 8d    ││
│    │  │  AAPL    225.31   -0.12%   +0.00%  1.2x     ↑↑↑      Earn 12d   ││
│    │  │  META    604.22   -0.44%   -0.08%  0.9x     →↑↑      Earn 5d    ││
│    │  │  ... (15 rows total, each 36px = ~540px region)                   ││
│    │  └──────────────────────────────────────────────────────────────────┘│
│    │                                                                       │
│    │  ┌─ REGIME HISTORY ─────────────────────── 30d · 60d · 90d ──────────│
│    │  │  [line chart, height 200px, accent color, no fill]                ││
│    │  └──────────────────────────────────────────────────────────────────┘│
│    │                                                                       │
│    │  ┌─ BREAKOUT SIGNALS ───────────────────── All · Fake · Real ───────│
│    │  │  [empty state: "No breakout signals in scope."]                  ││
│    │  └──────────────────────────────────────────────────────────────────┘│
│    │                                                                       │
└────┴──────────────────────────────────────────────────────────────────────┘
```

**Section order rationale**:
1. **RegimeScore + ContributionList** (side by side) — the "what's the call" answer, first thing user needs.
2. **StatBar** — ambient market context, one line.
3. **DataSourceStatus** — system health, one line.
4. **Watchlist Premarket table** — the actionable data. Largest region on page.
5. **Regime History chart** — context for the score (is regime improving or deteriorating?).
6. **Breakout Signals** — event log of significant setups detected.

**Components removed from current design**:
- Radar chart (replaced by ContributionList alone)
- Sentiment gauge (was never in this page; noted for stock detail)
- 6 small cards of market data (consolidated into StatBar)
- Emoji headings, gradient backgrounds

**Interactions**:
- Click row in watchlist → navigate to `/stocks/:ticker`
- Hover contribution item → show tooltip with calculation breakdown
- Click "Recompute" in top right corner (header-level action) → refresh regime
- `⌘K` anywhere → open command menu

---

### 11.3 Watchlist page (`/watchlist`)

**Purpose**: "Show me everything I'm tracking, with filters and columns I choose."

This is a denser variant of the Regime page's watchlist table — full screen, more columns, sortable, filterable.

**Layout**:

```
┌────┬──────────────────────────────────────────────────────────────────────┐
│    │  Watchlist         [+ Add ticker]   [Columns ▼]   [Filter: All ▼]    │
│    │  ──────────────────────────────────────────────────────────────────  │
│    │                                                                       │
│    │  [DataTable, ~20 columns, stickyHeader, density="compact"]            │
│    │                                                                       │
│    │  TICKER  LAST    CHG%   CHG$   VOL   AVG VOL  VOL/AVG  MA3 MA5 MA13 │
│    │  VWAP   HI52    LO52   MKT CAP  P/E   BETA   NEXT     SIGNAL  ...    │
│    │                                                                       │
│    │  (30+ rows, each 28px — up to 15 fits without scroll on 900px screen)│
└────┴──────────────────────────────────────────────────────────────────────┘
```

**Features**:
- Column picker (show/hide, reorder)
- Filter: by sector, by signal, by price change range
- Sort: click header to sort, shift-click to multi-sort
- Export: CSV download in toolbar
- Inline sparkline in last column (30-day)

**This page replaces the "持仓" in current nav.** Portfolio becomes a filter (`Filter: Held only`) within watchlist.

---

### 11.4 Stock detail page (`/stocks/:ticker`)

**Purpose**: Complete view of one ticker — chart, plan, analysis, news.

**Layout** (1440px):

```
┌────┬──────────────────────────────────────────────────────────────────────┐
│    │  ┌─ HEADER ───────────────────────────────────────────────────────┐ │
│    │  │ NASDAQ                                               [Analyze] │ │
│    │  │ NFLX  Netflix, Inc.                                  [Add ⋯]   │ │
│    │  │                                                                │ │
│    │  │ 93.82  −3.58%  −3.48    04/20 12:23 ET · After Hours           │ │
│    │  │ Hi 97.40  Lo 92.11  O 96.80  Vol 12.4M  (0.9x avg)             │ │
│    │  └────────────────────────────────────────────────────────────────┘ │
│    │                                                                       │
│    │  ┌─ CHART ──────────────────────── 1D · 4H · 1H · 15m · 5m ────────┐│
│    │  │                                                                  ││
│    │  │  [TradingView Lightweight Charts, 500px height]                 ││
│    │  │  Candlesticks + MA3/5/13 overlay + volume subpanel              ││
│    │  │                                                                  ││
│    │  └──────────────────────────────────────────────────────────────────┘│
│    │                                                                       │
│    │  ┌─ TRADE PLAN ─────────────────┐ ┌─ ANALYSIS ──────────────────────┐│
│    │  │ <TradePlan>                   │ │ AI summary (from current engine)││
│    │  │                               │ │                                  ││
│    │  │ ENTRY   primary   90.00       │ │ Core view: NFLX broke below      ││
│    │  │         alt       98.25       │ │ MA20 today. MA slopes bearish... ││
│    │  │ STOP              91.50       │ │                                  ││
│    │  │ TARGET           102.50       │ │ Stance: NEUTRAL · Confidence 48  ││
│    │  │ R:R               1:1.89      │ │                                  ││
│    │  └───────────────────────────────┘ └──────────────────────────────────┘│
│    │                                                                       │
│    │  ┌─ TABS ────────────────────────────────────────────────────────────┐│
│    │  │ [News 12] [Events 3] [Analysis History 7] [Trace]                 ││
│    │  └───────────────────────────────────────────────────────────────────┘│
│    │                                                                       │
│    │  [Tab content area, 400-600px height, scrollable]                     │
│    │                                                                       │
└────┴──────────────────────────────────────────────────────────────────────┘
```

**Components removed from current NFLX page**:
- Left-side history list (moved to a dedicated `/journal` page or `/stocks?history=true`)
- Sentiment gauge (consolidated into header price row or removed entirely — it's a single number, not a visual)
- "刷新" button next to News (moved to tab header, smaller)

**Components added**:
- Full chart (TradingView Lightweight Charts, Section 12.1)
- Header summary row (OHLV + VWAP)
- Tab navigation for secondary content

---

### 11.5 Journal page (`/journal`)

Out of scope for v1.0 UI refresh. Existing UI acceptable; revisit in v1.1.

### 11.6 Backtest page (`/backtest`)

Out of scope for v1.0. Existing UI acceptable; revisit in v1.1.

### 11.7 Settings page (`/settings`)

Out of scope for v1.0. Existing UI acceptable; revisit in v1.1.

---

## 12. Charts

### 12.1 Candlestick / OHLCV chart

**Library**: [TradingView Lightweight Charts](https://tradingview.github.io/lightweight-charts/) (43KB gzip)

**Why switch from Recharts**: Recharts cannot render proper candlesticks without a custom shape component, and even then performance degrades at 500+ candles. Lightweight Charts is purpose-built, handles pan/zoom natively, and is the de facto standard for financial UIs. The `PROJECT_VISION.md` ADR-002 listed this as an upgrade trigger; it is now time.

**File**: `src/components/charts/CandlestickChart.tsx`

```ts
interface CandlestickChartProps {
  data: Array<{ time: number; open: number; high: number; low: number; close: number; volume: number }>
  overlays?: Array<{ type: 'ma'; period: number; color: string }>
  height?: number
  interval?: '1d' | '4h' | '1h' | '15m' | '5m'
  onIntervalChange?: (i: string) => void
}
```

**Visual spec**:
- Height: 500px default
- Candle up: `--up-strong` body + wick
- Candle down: `--down-strong` body + wick (hollow style — empty body, colored wick)
- Volume bars below: 1/4 chart height, same up/down colors at 40% opacity
- MA overlays: MA3 `--chart-4` (amber), MA5 `--accent` (violet), MA13 `--chart-5` (cyan) — single hue each, 1px stroke
- Background: `--bg-1`
- Gridlines: `--border-subtle`, dashed, sparse (every 5 bars vertical, every 5 price levels horizontal)
- Crosshair: `--text-3`, dashed 1px
- Labels: `--font-mono`, `--text-mono-sm`, `--text-2`

**No**: chart animations on data load, rounded candle caps, gradient fills, shadow under candles.

---

### 12.2 Sparkline

See Section 10.7. SVG-only, no library.

---

### 12.3 Contribution bars

See Section 10.11. Plain divs with percentage width, no library.

---

### 12.4 Regime history line chart

**Library**: Recharts (already in project) or a custom `<svg>`. Prefer custom for control.

**File**: `src/components/charts/RegimeHistoryChart.tsx`

```ts
interface RegimeHistoryProps {
  data: Array<{ date: string; score: number; state: 'aggressive'|'standard'|'cautious'|'no_trade' }>
  height?: number
  range?: '30d' | '60d' | '90d'
}
```

**Visual spec**:
- Height: 200px
- Line: `--accent`, 1.5px stroke
- Fill under line: `--accent` at 4% opacity, from line down to zero
- Zero line: `--border-default` dashed
- Thresholds (horizontal dashed lines): +50 (aggressive), +20 (cautious-to-standard), -20 (standard-to-cautious), -50 (no_trade). Labels at right edge, `--text-3`, `--text-label`.
- X-axis: date labels every 5-10 points, `--text-caption`, `--text-3`. No axis line.
- Y-axis: labels at threshold positions only. No axis line.
- Dots: only on hover (crosshair), 3px radius, `--accent`

Below the chart, a row of tiny colored dots (5×5px each) for each day, colored by regime state — lets you scan state durations even when the line blurs together.

---

## 13. Interaction Patterns

### 13.1 Keyboard shortcuts (minimum viable set)

| Keys | Action |
|---|---|
| `⌘K` / `Ctrl+K` | Open command menu |
| `g r` | Go to Regime |
| `g w` | Go to Watchlist |
| `g j` | Go to Journal |
| `g b` | Go to Backtest |
| `j` / `k` | Next/prev row in tables |
| `Enter` | Open selected row |
| `/` | Focus search input |
| `?` | Show keyboard shortcut reference (modal) |
| `Esc` | Close modal/menu |

Use `kbd` library or `react-hotkeys-hook`.

### 13.2 Hover states

Every interactive element has a hover state. Rules:
- Table row hover: `bg-2`
- Button hover: as specified per variant
- Link hover: underline via `text-underline-offset-2` — never color change alone (accessibility)
- No hover animations on non-interactive elements (e.g., don't animate cards that aren't clickable)

### 13.3 Focus states

Focus ring is `outline: 2px solid var(--accent)` with 2px offset. Applies via `:focus-visible` only — not on mouse clicks.

### 13.4 Loading states

1. **First load**: skeleton components matching final layout (never spinner)
2. **Refresh**: subtle opacity dim (0.6) on refreshing region + inline spinner in top-right corner of that region
3. **Infinite action** (background job): top-bar progress indicator (2px bar, `--accent`, animated)

Never use a full-page spinner. Never block UI with a modal loader.

### 13.5 Error handling

Inline errors (within a component):
```
┌─────────────────────────────────────────────┐
│ ⚠ Failed to load watchlist data             │
│ yfinance returned 429. Try again in 5 min.  │
│ [Retry]                                      │
└─────────────────────────────────────────────┘
```

- Background: `--down-subtle`
- Border-left: 2px `--down-strong`
- Icon: `AlertTriangle`, `--down-strong`
- Button: ghost variant

Page-level errors (fatal): centered EmptyState with `--down-strong` icon.

### 13.6 Empty states

See `<EmptyState>` component (10.18). Professional copy, no emoji, no "Oops!"

---

## 14. File Structure

```
apps/dsa-web/
├── src/
│   ├── styles/
│   │   ├── tokens.css            # from Section 8
│   │   └── globals.css           # resets + base styles
│   ├── components/
│   │   ├── ui/                   # primitives, no business logic
│   │   │   ├── Button.tsx
│   │   │   ├── IconButton.tsx
│   │   │   ├── Input.tsx
│   │   │   ├── Tabs.tsx
│   │   │   ├── Badge.tsx
│   │   │   ├── DataTable.tsx
│   │   │   ├── EmptyState.tsx
│   │   │   ├── Skeleton.tsx
│   │   │   └── Toast.tsx
│   │   ├── data/                 # display primitives for financial data
│   │   │   ├── PriceCell.tsx
│   │   │   ├── ChangeCell.tsx
│   │   │   ├── Sparkline.tsx
│   │   │   ├── StatBar.tsx
│   │   │   └── MASlopeCell.tsx
│   │   ├── regime/
│   │   │   ├── RegimeScore.tsx
│   │   │   └── ContributionList.tsx
│   │   ├── trade/
│   │   │   └── TradePlan.tsx
│   │   ├── content/
│   │   │   └── NewsList.tsx
│   │   ├── system/
│   │   │   └── DataSourceStatus.tsx
│   │   ├── charts/
│   │   │   ├── CandlestickChart.tsx
│   │   │   └── RegimeHistoryChart.tsx
│   │   └── layout/
│   │       ├── NavSidebar.tsx
│   │       ├── TopBar.tsx
│   │       └── CommandMenu.tsx
│   ├── pages/
│   │   ├── RegimePage.tsx
│   │   ├── WatchlistPage.tsx
│   │   ├── StockDetailPage.tsx
│   │   └── ...
│   └── main.tsx
├── public/
│   └── fonts/
│       ├── Geist-Regular.woff2
│       ├── Geist-Medium.woff2
│       ├── Geist-SemiBold.woff2
│       ├── GeistMono-Regular.woff2
│       └── GeistMono-Medium.woff2
├── tailwind.config.ts            # from Section 9
└── package.json
```

**Package additions required**:
```json
{
  "@tanstack/react-table": "^8",       // for DataTable
  "lightweight-charts": "^4",          // for CandlestickChart
  "cmdk": "^1",                         // for CommandMenu
  "sonner": "^1",                       // for Toast
  "lucide-react": "^0.383"              // already present — used for all icons
}
```

**Package removals**:
- Remove any emoji-based icon libraries
- Keep recharts (still used for RegimeHistoryChart)

---

## 15. Implementation Roadmap

Execute in this order. Each step is independently testable.

### Step 1 — Foundation (0.5 day)
- [ ] Add Geist Sans + Geist Mono to `public/fonts/`, wire up `@font-face` in `globals.css`
- [ ] Create `tokens.css` (from Section 8)
- [ ] Replace `tailwind.config.ts` (from Section 9)
- [ ] Remove existing theme toggle code — app is dark-only
- [ ] Delete any `shadow-xl`, `rounded-xl`, `rounded-2xl` uses from existing code (will re-style these in later steps)

**Verification**: `npm run dev` loads with correct fonts and background color. No console errors.

### Step 2 — Primitives (1 day)
Build in this order (each depends on the previous):
- [ ] `Button`, `IconButton`
- [ ] `Input`
- [ ] `Badge`
- [ ] `Tabs`
- [ ] `EmptyState`, `Skeleton`, `Toast`
- [ ] `DataTable` (wrap `@tanstack/react-table`)

**Verification**: Create a throwaway page `/design-lab` that renders one of each component. Review visually against specs.

### Step 3 — Data primitives (0.5 day)
- [ ] `PriceCell`, `ChangeCell`
- [ ] `Sparkline`
- [ ] `StatBar`
- [ ] `MASlopeCell` (renders ↑↑→ for MA3/5/13 slopes)

### Step 4 — Layout (0.5 day)
- [ ] `NavSidebar` (collapsed-rail default, 56px)
- [ ] `TopBar` with regime mini-indicator
- [ ] `CommandMenu` (⌘K)

**Verification**: App shell renders correctly. `⌘K` opens the menu. Navigation works.

### Step 5 — Regime page (1 day)
- [ ] `RegimeScore`
- [ ] `ContributionList`
- [ ] `DataSourceStatus`
- [ ] Assemble `RegimePage.tsx` per Section 11.2 blueprint
- [ ] Wire up existing APIs (no backend changes)
- [ ] **Delete** the old Regime page code once parity is confirmed

**Verification**: Regime page matches wireframe. Still-functional using existing backend.

### Step 6 — Watchlist page (0.5 day)
- [ ] `WatchlistPage.tsx` per Section 11.3
- [ ] Column picker component (simple dropdown)
- [ ] CSV export utility

### Step 7 — Stock detail (1 day)
- [ ] Install `lightweight-charts`
- [ ] `CandlestickChart` wrapper
- [ ] `TradePlan` component
- [ ] `NewsList` component
- [ ] Assemble `StockDetailPage.tsx` per Section 11.4

**Verification**: NFLX page renders all sections. Chart loads with MA3/5/13 overlays.

### Step 8 — Cleanup (0.5 day)
- [ ] Remove Journal / Backtest / Settings pages from UI refresh scope — leave as-is for v1.1
- [ ] Audit for any `emoji` / `rounded-xl` / `shadow-xl` holdouts — grep and fix
- [ ] Verify `prefers-reduced-motion` respected
- [ ] Run Lighthouse — confirm Contrast AA passes

**Total estimate**: ~5 days of focused work for v1.0 UI refresh.

---

## 16. Before/After Gallery

### 16.1 Market snapshot region

**Before** (current Image 1):
6 cards in a grid, each with border + padding + rounded-xl, each containing a label + 1-2 values. ~180px tall region.

**After**:
Single `<StatBar>` horizontal strip. 48px tall. Same information.

```tsx
// Before
<div className="grid grid-cols-3 gap-4 p-6 bg-gray-900/50 rounded-xl">
  <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 shadow-lg">
    <div className="text-xs text-gray-400">SPY close</div>
    <div className="text-2xl font-bold mt-2">$708.72</div>
    <div className="text-xs text-gray-500 mt-1">MA20 $669.62 · MA50 $674.54</div>
    <div className="text-xs mt-1">5d —%</div>
  </div>
  {/* 5 more cards */}
</div>

// After
<StatBar items={[
  { label: 'SPY',     value: '708.72', delta: '−0.12%', sub: 'MA20 669.62' },
  { label: 'VIX',     value: '18.87',  delta: '−2.30%', sub: '5d −4.1%' },
  { label: 'Breadth', value: '62%',    sub: '>MA20' },
  { label: 'Premkt',  value: '+0.08%', sub: 'SPY fut' },
  { label: 'Updated', value: '09:10',  sub: 'v1 · ET' },
]} />
```

Vertical space saved: 132px (73% reduction).

### 16.2 Regime score region

**Before**: Circular arc gauge, 250×250px, showing "+38 CAUTIOUS" with an orange meter.
**After**: `<RegimeScore>` component, 180×90px, same info + 60-day sparkline.

Pixel savings: ~42,000 pixels reclaimed.

### 16.3 Dimension attribution

**Before**: Radar (hexagonal chart) 300×300px + bar chart on right 200×200px. Two views of same data.
**After**: Single `<ContributionList>` 300×160px.

### 16.4 Watchlist premarket

**Before**: 15 tile cards in a 5×3 grid. Each tile shows ticker + change%. ~400×200px region, only 30 data points displayed.
**After**: `<DataTable>` with 15 rows × 8 columns = 120 data points in same height.

Data density: 4x.

### 16.5 Stock detail page

**Before**: 3-column layout with history list, stock info, sentiment gauge. Trade plan as colored tiles. ~2000px vertical scroll.
**After**: Full-width chart-first layout. Trade plan as table. Tabs for secondary content. ~1200px scroll.

---

## Appendix A: Icon Inventory

Exact `lucide-react` icons used. Buying into this set keeps visual consistency.

| Concept | Icon |
|---|---|
| Home / Regime | `Gauge` |
| Watchlist / Stocks | `CandlestickChart` |
| Portfolio | `Briefcase` |
| Journal | `NotebookPen` |
| Backtest | `Rewind` |
| Settings | `Settings` |
| Search | `Search` |
| Refresh / Recompute | `RefreshCw` |
| Play / Analyze | `Play` |
| Pause | `Pause` |
| Add / New | `Plus` |
| Remove / Close | `X` |
| Expand / Dropdown | `ChevronDown` |
| Previous / Next | `ChevronLeft` / `ChevronRight` |
| Up / Down (sort) | `ArrowUp` / `ArrowDown` |
| Up trend | `TrendingUp` |
| Down trend | `TrendingDown` |
| Flat | `Minus` |
| External link | `ExternalLink` |
| Copy | `Copy` |
| More actions | `MoreHorizontal` |
| Filter | `SlidersHorizontal` |
| Columns | `Columns3` |
| Export | `Download` |
| Import | `Upload` |
| Check (success) | `CheckCircle2` |
| Error | `XCircle` |
| Info | `Info` |
| Warning | `AlertTriangle` |
| Lightbulb / Idea | `Lightbulb` |
| Target | `Target` |
| Flag | `Flag` |
| Bell / Notification | `Bell` |
| User | `User` |
| Keyboard shortcut | `Command` |
| Loading | use custom inline spinner (16px, accent color, 720ms rotate) |

**Default icon size**: 14px in buttons, 16px in navs, 12px inline with text. Stroke width 1.5.

**Never mix lucide with other icon libraries.**

---

## Appendix B: Copywriting Rules

The text in the UI is part of the design. Rules:

1. **Language**: English for UI chrome (labels, buttons, errors). Chinese acceptable in user-generated content (analysis, journal). Never mix in the same sentence.
2. **Case**: Sentence case for buttons ("Run analysis"), uppercase for labels ("LAST · CHG%"), title case for tab names.
3. **Numbers**:
   - Prices: 2 decimals for USD, tabular ("708.72" not "$708.72" in tables — currency column separate)
   - Percentages: 2 decimals, always prefix with sign ("+1.82%" / "−0.12%", true minus sign U+2212)
   - Counts: no decimals for integers ("15 rows")
   - Large numbers: abbreviate ("12.4M", "1.2B") but only in space-constrained contexts
4. **Time**:
   - Display format: "12:23 ET" (24h in user locale, suffix ET for market time)
   - Relative: "2h ago", "3d ago", "2 weeks ago"
   - Never "just now" — say "< 1m ago"
5. **Tone**:
   - Declarative: "No breakout signals in scope." not "Oops, nothing here!"
   - No exclamation points
   - No emoji
   - No humor — this is a trading tool
6. **Error messages**: state the problem, then state the cause, then state the action.
   - ✅ "Failed to load data. yfinance returned 429 (rate-limited). Try again in 5 minutes."
   - ❌ "Oops, something went wrong. Please try again."
7. **Action verbs**: use direct imperatives.
   - ✅ "Run analysis", "Export CSV", "Add ticker"
   - ❌ "Click here to run", "You can export", "Add a new ticker"

---

## Closing

This document is a contract between the design intent and the implementation. If code deviates from what is written here, either the code is wrong or this document is. In both cases, resolve by updating this document first, then updating code.

If a request cannot be fulfilled with tokens and components from this spec, the request is asking for something outside the design system. Either (a) the spec is incomplete and should be extended, or (b) the request is outside the product's scope. Do not silently add off-spec components.

— Maintainer: [@Cthloveross](https://github.com/Cthloveross) · 2026-04-20

**Next revision trigger**: complete Steps 1–8 of implementation roadmap, then review against real usage. Expect v1.1 within 2 weeks.