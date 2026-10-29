"""Backup jobs for tenant upload folders."""
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

BACKUP_ROOT = Path("/var/backups/tenants")
UPLOAD_ROOT = Path("/srv/uploads")


def backup_tenant(tenant_id: str) -> Path:
    """Archive one tenant's whole upload folder into BACKUP_ROOT and return the archive path."""
    source = UPLOAD_ROOT / tenant_id
    target = BACKUP_ROOT / tenant_id
    archive = shutil.make_archive(str(target), "gztar", root_dir=source)
    log.info("backed up %s to %s", tenant_id, archive)
    return Path(archive)
