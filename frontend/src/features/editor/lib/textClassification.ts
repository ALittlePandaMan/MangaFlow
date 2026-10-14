import type {ImagePage} from '../../../types'

export function classificationNeedsDetection(summary: ImagePage['text_classification'], config: Record<string, unknown>): boolean {
  const value = config.text_classification
  const options = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const enabled = value !== false && options.enabled !== false
  const filterSfx = options.filter_sfx !== false
  const confidence = Number(options.min_sfx_confidence ?? 0.6)
  if (!summary?.policy) return enabled
  if (enabled && summary.status === 'unavailable') return true
  return summary.version !== 1 || summary.policy.enabled !== enabled
    || summary.policy.filter_sfx !== filterSfx || summary.policy.min_sfx_confidence !== confidence
}
