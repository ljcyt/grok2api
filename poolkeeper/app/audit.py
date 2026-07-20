from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("poolkeeper")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def redact_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    blocked = {
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "web_token",
        "authorization",
    }
    out: Dict[str, Any] = {}
    for k, v in data.items():
        if k.lower() in blocked:
            out[k] = "***"
        else:
            out[k] = v
    return out


def run_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def dump_json(data: Dict[str, Any]) -> str:
    return json.dumps(redact_summary(data), ensure_ascii=False)
