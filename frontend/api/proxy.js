const READ_ONLY_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

function cleanEnvironmentValue(value) {
  return String(value || '').replace(/^\uFEFF/, '').trim()
}

function copyRequestHeaders(requestHeaders) {
  const headers = new Headers()
  for (const name of ['accept', 'accept-encoding', 'accept-language', 'content-type', 'if-none-match']) {
    const value = requestHeaders[name]
    if (typeof value === 'string' && value) headers.set(name, value)
  }
  headers.set('user-agent', 'WeatherBot-Vercel-Proxy/1.0')
  return headers
}

export default async function handler(request, response) {
  const method = String(request.method || 'GET').toUpperCase()

  if (method === 'OPTIONS') {
    response.setHeader('allow', 'GET, HEAD, OPTIONS')
    return response.status(204).end()
  }

  if (!READ_ONLY_METHODS.has(method)) {
    response.setHeader('allow', 'GET, HEAD, OPTIONS')
    return response.status(405).json({
      ok: false,
      reason: 'public_dashboard_read_only',
    })
  }

  const originUrl = cleanEnvironmentValue(process.env.WEATHERBOT_ORIGIN_URL).replace(/\/$/, '')
  const originToken = cleanEnvironmentValue(process.env.WEATHERBOT_ORIGIN_TOKEN)
  if (!originUrl || !originToken) {
    return response.status(503).json({
      ok: false,
      reason: 'origin_not_configured',
    })
  }

  const incomingUrl = new URL(request.url || '/', 'https://polywxx.org')
  const routedPath = incomingUrl.searchParams.get('path')
  incomingUrl.searchParams.delete('path')
  const targetPath = routedPath
    ? `/api/${routedPath.replace(/^\/+/, '')}`
    : incomingUrl.pathname
  const targetUrl = new URL(`${targetPath}${incomingUrl.search}`, `${originUrl}/`)
  const headers = copyRequestHeaders(request.headers)
  headers.set('x-weatherbot-origin-token', originToken)

  try {
    const upstream = await fetch(targetUrl, {
      method,
      headers,
      redirect: 'manual',
      signal: AbortSignal.timeout(25_000),
    })

    for (const name of ['cache-control', 'content-type', 'etag', 'last-modified']) {
      const value = upstream.headers.get(name)
      if (value) response.setHeader(name, value)
    }
    response.setHeader('x-weatherbot-data-mode', 'live')
    response.setHeader('x-weatherbot-write-enabled', 'false')

    if (method === 'HEAD' || upstream.status === 204 || upstream.status === 304) {
      return response.status(upstream.status).end()
    }

    const body = Buffer.from(await upstream.arrayBuffer())
    return response.status(upstream.status).send(body)
  } catch (error) {
    return response.status(502).json({
      ok: false,
      reason: 'origin_unavailable',
      detail: error instanceof Error ? error.name : 'unknown_error',
    })
  }
}
