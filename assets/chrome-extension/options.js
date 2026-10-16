const DEFAULT_PORT = 18792

function clampPort(value) {
  const n = Number.parseInt(String(value || ''), 10)
  if (!Number.isFinite(n)) return DEFAULT_PORT
  if (n <= 0 || n > 65535) return DEFAULT_PORT
  return n
}

function updateRelayUrl(port) {
  const el = document.getElementById('relay-url')
  if (!el) return
  el.textContent = `http://127.0.0.1:${port}/`
}

function setStatus(kind, message) {
  const status = document.getElementById('status')
  if (!status) return
  status.dataset.kind = kind || ''
  status.textContent = message || ''
}

async function checkRelayReachable(port) {
  const url = `http://127.0.0.1:${port}/`
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), 900)
  try {
    const res = await fetch(url, { method: 'HEAD', headers: { Authorization: `Bearer ${document.getElementById('token').value}` }, signal: ctrl.signal })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    setStatus('ok', `Relay reachable at ${url}`)
  } catch {
    setStatus(
      'error',
      `Relay not reachable at ${url}. Start Moltbot’s browser relay on this machine, then click the toolbar button again.`,
    )
  } finally {
    clearTimeout(t)
  }
}

async function load() {
  const stored = await chrome.storage.local.get(['relayPort', 'relayToken'])
  document.getElementById('token').value = stored.relayToken || ''
  const port = clampPort(stored.relayPort)
  document.getElementById('port').value = String(port)
  updateRelayUrl(port)
  await checkRelayReachable(port)
}

async function save() {
  const input = document.getElementById('port')
  const port = clampPort(input.value)
  const relayToken = document.getElementById('token').value.trim()
  if (relayToken.length < 32) return setStatus('error', 'Token must contain at least 32 characters')
  await chrome.storage.local.set({ relayPort: port, relayToken })
  input.value = String(port)
  updateRelayUrl(port)
  await checkRelayReachable(port)
}

document.getElementById('save').addEventListener('click', () => void save())
void load()
