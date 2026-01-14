import base64
import re
import frappe
from frappe.utils import getdate, validate_email_address
from frappe.utils.background_jobs import get_job as _get_job
from frappe.utils.background_jobs import get_job_status as _get_job_status
from odoo_xml_rpc.integrations.odoo_client import get_client


def _safe_set(doc, fieldname: str, value):
    if hasattr(doc, fieldname):
        setattr(doc, fieldname, value)


def _split_name(full_name: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s+", " ", (full_name or "").strip())
    if not cleaned:
        return "", ""
    parts = cleaned.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _normalize_language(lang: str) -> str:
    if not lang:
        return ""
    value = lang.replace("-", "_")
    if value in ("en_US", "en_GB"):
        return "en"
    return value.split("_")[0]


def _detect_image_extension(content: bytes) -> str:
    if not content:
        return "png"
    header = content.lstrip()[:10]
    if header.startswith(b"<?xml") or header.startswith(b"<svg"):
        return "svg"
    if content.startswith(b"\x89PNG"):
        return "png"
    if content.startswith(b"\xff\xd8"):
        return "jpg"
    return "png"


def _attach_user_image(user, image_b64: str, overwrite: int = 0):
    if not image_b64 or not hasattr(user, "user_image"):
        return
    if not isinstance(image_b64, str):
        return
    raw = image_b64.strip()
    if not raw or "Kb" in raw or "MB" in raw or "Mb" in raw:
        return
    if user.user_image and not int(overwrite or 0):
        return
    if user.user_image and int(overwrite or 0):
        old_files = frappe.db.get_all(
            "File",
            filters={"attached_to_doctype": user.doctype, "attached_to_name": user.name},
            fields=["name"],
        )
        for f in old_files:
            try:
                frappe.delete_doc("File", f["name"], ignore_permissions=True, force=True)
            except Exception:
                pass
        user.user_image = ""

    try:
        content = base64.b64decode(raw)
    except Exception:
        return

    ext = _detect_image_extension(content)
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"odoo_user_{user.custom_odoo_id}.{ext}",
            "attached_to_doctype": user.doctype,
            "attached_to_name": user.name,
            "content": content,
            "is_private": 0,
        }
    )
    file_doc.save(ignore_permissions=True)
    user.user_image = file_doc.file_url


def _build_odoo_user_avatar_url(base_url: str, odoo_id: int, write_date: str | None):
    if not base_url or not odoo_id:
        return ""
    cleaned = base_url.strip()
    if cleaned.endswith("/jsonrpc"):
        cleaned = cleaned[:-8]
    cleaned = cleaned.rstrip("/")
    if not cleaned:
        return ""
    unique = ""
    if write_date:
        unique = f"?unique={write_date.replace(' ', 'T')}"
    return f"{cleaned}/web/image/res.users/{odoo_id}/avatar_128{unique}"


def _upsert_user(r: dict):
    odoo_id = int(r.get("id") or 0)
    if not odoo_id:
        return None, False

    meta = frappe.get_meta("User")
    if not meta.has_field("custom_odoo_id"):
        frappe.throw("User doctype is missing custom field `custom_odoo_id` (Int, Unique).")

    existing = frappe.db.get_value("User", {"custom_odoo_id": odoo_id}, "name")
    email = (r.get("email") or r.get("login") or "").strip().lower()
    if email and not validate_email_address(email, throw=False):
        return None, False
    if not existing and email:
        existing = frappe.db.get_value("User", {"email": email}, "name")

    if existing:
        user = frappe.get_doc("User", existing)
        is_new = False
    else:
        if not email:
            return None, False
        user = frappe.new_doc("User")
        user.email = email
        user.name = email
        user.send_welcome_email = 0
        is_new = True

    if not getattr(user, "custom_odoo_id", None):
        user.custom_odoo_id = odoo_id

    full_name = (r.get("name") or "").strip() or email
    first_name, last_name = _split_name(full_name)

    _safe_set(user, "first_name", first_name)
    _safe_set(user, "last_name", last_name)
    _safe_set(user, "full_name", full_name)
    _safe_set(user, "username", (r.get("login") or "").strip() or user.username)

    lang = _normalize_language(r.get("lang") or "")
    if lang and frappe.db.exists("Language", lang):
        _safe_set(user, "language", lang)

    tz = (r.get("tz") or "").strip()
    if tz:
        _safe_set(user, "time_zone", tz)

    if hasattr(user, "enabled"):
        user.enabled = 1 if r.get("active", True) else 0

    if is_new and not getattr(user, "user_type", None):
        user.user_type = "System User"

    user.save(ignore_permissions=True)

    image_b64 = r.get("avatar_1920") or r.get("image_1920") or r.get("image_1024")
    _attach_user_image(user, image_b64 or "", overwrite=r.get("_overwrite_images") or 0)
    if not user.user_image:
        avatar_url = _build_odoo_user_avatar_url(
            r.get("_odoo_base_url") or "",
            odoo_id,
            r.get("write_date"),
        )
        if avatar_url:
            user.user_image = avatar_url
    if user.user_image:
        user.save(ignore_permissions=True)

    return user, is_new


