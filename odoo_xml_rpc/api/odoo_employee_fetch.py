import base64
import re
import frappe
from frappe.utils import getdate, today
from frappe.utils.background_jobs import get_job as _get_job
from frappe.utils.background_jobs import get_job_status as _get_job_status
from odoo_xml_rpc.integrations.odoo_client import get_client
from frappe.utils.file_manager import save_file
import binascii


def _m2o_display_name(value) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        return value.get("display_name") or ""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return value[1] or ""
    return ""


def _split_name(full_name: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s+", " ", (full_name or "").strip())
    if not cleaned:
        return "", ""
    parts = cleaned.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _safe_set(doc, fieldname: str, value):
    if hasattr(doc, fieldname):
        setattr(doc, fieldname, value)


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


def _attach_employee_image(emp, image_b64: str, overwrite: int = 0):
    if not hasattr(emp, "image"):
        return
    if not image_b64:
        return

    # Odoo usually returns base64 string, sometimes data-uri
    if not isinstance(image_b64, str):
        # sometimes it could be False/None, or bytes
        try:
            image_b64 = image_b64.decode("utf-8")
        except Exception:
            return

    raw = (image_b64 or "").strip()
    if not raw:
        return

    # If already has image and overwrite is off
    if emp.image and not int(overwrite or 0):
        return

    # Strip data URI prefix if present
    # e.g. data:image/png;base64,iVBORw0KGgo...
    if raw.startswith("data:") and "base64," in raw:
        raw = raw.split("base64,", 1)[1].strip()

    # Some Odoo responses can include whitespace/newlines
    raw = "".join(raw.split())

    # Fix missing padding
    # base64 length must be multiple of 4
    pad = (-len(raw)) % 4
    if pad:
        raw += ("=" * pad)

    try:
        content = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as e:
        frappe.log_error(
            title="Odoo Employee Image: base64 decode failed",
            message=f"Employee={getattr(emp,'name',None)} odoo_id={getattr(emp,'custom_odoo_id',None)}\nError={e}\nLen={len(raw)}\nHead={raw[:50]}",
        )
        return

    if not content or len(content) < 20:
        # too small / invalid
        return

    ext = _detect_image_extension(content)
    filename = f"odoo_employee_{getattr(emp,'custom_odoo_id','')}.{ext}"

    # If overwriting: remove only previous image file (safer than deleting all attachments)
    if emp.image and int(overwrite or 0):
        try:
            old = frappe.db.get_value("File", {"file_url": emp.image}, "name")
            if old:
                frappe.delete_doc("File", old, ignore_permissions=True, force=True)
        except Exception:
            pass
        emp.image = ""

    try:
        # save_file handles bytes correctly across Frappe versions
        f = save_file(
            fname=filename,
            content=content,
            dt=emp.doctype,
            dn=emp.name,
            is_private=0,
        )
        emp.image = f.file_url
    except Exception as e:
        frappe.log_error(
            title="Odoo Employee Image: save_file failed",
            message=f"Employee={emp.name} odoo_id={getattr(emp,'custom_odoo_id',None)}\nError={e}\nBytes={len(content)}",
        )
        return
def _build_odoo_avatar_url(base_url: str, odoo_id: int, write_date: str | None):
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
    return f"{cleaned}/web/image/hr.employee.public/{odoo_id}/avatar_128{unique}"


def _ensure_designation(name: str) -> str:
    if not name:
        return ""
    if frappe.db.exists("Designation", name):
        return name
    try:
        doc = frappe.get_doc(
            {
                "doctype": "Designation",
                "designation_name": name,
            }
        )
        doc.insert(ignore_permissions=True)
        return doc.name
    except Exception:
        return ""


def _ensure_gender(name: str) -> str:
    if not name:
        return ""
    if frappe.db.exists("Gender", name):
        return name
    try:
        doc = frappe.get_doc(
            {
                "doctype": "Gender",
                "gender": name,
            }
        )
        doc.insert(ignore_permissions=True)
        return doc.name
    except Exception:
        return ""


def _parse_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(getdate(value))
    except Exception:
        return ""


def _upsert_employee(r: dict):
    odoo_id = int(r.get("id") or 0)
    if not odoo_id:
        return None, False

    meta = frappe.get_meta("Employee")
    if not meta.has_field("custom_odoo_id"):
        frappe.throw("Employee doctype is missing custom field `custom_odoo_id` (Int, Unique).")

    existing = frappe.db.get_value("Employee", {"custom_odoo_id": odoo_id}, "name")
    if existing:
        emp = frappe.get_doc("Employee", existing)
        is_new = False
    else:
        emp = frappe.new_doc("Employee")
        emp.custom_odoo_id = odoo_id
        is_new = True
    if not getattr(emp, "custom_odoo_id", None):
        emp.custom_odoo_id = odoo_id

    full_name = (r.get("name") or "").strip()
    first_name, last_name = _split_name(full_name)

    _safe_set(emp, "employee_name", full_name)
    _safe_set(emp, "first_name", first_name)
    _safe_set(emp, "last_name", last_name)

    if hasattr(emp, "designation"):
        job_title = (r.get("job_title") or "").strip()
        if job_title:
            designation_name = _ensure_designation(job_title)
            if designation_name:
                emp.designation = designation_name

    department_name = _m2o_display_name(r.get("department_id"))
    if department_name and frappe.db.exists("Department", department_name):
        _safe_set(emp, "department", department_name)

    company_name = _m2o_display_name(r.get("company_id"))
    if company_name and frappe.db.exists("Company", company_name):
        _safe_set(emp, "company", company_name)
    elif hasattr(emp, "company") and not getattr(emp, "company", None):
        default_company = frappe.db.get_default("company")
        if default_company and frappe.db.exists("Company", default_company):
            emp.company = default_company

    if hasattr(emp, "status"):
        emp.status = "Active" if r.get("active", True) else "Inactive"

    if hasattr(emp, "gender"):
        gender_raw = (r.get("gender") or "").strip().lower()
        gender_map = {"male": "Male", "female": "Female", "other": "Other"}
        gender_name = gender_map.get(gender_raw, "") or "Other"
        gender_name = _ensure_gender(gender_name)
        if gender_name:
            emp.gender = gender_name

    if hasattr(emp, "date_of_birth") and not getattr(emp, "date_of_birth", None):
        dob = _parse_date(r.get("birthday") or r.get("birthdate") or "")
        emp.date_of_birth = dob or "1990-01-01"

    if hasattr(emp, "date_of_joining") and not getattr(emp, "date_of_joining", None):
        doj = _parse_date(r.get("first_contract_date") or r.get("create_date") or "")
        emp.date_of_joining = doj or today()

    _safe_set(emp, "cell_number", r.get("mobile_phone") or "")
    _safe_set(emp, "company_email", r.get("work_email") or "")

    emp.save(ignore_permissions=True)

    image_b64 = r.get("avatar_1920") or r.get("image_1920") or r.get("image_1024")
    _attach_employee_image(emp, image_b64 or "", overwrite=r.get("_overwrite_images") or 0)
    if not emp.image:
        avatar_url = _build_odoo_avatar_url(
            r.get("_odoo_base_url") or "",
            odoo_id,
            r.get("write_date"),
        )
        if avatar_url:
            emp.image = avatar_url
    if emp.image:
        emp.save(ignore_permissions=True)
    return emp, is_new


@frappe.whitelist()
def fetch_employees_raw(
    limit: int = 0, batch_size: int = 200, run_async: int = 1, overwrite_images: int = 0
):
    if not frappe.has_permission("Employee", "write"):
        frappe.throw("Not permitted to update Employees.")

    if int(run_async or 0):
        job = frappe.enqueue(
            "odoo_xml_rpc.api.odoo_employee_fetch._run_fetch_employees_raw",
            queue="long",
            job_name="fetch_employees_raw",
            limit=limit,
            batch_size=batch_size,
            overwrite_images=overwrite_images,
        )
        return {"job_id": job.id}

    return _run_fetch_employees_raw(
        limit=limit, batch_size=batch_size, overwrite_images=overwrite_images
    )


def _run_fetch_employees_raw(
    limit: int = 0, batch_size: int = 200, overwrite_images: int = 0
):
    c = get_client()

    fields_full = [
        "id",
        "name",
        "active",
        "write_date",
        "job_title",
        "work_email",
        "mobile_phone",
        "work_phone",
        "department_id",
        "company_id",
        "user_id",
        "gender",
        "birthday",
        "first_contract_date",
        "create_date",
        "avatar_1920",
        "image_1920",
        "image_1024",
    ]
    fields_public = [
        "id",
        "name",
        "active",
        "write_date",
        "job_title",
        "work_email",
        "mobile_phone",
        "work_phone",
        "department_id",
        "company_id",
        "user_id",
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
    model = "hr.employee"
    fields = list(fields_full)
    retried_public = False


    while True:
        batch_limit = batch_size
        if limit:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            batch_limit = min(batch_limit, remaining)

        try:
            batch = c.search_read(
                model=model,
                domain=[],
                fields=fields,
                limit=batch_limit,
                offset=offset,
                order="id asc",
                load=True,
                context=context,
            )
        except frappe.ValidationError as exc:
            msg = str(exc)
            if (not retried_public) and ("hr.employee.public" in msg or "AccessError" in msg):
                retried_public = True
                model = "hr.employee.public"
                fields = list(fields_public)
                rows = []
                offset = 0
                continue
            if (not retried_public) and "Invalid field" in msg and "hr.employee" in msg:
                retried_public = True
                model = "hr.employee.public"
                fields = list(fields_public)
                rows = []
                offset = 0
                continue
            raise
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)

    created = 0
    odoo_base_url = ""
    try:
        settings = frappe.get_single("Odoo Config")
        odoo_base_url = settings.odoo_url or ""
    except Exception:
        odoo_base_url = ""

    for r in rows:
        payload = dict(r)
        payload["_overwrite_images"] = int(overwrite_images or 0)
        payload["_odoo_base_url"] = odoo_base_url
        _, is_new = _upsert_employee(payload)
        if is_new:
            created += 1

    return {"fetched": len(rows), "created": created}


@frappe.whitelist()
def get_fetch_employees_job_status(job_id: str):
    if not frappe.has_permission("Employee", "read"):
        frappe.throw("Not permitted to read Employees.")

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
