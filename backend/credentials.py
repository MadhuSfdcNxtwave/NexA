"""On Render you can't run `gcloud auth`. Instead you paste the whole
service-account JSON into ONE secret env var (GCP_SA_KEY_JSON). This module
writes it to a temp file at startup and points Application Default
Credentials at it, so both BigQuery and Vertex AI authenticate automatically
with no further code.

Locally you can skip this and use `gcloud auth application-default login`
instead — if GCP_SA_KEY_JSON is unset, this is a no-op.
"""
import os
import tempfile

import config


def bootstrap_gcp_credentials() -> None:
    raw = os.environ.get("GCP_SA_KEY_JSON", "").strip()
    if raw and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        path = os.path.join(tempfile.gettempdir(), "gcp-sa.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

    if config.GCP_PROJECT and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        os.environ["GOOGLE_CLOUD_PROJECT"] = config.GCP_PROJECT
