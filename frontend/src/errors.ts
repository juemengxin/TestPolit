type ApiErrorShape = {
  response?: {
    data?: unknown
  }
}

function valueAtPath(value: unknown, path: string[]): unknown {
  return path.reduce<unknown>((current, key) => {
    if (!current || typeof current !== 'object') return undefined
    return (current as Record<string, unknown>)[key]
  }, value)
}

function formatErrorValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(formatErrorValue).join('，')
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([key, nested]) => `${key}: ${formatErrorValue(nested)}`).join('；')
  }
  return String(value)
}

/** Normalize API errors once so pages and the global interceptor show consistent messages. */
export function apiErrorMessage(
  error: unknown,
  fallback: string,
  preferredPath: string[] = [],
): string {
  if (!error || typeof error !== 'object') return fallback

  const data = (error as ApiErrorShape).response?.data
  const preferredMessage = valueAtPath(data, preferredPath)
  if (typeof preferredMessage === 'string' && preferredMessage.trim()) return preferredMessage

  const detail = valueAtPath(data, ['detail'])
  if (typeof detail === 'string' && detail.trim()) return detail

  if (data && typeof data === 'object') {
    const message = Object.entries(data)
      .filter(([, value]) => typeof value !== 'string' || value.trim())
      .map(([key, value]) => `${key}: ${formatErrorValue(value)}`)
      .join('；')
    if (message) return message
  }

  return fallback
}
