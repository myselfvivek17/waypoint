/* Thin JSON client for the Waypoint API. Every call has a timeout and returns
   {ok, status, data} instead of throwing, so the UI can always show a friendly state. */

async function call(method, path, body, timeoutMs) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      signal: ctl.signal,
      credentials: 'same-origin',
    });
    const type = res.headers.get('content-type') || '';
    const data = type.includes('json') ? await res.json() : await res.blob();
    return { ok: res.ok, status: res.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: { error: e.name === 'AbortError' ? 'That took too long.' : "Can't reach Waypoint." } };
  } finally {
    clearTimeout(timer);
  }
}

export function errorText(r) {
  if (r.status === 429) return 'Lots of requests just now. Please wait a few seconds and try again.';
  if (r.status === 0) return r.data.error + ' Check that the server is running, then try again.';
  return (r.data && r.data.error) || 'Something went wrong. Please try again.';
}

export const api = {
  health: () => call('GET', '/api/health', null, 5000),
  chat: (body) => call('POST', '/api/chat', body, 40000),
  plan: (profile, ai = true) => call('POST', '/api/plan', { profile, ai }, 150000),
  adjust: (body) => call('POST', '/api/adjust', body, 40000),
  apply: (body) => call('POST', '/api/apply', body, 60000),
  invite: (body) => call('POST', '/api/invite', body, 40000),
  ics: (body) => call('POST', '/api/ics', body, 10000),
  whereami: (lat, lon) => call('POST', '/api/whereami', { lat, lon }, 15000),
  prefetch: (area, guests) => call('GET', '/api/places?' + new URLSearchParams(
    guests ? { area, guests } : { area }), null, 60000),
  nearby: (lat, lon) => call('GET', '/api/places?' + new URLSearchParams({ lat: lat.toFixed(5), lon: lon.toFixed(5) }), null, 60000),
  photos: (hint) => {
    const q = {};
    if (hint.file) q.file = hint.file;
    else if (hint.wikidata) q.wikidata = hint.wikidata;
    if (hint.lat != null && hint.lon != null) { q.lat = hint.lat.toFixed(5); q.lon = hint.lon.toFixed(5); }
    if (hint.name) q.name = hint.name;
    if (hint.vibe) q.vibe = hint.vibe;
    if (hint.query) q.q = hint.query;
    return call('GET', '/api/photo?' + new URLSearchParams(q), null, 30000);
  },
};
