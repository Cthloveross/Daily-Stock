// apps/dsa-web/tailwind.config.ts
// Design_system.md v1.0 tokens live alongside the legacy config.
// New primitives (components/ui/, components/data/) MUST use the design-system tokens:
//   bg-bg-0..3 · text-text-1..4 · text-accent · up-strong · down-strong · warn-strong
//   font-mono · tabular-nums · text-body · text-label · text-mono-md · rounded-md
// Legacy aliases (cyan, purple, emerald utilities, rounded-xl) are retained ONLY until
// Step 8 cleanup removes them. Do NOT add new components that depend on them.
import type { Config } from 'tailwindcss'

export default {
  darkMode: ['class'],
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: {
        '2xl': '1400px',
      },
    },
    extend: {
      colors: {
        // ---------- Design_system.md v1.0 tokens (strict, use in new code) ----------
        bg: {
          0: 'var(--bg-0)',
          1: 'var(--bg-1)',
          2: 'var(--bg-2)',
          3: 'var(--bg-3)',
        },
        text: {
          1: 'var(--text-1)',
          2: 'var(--text-2)',
          3: 'var(--text-3)',
          4: 'var(--text-4)',
        },
        up: {
          strong: 'var(--up-strong)',
          muted: 'var(--up-muted)',
          subtle: 'var(--up-subtle)',
        },
        down: {
          strong: 'var(--down-strong)',
          muted: 'var(--down-muted)',
          subtle: 'var(--down-subtle)',
        },
        warn: {
          strong: 'var(--warn-strong)',
          muted: 'var(--warn-muted)',
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
        // ---------- Legacy (deprecated; removed in Step 8) ----------
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        // Design_system.md §4.4 accent; legacy card/popover/accent overloaded
        accent: {
          // new tokens first — take precedence because they are stable named entries
          DEFAULT: 'var(--accent)',
          hover: 'var(--accent-hover)',
          active: 'var(--accent-active)',
          'subtle-bg': 'var(--accent-subtle-bg)',
          'subtle-border': 'var(--accent-subtle-border)',
          // legacy
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        cyan: {
          DEFAULT: 'hsl(var(--primary))',
          dim: 'hsl(var(--primary) / 0.8)',
          glow: 'hsl(var(--primary) / 0.4)',
        },
        purple: {
          DEFAULT: 'hsl(var(--accent-legacy, var(--accent)))',
          dim: 'hsl(var(--accent-legacy, var(--accent)) / 0.8)',
          glow: 'hsl(var(--accent-legacy, var(--accent)) / 0.3)',
        },
        success: {
          DEFAULT: 'hsl(var(--success))',
          dim: 'hsl(var(--success) / 0.8)',
          glow: 'hsl(var(--success) / 0.3)',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          dim: 'hsl(var(--warning) / 0.8)',
          glow: 'hsl(var(--warning) / 0.3)',
        },
        danger: {
          DEFAULT: 'hsl(var(--destructive))',
          dim: 'hsl(var(--destructive) / 0.8)',
          glow: 'hsl(var(--destructive) / 0.3)',
        },
        base: 'hsl(var(--background))',
        elevated: 'hsl(var(--elevated))',
        hover: 'hsl(var(--hover))',
        'secondary-bg': 'hsl(var(--secondary))',
        'muted-bg': 'hsl(var(--muted))',
        'secondary-text': 'hsl(var(--secondary-text))',
        'muted-text': 'hsl(var(--muted-text))',
        dim: 'hsl(var(--border-dim-raw) / 0.06)',
        subtle: 'hsl(var(--bg-subtle-raw) / 0.05)',
        'subtle-hover': 'hsl(var(--bg-subtle-raw) / 0.1)',
        'subtle-soft': 'hsl(var(--bg-subtle-raw) / 0.03)',
        'subtle-active': 'hsl(var(--bg-subtle-raw) / 0.15)',
        'surface-1': 'var(--surface-1)',
        'surface-2': 'var(--surface-2)',
        'surface-3': 'var(--surface-3)',
        'overlay-hover': 'var(--overlay-hover)',
        'overlay-selected': 'var(--overlay-selected)',
      },
      borderColor: {
        // Design_system.md v1.0
        DEFAULT: 'var(--border-default)',
        subtle: 'var(--border-subtle)',
        strong: 'var(--border-strong)',
        // legacy aliases retained for existing components
        dim: 'hsl(var(--border-dim-raw) / 0.06)',
        'subtle-hover': 'hsl(var(--border-subtle-raw) / 0.15)',
      },
      backgroundColor: {
        subtle: 'hsl(var(--bg-subtle-raw) / 0.05)',
        'subtle-hover': 'hsl(var(--bg-subtle-raw) / 0.1)',
        'subtle-soft': 'hsl(var(--bg-subtle-raw) / 0.03)',
        'subtle-active': 'hsl(var(--bg-subtle-raw) / 0.15)',
      },
      backgroundImage: {
        'gradient-purple-cyan': 'linear-gradient(135deg, hsla(var(--accent), 0.2) 0%, hsla(var(--primary), 0.1) 100%)',
        'gradient-card-border': 'linear-gradient(180deg, hsla(var(--accent), 0.4) 0%, hsla(var(--accent), 0.1) 50%, hsla(var(--primary), 0.2) 100%)',
        'gradient-cyan': 'linear-gradient(135deg, hsl(var(--primary)) 0%, hsl(var(--primary) / 0.8) 100%)',
        'primary-gradient': 'linear-gradient(135deg, #00d4ff 0%, #00a8cc 100%)',
      },
      boxShadow: {
        // Design_system.md v1.0
        'ds-sm': 'var(--shadow-sm)',
        'ds-md': 'var(--shadow-md)',
        // legacy
        'soft-card': 'var(--shadow-soft-card)',
        'soft-card-strong': 'var(--shadow-soft-card-strong)',
        'glow-cyan': '0 0 20px rgba(0, 212, 255, 0.4)',
        'glow-purple': '0 0 20px rgba(168, 85, 247, 0.3)',
        'glow-success': '0 0 20px rgba(0, 255, 136, 0.3)',
        'glow-danger': '0 0 20px rgba(255, 68, 102, 0.3)',
        'cyan/20': '0 12px 28px rgba(0, 212, 255, 0.2)',
        'cyan/22': '0 18px 34px rgba(0, 212, 255, 0.22)',
      },
      borderRadius: {
        // Design_system.md v1.0 scale
        'ds-sm': 'var(--radius-sm)',
        'ds-md': 'var(--radius-md)',
        'ds-lg': 'var(--radius-lg)',
        // legacy (will be converted to ds-md/ds-sm in Step 8)
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
        xl: '12px',
        '2xl': '16px',
        '3xl': '20px',
      },
      fontFamily: {
        sans: 'var(--font-sans)',
        mono: 'var(--font-mono)',
      },
      fontSize: {
        // Design_system.md v1.0 semantic sizes
        display: ['var(--text-display)', { lineHeight: 'var(--leading-tight)', fontWeight: '600', letterSpacing: 'var(--tracking-tight)' }],
        h1: ['var(--text-h1)', { lineHeight: 'var(--leading-snug)', fontWeight: '600' }],
        h2: ['var(--text-h2)', { lineHeight: 'var(--leading-snug)', fontWeight: '600' }],
        h3: ['var(--text-h3)', { lineHeight: 'var(--leading-normal)', fontWeight: '600' }],
        body: ['var(--text-body)', { lineHeight: 'var(--leading-relaxed)' }],
        'body-sm': ['var(--text-body-sm)', { lineHeight: 'var(--leading-normal)' }],
        label: ['var(--text-label)', { lineHeight: 'var(--leading-normal)', letterSpacing: 'var(--tracking-label)', fontWeight: '500' }],
        caption: ['var(--text-caption)', { lineHeight: 'var(--leading-normal)' }],
        'mono-lg': ['var(--text-mono-lg)', { lineHeight: 'var(--leading-tight)', fontWeight: '500' }],
        'mono-md': ['var(--text-mono-md)', { lineHeight: 'var(--leading-normal)' }],
        'mono-sm': ['var(--text-mono-sm)', { lineHeight: 'var(--leading-normal)' }],
        'mono-xs': ['var(--text-mono-xs)', { lineHeight: 'var(--leading-snug)' }],
        // legacy
        xxs: '10px',
      },
      spacing: {
        18: '4.5rem',
        22: '5.5rem',
      },
      transitionDuration: {
        fast: 'var(--duration-fast)',
        mid: 'var(--duration-mid)',
        slow: 'var(--duration-slow)',
      },
      transitionTimingFunction: {
        'ds-out': 'var(--ease-out)',
        'ds-in': 'var(--ease-in)',
        'ds-inout': 'var(--ease-inout)',
      },
      zIndex: {
        sticky: 'var(--z-sticky)',
        dropdown: 'var(--z-dropdown)',
        popover: 'var(--z-popover)',
        modal: 'var(--z-modal)',
        toast: 'var(--z-toast)',
        cmdk: 'var(--z-cmdk)',
      },
      animation: {
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.4s ease-out',
        'slide-in-right': 'slideInRight 0.3s ease-out',
        'pulse-glow': 'pulseGlow 2s ease-in-out infinite',
        'spin-slow': 'spin 2s linear infinite',
        'float-in': 'floatIn 0.45s ease-out',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        slideInRight: {
          from: { opacity: '0', transform: 'translateX(100%)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        floatIn: {
          from: { opacity: '0', transform: 'translateY(16px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        pulseGlow: {
          '0%, 100%': { boxShadow: '0 0 20px rgba(0, 212, 255, 0.4)' },
          '50%': { boxShadow: '0 0 40px rgba(0, 212, 255, 0.6)' },
        },
      },
      fontVariantNumeric: {
        'tabular-nums': 'tabular-nums',
      },
    },
  },
  plugins: [],
} satisfies Config
