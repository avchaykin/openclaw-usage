import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "0.0.0.0"
PORT = 8090
SESSIONS_DIR = Path("/Users/chay/.openclaw/agents/main/sessions")
ACTIVE_AGE_HOURS = 24
USD_TO_EUR = float(os.environ.get("OPENCLAW_USAGE_USD_TO_EUR", "0.92"))
TOPIC_MAP_FILE = Path(os.environ.get("OPENCLAW_USAGE_TOPIC_MAP", str(Path(__file__).with_name("topics.json"))))

INDEX_HTML = """<!doctype html>
<html lang=\"ru\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>OpenClaw Session Usage</title>
  <style>
    :root { color-scheme: dark; }
    body { margin:0; font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:#0d1117; color:#e6edf3; }
    .wrap { max-width: 1100px; margin: 24px auto; padding: 0 16px; }
    h1 { margin: 0 0 8px; font-size: 1.5rem; }
    .sub { color: #8b949e; margin-bottom: 16px; }
    .section { margin-top: 20px; }
    .card { background:#161b22; border:1px solid #30363d; border-radius:14px; padding:14px; margin-bottom:12px; }
    .row { display:flex; justify-content:space-between; gap:12px; flex-wrap: wrap; }
    .meta { color:#8b949e; font-size:0.9rem; }
    .model { margin-top:8px; }
    .model-line { margin:8px 0; }
    .bar-wrap { width:100%; height:10px; background:#21262d; border-radius:999px; overflow:hidden; margin-top:4px; }
    .bar { height:100%; background:linear-gradient(90deg, #2ea043, #58a6ff); }
    .pill { display:inline-block; padding:2px 8px; border:1px solid #30363d; border-radius:999px; font-size:12px; color:#8b949e; }
    .empty { color:#8b949e; padding:12px 0; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>OpenClaw: использование моделей по сессиям</h1>
    <div class=\"sub\" id=\"updated\">Загрузка...</div>

    <div class=\"section\">
      <h2>Активные сессии</h2>
      <div id=\"active\"></div>
    </div>

    <div class=\"section\">
      <h2>Архив</h2>
      <div id=\"archived\"></div>
    </div>
  </div>
<script>
function fmt(n) { return new Intl.NumberFormat('ru-RU').format(n || 0); }
function money(n) { return new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(n || 0); }

function chatLine(s) {
  const chat = s.chat_name || '—';
  const topic = s.topic_name
    ? ` · топик: ${s.topic_name}${s.topic_label ? ` (${s.topic_label})` : ''}`
    : (s.topic_label ? ` · topic: ${s.topic_label}` : '');
  return `Чат: ${chat}${topic}`;
}

function renderSession(s) {
  const models = s.models || [];
  const modelHtml = models.map(m => `
    <div class=\"model-line\">
      <div class=\"row\"><div>${m.model}</div><div>${m.percent.toFixed(1)}% · ${fmt(m.tokens)} токенов · $${money(m.usd)} / €${money(m.eur)}</div></div>
      <div class=\"bar-wrap\"><div class=\"bar\" style=\"width:${m.percent}%\"></div></div>
    </div>
  `).join('');

  return `
    <div class=\"card\">
      <div class=\"row\">
        <div><strong>${s.session}</strong></div>
        <div class=\"pill\">Всего: ${fmt(s.total_tokens)} токенов · $${money(s.total_usd)} / €${money(s.total_eur)}</div>
      </div>
      <div class=\"meta\">${chatLine(s)} · файл: ${s.file_name} · обновлено: ${s.updated_at}</div>
      <div class=\"model\">${modelHtml || '<div class=\"empty\">Нет usage-данных</div>'}</div>
    </div>
  `;
}

function renderList(containerId, list) {
  const el = document.getElementById(containerId);
  if (!list.length) {
    el.innerHTML = '<div class=\"empty\">Пусто</div>';
    return;
  }
  el.innerHTML = list.map(renderSession).join('');
}

async function load() {
  try {
    const res = await fetch('/api/sessions');
    const data = await res.json();
    document.getElementById('updated').textContent = `Обновлено: ${data.generated_at} · курс: 1 USD = ${data.usd_to_eur.toFixed(4)} EUR`;
    renderList('active', data.active);
    renderList('archived', data.archived);
  } catch (e) {
    document.getElementById('updated').textContent = 'Ошибка загрузки данных';
  }
}

load();
setInterval(load, 5000);
</script>
</body>
</html>
"""


