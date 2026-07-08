"""Ask-pipeline debug tracing for local testing."""
from __future__ import annotations

import json
import time
from pathlib import Path

import config

_LEGACY_LOG = Path(__file__).resolve().parent.parent / "debug-3cdccf.log"
_ASK_LOG = Path(__file__).resolve().parent / "ask-debug.log"
_AGENT_LOG = Path(__file__).resolve().parent.parent / "debug-cf70fb.log"


def agent_log(
    location: str,
    message: str,
    data: dict | None = None,
    *,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    """NDJSON trace for Cursor debug mode (session cf70fb)."""
    payload = {
        "sessionId": "cf70fb",
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "data": data or {},
        "hypothesisId": hypothesis_id,
        "runId": run_id,
    }
    try:
        with _AGENT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass


def _write_line(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def clear_ask_log() -> None:
    """Mark a new server session in ask-debug.log (append, do not wipe history)."""
    if not config.ASK_DEBUG_LOG:
        return
    try:
        payload = {
            "ts": int(time.time() * 1000),
            "phase": "server_start",
            "message": "NexA backend started",
        }
        _write_line(_ASK_LOG, payload)
        print(f"[ASK] debug log session → {_ASK_LOG}", flush=True)
    except Exception:
        pass


def ask_trace(phase: str, **data) -> None:
    """Human-readable + JSON trace when ASK_DEBUG_LOG=true."""
    if not config.ASK_DEBUG_LOG:
        return
    payload = {
        "ts": int(time.time() * 1000),
        "phase": phase,
        **data,
    }
    try:
        _write_line(_ASK_LOG, payload)
        summary = " | ".join(
            f"{k}={v!r}" if not isinstance(v, (dict, list)) else f"{k}=..."
            for k, v in data.items()
        )
        print(f"[ASK] {phase}: {summary}", flush=True)
    except Exception:
        pass


def debug_log(
    location: str,
    message: str,
    data: dict | None = None,
    *,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    payload = {
        "sessionId": "3cdccf",
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "data": data or {},
        "hypothesisId": hypothesis_id,
        "runId": run_id,
    }
    if config.ASK_DEBUG_LOG:
        try:
            _write_line(_ASK_LOG, {"phase": message, "location": location, **(data or {})})
            print(f"[ASK] {message} @ {location}", flush=True)
        except Exception:
            pass
    try:
        with _LEGACY_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass
