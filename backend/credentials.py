"""On Render you can't run `gcloud auth`. Instead you paste the whole
service-account JSON into ONE secret env var (GCP_SA_KEY_JSON). This module
writes it to a temp file at startup and points Application Default
Credentials at it, so both BigQuery and Vertex AI authenticate automatically
with no further code.

Locally you can either:
  - set GCP_SA_KEY_FILE to a path of your downloaded JSON key (recommended), or
  - set GCP_SA_KEY_JSON to the raw JSON string, or
  - run `gcloud auth application-default login` and leave both unset.

If GCP_SA_KEY_FILE is set but the file is not there yet, startup continues;
BigQuery will fail until you add the key file and restart.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import config

_BACKEND_DIR = Path(__file__).resolve().parent

# Set when credentials are ready; used by /setup/status
credentials_ready: bool = False
credentials_message: str = ""


def expected_key_path() -> str | None:
    raw = os.environ.get("GCP_SA_KEY_FILE", "").strip()
    if not raw:
        return None
    p = Path(raw.strip().strip('"').strip("'"))
    if not p.is_absolute():
        p = (_BACKEND_DIR / p).resolve()
    return str(p)


def _normalize_private_key_pem(private_key: str) -> str:
    """Wrap raw base64 keys missing -----BEGIN PRIVATE KEY----- headers."""
    pk = (private_key or "").strip()
    if not pk or "BEGIN PRIVATE KEY" in pk:
        return pk
    body = pk.replace("\\n", "").replace("\n", "").replace("\r", "").strip()
    if not body or body.startswith("PASTE"):
        return pk
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{wrapped}\n-----END PRIVATE KEY-----\n"


def _normalize_service_account_info(data: dict) -> dict:
    out = dict(data)
    if "private_key" in out:
        out["private_key"] = _normalize_private_key_pem(str(out.get("private_key") or ""))
    return out


def _verify_service_account_file(path: str) -> None:
    from google.oauth2 import service_account

    service_account.Credentials.from_service_account_file(path)


def _materialize_service_account_json(data: dict) -> str:
    """Write normalized SA JSON to a temp file and return its path."""
    normalized = _normalize_service_account_info(data)
    path = os.path.join(tempfile.gettempdir(), "nexa-gcp-sa.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized, f)
    _verify_service_account_file(path)
    return path


def _activate_credentials_path(path: str) -> None:
    global credentials_ready, credentials_message
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        usable = _materialize_service_account_json(data)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = usable
        credentials_ready = True
        credentials_message = f"Using service account key: {path}"
    except (OSError, json.JSONDecodeError, ValueError) as e:
        credentials_ready = False
        credentials_message = (
            f"Invalid service account key at {path}: {e}. "
            "Re-download the JSON from GCP IAM and run: .\\install-gcp-key.ps1 <path>"
        )


def bootstrap_gcp_credentials() -> None:
    global credentials_ready, credentials_message

    # .env GCP_SA_KEY_FILE wins over a stale GOOGLE_APPLICATION_CREDENTIALS in the shell.
    key_file = os.environ.get("GCP_SA_KEY_FILE", "").strip()
    if key_file:
        path = expected_key_path()
        if not path or not os.path.isfile(path):
            credentials_ready = False
            credentials_message = (
                f"Waiting for service account key at: {path}. "
                "Edit backend/gcp-sa-config.json in Cursor, or run install-gcp-key.ps1."
            )
            return
        _activate_credentials_path(path)
    elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
        if os.path.isfile(path):
            _activate_credentials_path(path)
        else:
            credentials_ready = False
            credentials_message = f"GOOGLE_APPLICATION_CREDENTIALS file not found: {path}"
    else:
        raw = os.environ.get("GCP_SA_KEY_JSON", "").strip()
        if raw:
            try:
                data = json.loads(raw)
                path = _materialize_service_account_json(data)
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
                credentials_ready = True
                credentials_message = "Using GCP_SA_KEY_JSON"
            except (json.JSONDecodeError, ValueError) as e:
                credentials_ready = False
                credentials_message = f"Invalid GCP_SA_KEY_JSON: {e}"
        else:
            credentials_ready = False
            credentials_message = (
                "No GCP credentials configured. Set GCP_SA_KEY_FILE in .env "
                "or run: gcloud auth application-default login"
            )

    if config.GCP_PROJECT and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        os.environ["GOOGLE_CLOUD_PROJECT"] = config.GCP_PROJECT


def service_account_email() -> str | None:
    """Email of the identity NexA uses for BigQuery (from key file or ADC)."""
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("client_email") or None
        except (OSError, json.JSONDecodeError, AttributeError):
            return None
    try:
        import google.auth

        creds, _ = google.auth.default()
        return getattr(creds, "service_account_email", None) or getattr(creds, "signer_email", None)
    except Exception:
        return None
