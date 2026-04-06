import logging
import uuid
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from invoicer.invoice_tasks import process_invoice_job

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run background invoice generation for a given job UUID."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("public_id", type=str, help="InvoiceJob.public_id (UUID)")

    def handle(self, *args: Any, **options: Any) -> None:
        log_path = settings.BASE_DIR / "data" / "invoice_worker.log"
        log_path.parent.mkdir(exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        invoicer_log = logging.getLogger("invoicer")
        invoicer_log.addHandler(fh)
        try:
            uid = uuid.UUID(options["public_id"])
        except ValueError:
            self.stderr.write(self.style.ERROR("Invalid UUID"))
            return
        try:
            process_invoice_job(uid)
        finally:
            invoicer_log.removeHandler(fh)
            fh.close()
