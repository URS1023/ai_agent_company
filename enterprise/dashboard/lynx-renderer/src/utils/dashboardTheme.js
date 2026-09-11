const FALLBACK_THEME = Object.freeze({
  id: 'spectral-abyss',
  primary: '#77E6FF',
  secondary: '#6C7CFF',
  accent: '#B99AFF',
  danger: '#FF6B83',
  positive: '#77F2BC',
  panel: '#091522E8',
  panelStrong: '#0D1D2DF2',
  grid: '#21364A',
  text: '#F4FAFF',
  muted: '#8296A8',
  background: '#040912',
  ambientPrimary: '#77E6FF24',
  ambientSecondary: '#6C7CFF2E',
  surfaceLift: '#183552',
  backgroundRaised: '#081A2E',
  surfaceRadius: 18,
  palette: ['#77E6FF', '#6C7CFF', '#B99AFF', '#77F2BC', '#FF6B83', '#59C3FF', '#F2A7D8', '#A7B6C8']
})

function explicit(value) {
  return value !== undefined && value !== null && String(value).trim() !== ''
}

export function withAlpha(value, alpha) {
  const color = String(value || '').trim()
  const match = color.match(/^#([0-9a-f]{6})([0-9a-f]{2})?$/i)
  if (!match) return color
  const alphaHex = typeof alpha === 'number'
    ? Math.round(Math.max(0, Math.min(1, alpha)) * 255).toString(16).padStart(2, '0')
    : String(alpha || '').replace(/^#/, '').padStart(2, '0').slice(-2)
  return `#${match[1]}${alphaHex.toLowerCase()}`
}

export function normalizeDashboardTheme(widget = {}) {
  const source = widget.theme && typeof widget.theme === 'object' ? widget.theme : {}
  const primary = source.primary || FALLBACK_THEME.primary
  const secondary = source.secondary || FALLBACK_THEME.secondary
  const accent = source.accent || FALLBACK_THEME.accent
  const danger = source.danger || FALLBACK_THEME.danger
  const positive = source.positive || FALLBACK_THEME.positive
  const sourcePalette = Array.isArray(source.palette) ? source.palette.filter(Boolean) : []
  return {
    ...FALLBACK_THEME,
    ...source,
    id: widget.themeId || source.id || FALLBACK_THEME.id,
    primary,
    secondary,
    accent,
    danger,
    positive,
    palette: sourcePalette.length >= 4
      ? sourcePalette
      : [primary, secondary, accent, positive, danger, '#59C3FF', '#F2A7D8', '#A7B6C8']
  }
}

function surfaceTone(widget, theme) {
  const role = `${widget.visualRole || ''} ${widget.panelVariant || ''}`.toLowerCase()
  if (/alert|exception|risk|danger/.test(role)) return theme.danger
  if (/progress|status|health|success/.test(role)) return theme.positive
  if (/composition|structure|orbit/.test(role)) return theme.accent
  if (/relation|detail|table|stream/.test(role)) return theme.secondary
  const metricIndex = Number(widget.metricIndex)
  if (Number.isFinite(metricIndex) && theme.palette.length) {
    return theme.palette[Math.abs(metricIndex) % Math.min(theme.palette.length, 5)]
  }
  if (/ranking/.test(role)) return theme.accent
  return theme.primary
}

export function shouldShowPanelChrome(widget = {}) {
  if (widget.visualMode === 'ai') {
    const mode = String(widget.chromeMode || '').toLowerCase()
    return Boolean(mode && mode !== 'none')
  }
  return Boolean(widget.theme || widget.themeId || widget.panelVariant)
    && !String(widget.panelVariant || '').startsWith('reference-')
    && !String(widget.panelVariant || '').startsWith('showcase-template-')
}

export function shouldShowTechFrame(widget = {}) {
  if (widget.visualMode === 'ai') {
    const mode = String(widget.frameMode || '').toLowerCase()
    return Boolean(mode && mode !== 'none')
  }
  return Boolean(widget.theme || widget.themeId || widget.panelVariant)
    && !String(widget.panelVariant || '').startsWith('showcase-template-')
}

export function panelStyle(widget = {}) {
  const theme = normalizeDashboardTheme(widget)
  const aiVisual = widget.visualMode === 'ai'
  const decoration = widget.type === 'decoration' || widget.visualRole?.includes('decoration')
  const explicitBackground = widget.style?.background ?? widget.config?.background
  const explicitBorder = widget.style?.borderColor ?? widget.config?.borderColor
  const hero = /hero|primary|spotlight|command-title/.test(`${widget.panelVariant || ''} ${widget.visualRole || ''}`)
  const generated = Boolean(widget.theme || widget.themeId || widget.panelVariant || widget.visualRole)
  const tone = surfaceTone(widget, theme)
  const basePanel = hero ? theme.panelStrong : theme.panel
  const background = decoration
    ? 'transparent'
    : (explicit(explicitBackground)
        ? explicitBackground
        : (aiVisual
            ? 'transparent'
            : (generated
            ? `linear-gradient(145deg, ${withAlpha(tone, hero ? .24 : .14)} 0%, ${basePanel} 46%, ${withAlpha(theme.backgroundRaised || theme.background, .94)} 100%)`
            : basePanel)))
  const borderColor = decoration
    ? 'transparent'
    : (explicit(explicitBorder)
        ? explicitBorder
        : (aiVisual
            ? 'transparent'
            : (generated ? `color-mix(in srgb, ${tone} ${hero ? 58 : 40}%, ${theme.grid})` : theme.grid)))
  const radius = Number(widget.style?.radius ?? (aiVisual ? 0 : theme.surfaceRadius))
  const borderWidth = widget.style?.borderWidth ?? widget.config?.borderWidth ?? (aiVisual ? 0 : 1)
  const glass = /glass|hologram/i.test(widget.panelVariant || '')
  const backdropFilter = widget.style?.backdropFilter
    ?? (glass ? 'blur(14px) saturate(1.28)' : undefined)
  const defaultShadow = !aiVisual && generated && !decoration
    ? `inset 0 1px 0 ${withAlpha(tone, .24)}, inset 0 0 42px ${withAlpha(tone, hero ? .08 : .035)}, 0 16px 42px ${withAlpha(theme.background, .4)}, 0 0 26px ${withAlpha(tone, hero ? .15 : .07)}`
    : (aiVisual ? 'none' : undefined)

  return {
    background,
    borderColor,
    borderWidth: typeof borderWidth === 'number' ? `${Math.max(0, borderWidth)}px` : borderWidth,
    borderStyle: widget.style?.borderStyle || (Number(borderWidth) === 0 ? 'none' : 'solid'),
    borderRadius: Number.isFinite(radius) ? `${Math.max(0, radius)}px` : undefined,
    boxShadow: widget.style?.shadow ?? defaultShadow,
    backdropFilter,
    WebkitBackdropFilter: backdropFilter,
    color: widget.style?.color ?? theme.text,
    opacity: widget.style?.opacity,
    filter: widget.style?.filter,
    clipPath: widget.style?.clipPath,
    fontFamily: widget.style?.fontFamily,
    padding: widget.style?.padding,
    overflow: widget.style?.overflow,
    boxSizing: widget.style?.boxSizing || 'border-box',
    '--widget-primary': theme.primary,
    '--widget-secondary': theme.secondary,
    '--widget-accent': theme.accent,
    '--widget-danger': theme.danger,
    '--widget-positive': theme.positive,
    '--widget-panel': theme.panel,
    '--widget-panel-strong': theme.panelStrong,
    '--widget-grid': theme.grid,
    '--widget-text': theme.text,
    '--widget-muted': theme.muted,
    '--widget-glow': widget.style?.glow || tone,
    '--widget-surface': tone,
    '--widget-surface-lift': theme.surfaceLift || tone,
    '--widget-background-raised': theme.backgroundRaised || theme.background,
    '--widget-ambient-primary': theme.ambientPrimary,
    '--widget-ambient-secondary': theme.ambientSecondary
  }
}

export { FALLBACK_THEME }
