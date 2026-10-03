# Gnani MCP server (Raahi voice rail)

Six tools matching capabilities 1-6 in the doc, plus `list_tts_voices`.

## Run
    python -m venv venv && . venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # fill in
    python server.py       # stdio

## Claude Desktop / Claude Code config
    {
      "mcpServers": {
        "gnani": {
          "command": "/abs/path/venv/bin/python",
          "args": ["/abs/path/gnani-mcp/server.py"],
          "env": { "GNANI_API_KEY": "...", "GNANI_ALLOWED_NUMBERS": "+91..." }
        }
      }
    }
    # Claude Code: claude mcp add gnani -e GNANI_API_KEY=... -- /abs/path/venv/bin/python /abs/path/server.py

## Status
| Tool | Endpoint | Confidence |
|---|---|---|
| transcribe_speech | POST api.vachana.ai/stt/v3 | Verified from official SDK |
| speak_reply | POST api.vachana.ai/api/v1/tts/inference | Verified from official SDK |
| call_bank_rm_or_desk | env: GNANI_CALL_TRIGGER_PATH | ASSUMED - confirm with Gnani |
| read_call_outcome | env: GNANI_CALL_STATS_PATH | ASSUMED - confirm with Gnani |
| navigate_ivr | env: GNANI_CALL_DTMF_PATH | ASSUMED - confirm with Gnani |
| pull_case_status_into_call | env: CASE_STATUS_URL | You build this endpoint |

## Notes
- MCP is request/response, so the *realtime* WebSocket STT/TTS (wss://api.vachana.ai/stt/v3/stream,
  /api/v1/tts) is not exposed here. Use the REST tools for WhatsApp voice notes; use the WebSocket
  directly (gnani-vachana / pipecat-gnani) for live phone calls.
- REST STT clips are limited to ~60 s.
- Gnani's "DTMF Collection" feature is documented as *collecting* keypresses from callers. Sending
  tones into a bank's IVR may need a different Gnani/telephony capability - verify before relying on navigate_ivr.

## Deploy on Render (HTTP)
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health`
- Environment variables: everything in `.env.example`, plus `MCP_AUTH_TOKEN` (any long random string).
- MCP URL to give your client: `https://<service>.onrender.com/mcp` with header `Authorization: Bearer <MCP_AUTH_TOKEN>`.
- Without `MCP_AUTH_TOKEN` anyone with the URL can place calls and spend your Gnani credits, so always set it.
- `GNANI_AUDIO_OUT_DIR` files are on Render's ephemeral disk; fetch/serve them promptly.