@frappe.whitelist()
def fetch_users_raw(
    limit: int = 0, batch_size: int = 200, run_async: int = 1, overwrite_images: int = 0
):
    if not frappe.has_permission("User", "write"):
        frappe.throw("Not permitted to update Users.")

    if int(run_async or 0):
        job = frappe.enqueue(
            "odoo_xml_rpc.api.odoo_user_fetch._run_fetch_users_raw",
            queue="long",
            job_name="fetch_users_raw",
            limit=limit,
            batch_size=batch_size,
            overwrite_images=overwrite_images,
        )
        return {"job_id": job.id}

    return _run_fetch_users_raw(limit=limit, batch_size=batch_size, overwrite_images=overwrite_images)


def _run_fetch_users_raw(limit: int = 0, batch_size: int = 200, overwrite_images: int = 0):
    c = get_client()

    fields = [
        "id",
        "name",
        "active",
        "write_date",
        "email",
        "login",
        "lang",
        "tz",
        "avatar_1920",
        "image_1920",
        "image_1024",
    ]

    limit = int(limit or 0)
    batch_size = int(batch_size or 200)
    if batch_size <= 0:
        batch_size = 200

    rows = []
    offset = 0
    context = {"bin_size": False}
    while True:
        batch_limit = batch_size
        if limit:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            batch_limit = min(batch_limit, remaining)

        batch = c.search_read(
            model="res.users",
            domain=[],
            fields=fields,
            limit=batch_limit,
            offset=offset,
            order="id asc",
            load=True,
            context=context,
        )
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)

    created = 0
    skipped_invalid_email = 0
    prev_mute = getattr(frappe.flags, "mute_emails", False)
    prev_import = getattr(frappe.flags, "in_import", False)
    frappe.flags.mute_emails = True
    frappe.flags.in_import = True
    odoo_base_url = ""
    try:
        settings = frappe.get_single("Odoo Config")
        odoo_base_url = settings.odoo_url or ""
    except Exception:
        odoo_base_url = ""

    try:
        for r in rows:
            payload = dict(r)
            payload["_overwrite_images"] = int(overwrite_images or 0)
            payload["_odoo_base_url"] = odoo_base_url
            user, is_new = _upsert_user(payload)
            if not user:
                skipped_invalid_email += 1
                continue
            if is_new:
                created += 1
    finally:
        frappe.flags.mute_emails = prev_mute
        frappe.flags.in_import = prev_import

    return {"fetched": len(rows), "created": created, "skipped_invalid_email": skipped_invalid_email}


@frappe.whitelist()
def get_fetch_users_job_status(job_id: str):
    if not frappe.has_permission("User", "read"):
        frappe.throw("Not permitted to read Users.")

    if not job_id:
        return {"status": "not_found"}

    if "::" in job_id:
        job_id = job_id.rsplit("::", 1)[1]

    status = _get_job_status(job_id)
    if not status:
        return {"status": "not_found"}

    status_value = getattr(status, "value", None) or str(status)
    payload = {"status": status_value.lower()}

    job = _get_job(job_id)
    if job and payload["status"] == "failed":
        exc_info = getattr(job, "exc_info", None) or ""
        if exc_info:
            payload["error"] = exc_info.splitlines()[-1].strip()

    return payload
