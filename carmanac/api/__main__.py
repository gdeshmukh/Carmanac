"""Serve the read pages locally: `python -m carmanac.api`.

Auto-reload for dev; local only - nothing here is deployment.
"""

from __future__ import annotations

import uvicorn

uvicorn.run("carmanac.api.app:app", host="127.0.0.1", port=8000, reload=True)