def iso_local(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def iter_session_files():
    if not SESSIONS_DIR.exists():
        return []
    return sorted(
        [
            p
            for p in SESSIONS_DIR.iterdir()
            if p.is_file() and (p.name.endswith('.jsonl') or '.jsonl.reset.' in p.name)
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def load_topic_map():
    try:
        if TOPIC_MAP_FILE.exists():
            data = json.loads(TOPIC_MAP_FILE.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def extract_conversation_meta(path: Path):
    """Try to extract chat/title/topic from first user message metadata block."""
    chat_name = None
    topic_label = None

    try:
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                event = json.loads(line)
                if event.get('type') != 'message':
                    continue
                message = event.get('message') or {}
                if message.get('role') != 'user':
                    continue
                content = message.get('content') or []
                text = "\n".join(part.get('text', '') for part in content if part.get('type') == 'text')
                if not text:
                    continue

                match = re.search(r"Conversation info \(untrusted metadata\):\s*```json\s*(\{.*?\})\s*```", text, re.S)
                if not match:
                    break

                meta = json.loads(match.group(1))
                chat_name = meta.get('group_subject') or meta.get('conversation_label')
                conv_label = meta.get('conversation_label', '')
                tmatch = re.search(r"topic:(\d+)", conv_label)
                if tmatch:
                    topic_label = tmatch.group(1)
                break
    except Exception:
        pass

    if not topic_label:
        tm = re.search(r"-topic-(\d+)", path.name)
        if tm:
            topic_label = tm.group(1)

    return chat_name or 'Direct/Unknown', topic_label


def parse_usage(path: Path):
    token_totals = defaultdict(int)
    usd_totals = defaultdict(float)
    total_tokens = 0
    total_usd = 0.0
    parse_errors = 0

    try:
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue

                if event.get('type') != 'message':
                    continue
                message = event.get('message') or {}
                usage = message.get('usage') or {}
                tokens = usage.get('totalTokens')
                if not isinstance(tokens, int) or tokens <= 0:
                    continue

                cost_total = ((usage.get('cost') or {}).get('total'))
                if isinstance(cost_total, (int, float)):
                    cost_total = float(cost_total)
                else:
                    cost_total = 0.0

                model = message.get('model') or 'unknown'
                provider = message.get('provider') or 'unknown'
                key = f"{provider}/{model}"

                token_totals[key] += tokens
                usd_totals[key] += cost_total
                total_tokens += tokens
                total_usd += cost_total
    except Exception:
        parse_errors += 1

    models = []
    for model, tokens in sorted(token_totals.items(), key=lambda x: x[1], reverse=True):
        percent = (tokens / total_tokens * 100.0) if total_tokens else 0.0
        usd = usd_totals[model]
        eur = usd * USD_TO_EUR
        models.append(
            {
                "model": model,
                "tokens": tokens,
                "percent": percent,
                "usd": usd,
                "eur": eur,
            }
        )

    return total_tokens, total_usd, total_usd * USD_TO_EUR, models, parse_errors


def session_kind(path: Path) -> str:
    if '.jsonl.reset.' in path.name:
        return 'archived'
    age_seconds = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    if age_seconds <= ACTIVE_AGE_HOURS * 3600:
        return 'active'
    return 'archived'


def collect_data():
    active = []
    archived = []
    topic_map = load_topic_map()

    for path in iter_session_files():
        total_tokens, total_usd, total_eur, models, parse_errors = parse_usage(path)
        chat_name, topic_label = extract_conversation_meta(path)
        topic_name = topic_map.get(str(topic_label)) if topic_label else None
        row = {
            "session": path.name.split('.jsonl')[0],
            "file_name": path.name,
            "updated_at": iso_local(path.stat().st_mtime),
            "chat_name": chat_name,
            "topic_label": topic_label,
            "topic_name": topic_name,
            "total_tokens": total_tokens,
            "total_usd": total_usd,
            "total_eur": total_eur,
            "models": models,
            "parse_errors": parse_errors,
        }
        (active if session_kind(path) == 'active' else archived).append(row)

    return {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "usd_to_eur": USD_TO_EUR,
        "active": active,
        "archived": archived,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode('utf-8'))
            return

        if parsed.path == '/api/sessions':
            payload = json.dumps(collect_data(), ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(404)
        self.end_headers()


if __name__ == '__main__':
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Session usage dashboard on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
