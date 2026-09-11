import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { matchesReview, validateReviews } from './reviewed-integrations.mjs'

export const BASELINE = '8072b642927ea5468f2a6a9bc78eb9e77f5ca338'

export function isPreservedMainNavLayout(original, relocated, originalExists) {
  return (
    !originalExists &&
    typeof original === 'string' &&
    original.length > 0 &&
    typeof relocated === 'string' &&
    original.replace(/\r\n/g, '\n') === relocated.replace(/\r\n/g, '\n')
  )
}

// These integration seams are intentionally reviewed and tested separately.
const integrationSeams = new Set([
  'web/app/components/main-nav/index.tsx',
  'web/app/components/main-nav/__tests__/index.spec.tsx',
  'web/env.ts',
])

export function isProtectedPath(path) {
  return !(
    path.startsWith('enterprise/') ||
    path.startsWith('web/app/(commonLayout)/enterprise/') ||
    path.startsWith('web/app/components/enterprise/') ||
    integrationSeams.has(path) ||
    /^web\/i18n\/[^/]+\/common\.json$/.test(path)
  )
}

export function findProtectedChanges(changedPaths, baselinePaths) {
  return changedPaths.filter((path) => baselinePaths.has(path) && isProtectedPath(path))
}

export function findChangedOriginalKeys(original, current, prefix = '') {
  if (current === null || typeof current !== 'object' || Array.isArray(current))
    return [prefix || '$']
  const changed = []
  for (const [key, value] of Object.entries(original)) {
    const path = prefix ? `${prefix}.${key}` : key
    const next = current?.[key]
    if (!Object.hasOwn(current, key)) changed.push(path)
    else if (value !== null && typeof value === 'object' && !Array.isArray(value))
      changed.push(...findChangedOriginalKeys(value, next, path))
    else if (JSON.stringify(value) !== JSON.stringify(next)) changed.push(path)
  }
  return changed
}

export function checkNativeBaseline(cwd) {
  const git = (args) =>
    execFileSync('git', args, {
      cwd,
      encoding: 'utf8',
      maxBuffer: 16 * 1024 * 1024,
      windowsHide: true,
    })
  const root = git(['rev-parse', '--show-toplevel']).trim()
  const baselinePaths = new Set(
    git(['ls-tree', '-r', '--name-only', '-z', BASELINE]).split('\0').filter(Boolean),
  )
  const changedPaths = git(['diff', '--name-only', '--no-renames', '-z', BASELINE, '--'])
    .split('\0')
    .filter(Boolean)
  const reviewPath = resolve(root, 'enterprise/reviewed-native-integrations.json')
  const reviews = existsSync(reviewPath)
    ? validateReviews(JSON.parse(readFileSync(reviewPath, 'utf8')), BASELINE)
    : new Map()
  const exactReviewedIntegrations = []
  const violations = findProtectedChanges(changedPaths, baselinePaths).filter((path) => {
    if (path === 'web/app/components/main-nav/layout.tsx') {
      try {
        if (
          isPreservedMainNavLayout(
            git(['show', `${BASELINE}:${path}`]),
            readFileSync(resolve(root, 'web/app/components/main-nav/layout-shell.tsx'), 'utf8'),
            existsSync(resolve(root, path)),
          )
        ) {
          exactReviewedIntegrations.push(`${path} -> layout-shell.tsx (unchanged)`)
          return false
        }
      } catch {
        // A missing relocation is still a deleted native component.
      }
    }
    if (!reviews.has(path)) return true
    try {
      if (
        matchesReview(
          path,
          git(['show', `${BASELINE}:${path}`]),
          readFileSync(resolve(root, path), 'utf8'),
          reviews,
        )
      ) {
        exactReviewedIntegrations.push(path)
        return false
      }
    } catch {
      /* Deleted/unreadable files remain violations. */
    }
    return true
  })
  const reviewedSeams = []
  for (const path of changedPaths) {
    if (!baselinePaths.has(path) || isProtectedPath(path)) continue
    if (/^web\/i18n\/[^/]+\/common\.json$/.test(path)) {
      try {
        const original = JSON.parse(git(['show', `${BASELINE}:${path}`]))
        const current = JSON.parse(readFileSync(resolve(root, path), 'utf8'))
        violations.push(
          ...findChangedOriginalKeys(original, current).map((key) => `${path}#${key}`),
        )
      } catch (error) {
        violations.push(`${path}: ${error.message}`)
      }
    } else {
      reviewedSeams.push(path)
    }
  }
  return {
    baseline: BASELINE,
    protectedFileCount: [...baselinePaths].filter(isProtectedPath).length,
    violations,
    integrationSeamsRequiringReview: reviewedSeams,
    exactReviewedIntegrations,
    note: 'Source preservation check; not a substitute for native runtime regression tests.',
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const report = checkNativeBaseline(process.cwd())
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
    process.exitCode = report.violations.length ? 1 : 0
  } catch (error) {
    process.stderr.write(`Native baseline check failed: ${error.message}\n`)
    process.exitCode = 1
  }
}
