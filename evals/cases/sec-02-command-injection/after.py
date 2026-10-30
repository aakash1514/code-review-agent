"""Backup jobs for tenant upload folders."""
import logging
import shutil
import subprocess
from pathlib import Path

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
bp = Blueprint("backups", __name__)

BACKUP_ROOT = Path("/var/backups/tenants")
UPLOAD_ROOT = Path("/srv/uploads")


def backup_tenant(tenant_id: str) -> Path:
    """Archive one tenant's whole upload folder into BACKUP_ROOT and return the archive path."""
    source = UPLOAD_ROOT / tenant_id
    target = BACKUP_ROOT / tenant_id
    archive = shutil.make_archive(str(target), "gztar", root_dir=source)
    log.info("backed up %s to %s", tenant_id, archive)
    return Path(archive)


@bp.route("/tenants/<tenant_id>/backups", methods=["POST"])
def backup_folder(tenant_id: str):
    """Archive a single folder chosen in the restore UI and return the archive path."""
    folder = request.json["folder"]
    archive = BACKUP_ROOT / f"{tenant_id}-partial.tar.gz"
    cmd = f"tar -czf {archive} -C {UPLOAD_ROOT / tenant_id} {folder}"
    subprocess.run(cmd, shell=True, check=True)
    log.info("backed up %s/%s to %s", tenant_id, folder, archive)
    return jsonify({"archive": str(archive)})
