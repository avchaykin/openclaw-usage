# openclaw-usage

Local web dashboard for OpenClaw session usage.

## Features

- Active + archived sessions
- Per-model token share (%)
- Cost in USD and EUR
- Chat name + topic id
- Optional topic name mapping via `topics.json`
- mDNS publication (`openclaw-usage.local`)

## Run

```bash
python3 server.py
```

Open: `http://127.0.0.1:8090/`

## Topic names

Edit `topics.json`:

```json
{
  "27": "Infra",
  "42": "Ideas"
}
```

## mDNS

```bash
./publish-mdns.sh
```
