"""Spawn manage.py process_invoice_job in a separate process (non-blocking HTTP)."""
from __future__ import annotations

import os
import subprocess
import sys
from uuid import UUID

from django.conf import settings


def spawn_invoice_job_process(public_id: UUID) -> None:
    cmd = [
        sys.executable,
        str(settings.BASE_DIR / "manage.py"),
        "process_invoice_job",
        str(public_id),
    ]
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    subprocess.Popen(
        cmd,
        cwd=str(settings.BASE_DIR),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
