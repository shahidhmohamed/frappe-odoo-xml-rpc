import re
import frappe
from frappe.utils import validate_email_address
from frappe.utils.background_jobs import get_job as _get_job
from frappe.utils.background_jobs import get_job_status as _get_job_status
from odoo_xml_rpc.integrations.odoo_client import get_client

MAX_DOCNAME = 140


def _m2o_display_name(value) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        return value.get("display_name") or ""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return value[1] or ""
    return ""


def _first_category_name(value) -> str:
    if not value:
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            name = _m2o_display_name(item)
            if name:
                return name
        return ""
    return _m2o_display_name(value)


def _first_category_id(value) -> int:
    if not value:
        return 0
    if isinstance(value, dict):
        return int(value.get("id") or 0)
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                cid = int(item.get("id") or 0)
                if cid:
                    return cid
            elif isinstance(item, (int, float)) and int(item) > 0:
                return int(item)
            elif isinstance(item, (list, tuple)) and item:
                try:
                    return int(item[0] or 0)
                except (TypeError, ValueError):
                    continue
        return 0
    if isinstance(value, (int, float)) and int(value) > 0:
        return int(value)
    return 0


def _parse_percent(value) -> float:
    if not value:
        return 0.0
    if isinstance(value, dict):
        display = value.get("display_name") or value.get("name")
        if not display:
            return 0.0
        value = display
    elif isinstance(value, (list, tuple)):
        if len(value) >= 2 and value[1]:
            value = value[1]
        elif len(value) == 1 and value[0]:
            value = value[0]
        else:
            return 0.0
    elif isinstance(value, (int, float)):
        return 0.0
    text = str(value)
    percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if percent_match:
        try:
            return float(percent_match.group(1))
        except ValueError:
            return 0.0
    numbers = re.findall(r"(\d+(?:\.\d+)?)", text)
    if not numbers:
        return 0.0
    try:
        return float(numbers[-1])
    except ValueError:
        return 0.0


def _coerce_percent(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return 0.0


def _find_employee_by_name(name: str) -> str:
    if not name:
        return ""
    employee = frappe.db.get_value("Employee", {"employee_name": name}, "name")
    if employee:
        return employee
    return frappe.db.get_value("Employee", {"name": name}, "name") or ""


def _find_employee_by_user(user: str) -> str:
    if not user:
        return ""
    return frappe.db.get_value("Employee", {"user_id": user}, "name") or ""


def _find_user_by_full_name(name: str) -> str:
    if not name:
        return ""
    user = frappe.db.get_value("User", {"full_name": name}, "name")
    if user:
        return user
    return frappe.db.get_value("User", {"name": name}, "name") or ""


def _find_language_name(code: str) -> str:
    if not code:
        return ""
    lang = frappe.db.get_value("Language", {"language_code": code}, "name")
    if lang:
        return lang
    return frappe.db.get_value("Language", {"name": code}, "name") or ""


def _build_customer_preview(r: dict) -> dict:
    odoo_id = int(r.get("id") or 0)
    payload = {
        "odoo_id": odoo_id,
        "customer_name": _safe_customer_name(r.get("name"), odoo_id),
        "disabled": 0 if r.get("active", True) else 1,
        "tax_id": r.get("vat") or "",
    }

    category_name = (r.get("category_display_name") or "").strip() or _first_category_name(
        r.get("category_id")
    )
    group_name = ""
    if category_name and frappe.db.exists("Customer Group", category_name):
        group_name = category_name
    else:
        category_id = _first_category_id(r.get("category_id"))
        if category_id:
            meta = frappe.get_meta("Customer Group")
            fieldname = ""
            if meta.has_field("custom_odoo_id"):
                fieldname = "custom_odoo_id"
            elif meta.has_field("odoo_id"):
                fieldname = "odoo_id"
            if fieldname:
                group_name = (
                    frappe.db.get_value("Customer Group", {fieldname: category_id}, "name") or ""
                )
    if group_name:
        payload["customer_group"] = group_name

    sales_user_name = _m2o_display_name(r.get("user_id"))
    if sales_user_name:
        user = _find_user_by_full_name(sales_user_name)
        if user:
            payload["custom_salesperson"] = user

    sales_staff_name = (r.get("x_studio_sales_staff") or "").strip()
    employee = _find_employee_by_name(sales_staff_name)
    if not employee and sales_user_name:
        employee = _find_employee_by_user(_find_user_by_full_name(sales_user_name))
    if employee:
        payload["custom_sales_staff"] = employee

    rebate = _coerce_percent(r.get("rebate_percentage"))
    if not rebate:
        rebate = _parse_percent(r.get("rebate_display_name") or r.get("rebate_name"))
    payload["custom_rebate"] = rebate

    payload["custom_old_customer_no"] = (r.get("x_studio_old_customer_no") or "").strip()
    payload["custom_partner_type"] = (r.get("x_studio_partner_type") or "").strip()
    payload["custom_old_rec"] = 1 if r.get("x_studio_old_rec") else 0

    lang_code = (r.get("lang") or "").strip()
    lang_name = _find_language_name(lang_code)
    if lang_name:
        payload["custom_language"] = lang_name

    return payload


def _fetch_rebate_config(c, rebate_ids: list[int]) -> tuple[dict[int, str], dict[int, float]]:
    if not rebate_ids:
        return {}, {}
    try:
        rows = c.search_read(
            model="rebate.configuration",
            domain=[["id", "in", list(set(rebate_ids))]],
            fields=["id", "display_name", "name", "rebate_name", "rebate_percentage"],
            limit=len(rebate_ids),
            order="id asc",
        )
    except Exception:
        return {}, {}
    name_map = {}
    pct_map = {}
    for row in rows or []:
        try:
            rid = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if not rid:
            continue
        name_map[rid] = row.get("display_name") or row.get("name") or ""
        if row.get("rebate_percentage") is not None:
            pct_map[rid] = row.get("rebate_percentage")
    return name_map, pct_map


def _collect_m2o_ids(rows, fieldname: str) -> list[int]:
    ids = []
    for r in rows:
        val = r.get(fieldname)
        if isinstance(val, (int, float)) and int(val) > 0:
            ids.append(int(val))
            continue
        if isinstance(val, dict):
            mid = int(val.get("id") or 0)
            if mid:
                ids.append(mid)
            continue
        if isinstance(val, (list, tuple)):
            if len(val) >= 2 and isinstance(val[0], (int, float)):
                mid = int(val[0] or 0)
                if mid:
                    ids.append(mid)
                continue
            for item in val:
                if isinstance(item, dict):
                    mid = int(item.get("id") or 0)
                    if mid:
                        ids.append(mid)
                elif isinstance(item, (int, float)) and int(item) > 0:
                    ids.append(int(item))
    return ids


def _fetch_name_map(c, model: str, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    try:
        rows = c.search_read(
            model=model,
            domain=[["id", "in", list(set(ids))]],
            fields=["id", "name"],
            limit=len(ids),
            order="id asc",
        )
    except Exception:
        return {}
    return {int(r.get("id")): (r.get("name") or "") for r in rows}


def _fetch_m2o_name_map(c, model: str, fieldname: str, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    relation = _get_m2o_relation(c, model, fieldname)
    if not relation:
        return {}
    try:
        rows = c.name_get(relation, ids)
    except Exception:
        rows = []
    mapping = {}
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            try:
                mapping[int(row[0])] = row[1] or ""
            except (TypeError, ValueError):
                continue
    if mapping:
        return mapping
    try:
        rows = c.search_read(
            model=relation,
            domain=[["id", "in", list(set(ids))]],
            fields=["id", "display_name", "name"],
            limit=len(ids),
            order="id asc",
        )
    except Exception:
        rows = []
    for row in rows:
        try:
            rid = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if rid:
            mapping[rid] = row.get("display_name") or row.get("name") or ""
    if mapping:
        return mapping
    try:
        rows = c.web_read(relation, ids, ["display_name", "name"])
    except Exception:
        frappe.log_error(
            title="Odoo rebate lookup failed",
            message=f"relation={relation} ids={ids}",
        )
        rows = []
    for row in rows:
        try:
            rid = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if rid:
            mapping[rid] = row.get("display_name") or row.get("name") or ""
    if mapping:
        return mapping
    try:
        rows = c.read(relation, ids, ["display_name", "name"])
    except Exception:
        return {}
    for row in rows:
        try:
            rid = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if rid:
            mapping[rid] = row.get("display_name") or row.get("name") or ""
    return mapping


def _get_m2o_relation(c, model: str, fieldname: str) -> str:
    try:
        meta = c.fields_get(model, [fieldname])
    except Exception:
        meta = {}
    field_meta = meta.get(fieldname) if isinstance(meta, dict) else {}
    if isinstance(field_meta, dict):
        relation = field_meta.get("relation") or ""
        if relation:
            return relation

    try:
        rows = c.search_read(
            model="ir.model.fields",
            domain=[["model", "=", model], ["name", "=", fieldname]],
            fields=["relation"],
            limit=1,
        )
    except Exception:
        rows = []
    if rows:
        return (rows[0].get("relation") or "").strip()
    if model == "res.partner" and fieldname == "rebate_name":
        return "rebate.configuration"
    return ""


def _normalize_phone(value: str) -> str:
    if not value:
        return ""
    raw = str(value)
    for sep in ["/", ";", "|", ","]:
        if sep in raw:
            raw = raw.split(sep)[0]
            break
    raw = raw.strip()
    raw = re.sub(r"[()\-]", "", raw)
    raw = raw.replace(" ", "")
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if raw.startswith("+"):
        rest = "".join(ch for ch in raw[1:] if ch.isdigit())
        return "+" + rest[:15]
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[:15]


def _normalize_email(value: str) -> str:
    if not value:
        return ""
    cleaned = value.strip().rstrip(".,;")
    return cleaned


def _safe_email(value: str) -> str:
    cleaned = _normalize_email(value)
    if not cleaned:
        return ""
    if validate_email_address(cleaned, throw=False):
        return cleaned
    return ""


def _make_short_name(prefix: str, odoo_id: int, extra: str = "") -> str:
    base = f"{prefix}-{int(odoo_id)}"
    if extra:
        extra = re.sub(r"[^A-Za-z0-9 _-]+", "", extra).strip()
        if extra:
            base = f"{base}-{extra}"
    return base[:MAX_DOCNAME]


def _safe_customer_name(name: str, odoo_id: int) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return str(odoo_id)
    if frappe.db.exists("Customer Group", cleaned):
        suffix = f" ({int(odoo_id)})"
        return (cleaned + suffix)[:MAX_DOCNAME]
    return cleaned[:MAX_DOCNAME]


def _find_address_by_odoo_partner(odoo_partner_id: int, address_type: str):
    meta = frappe.get_meta("Address")
    if meta.has_field("odoo_partner_id"):
        return frappe.db.get_value(
            "Address",
            {"odoo_partner_id": odoo_partner_id, "address_type": address_type},
            "name",
        )
    return None


def _ensure_address_link(addr, customer_doc):
    if not hasattr(addr, "links"):
        return
    for row in (addr.links or []):
        if row.link_doctype == "Customer" and row.link_name == customer_doc.name:
            return
    addr.append("links", {"link_doctype": "Customer", "link_name": customer_doc.name})


def _upsert_customer(r: dict):
    odoo_id = int(r.get("id") or 0)
    if not odoo_id:
        return None

    meta = frappe.get_meta("Customer")
    if not meta.has_field("custom_odoo_id"):
        frappe.throw("Customer doctype is missing custom field `custom_odoo_id` (Int, Unique).")

    existing = frappe.db.get_value("Customer", {"custom_odoo_id": odoo_id}, "name")
    if not existing and frappe.db.exists("Customer", str(odoo_id)):
        existing = str(odoo_id)

    if existing:
        cust = frappe.get_doc("Customer", existing)
        if not getattr(cust, "custom_odoo_id", None):
            cust.custom_odoo_id = odoo_id
    else:
        cust = frappe.new_doc("Customer")
        cust.custom_odoo_id = odoo_id
        cust.flags.ignore_autoname = True
        cust.name = str(odoo_id)

        if hasattr(cust, "customer_group") and not getattr(cust, "customer_group", None):
            cust.customer_group = "All Customer Groups"
        if hasattr(cust, "territory") and not getattr(cust, "territory", None):
            cust.territory = "All Territories"
        if hasattr(cust, "customer_type") and not getattr(cust, "customer_type", None):
            cust.customer_type = "Company" if (r.get("company_type") == "company") else "Individual"

    cust.customer_name = _safe_customer_name(r.get("name"), odoo_id)
    if hasattr(cust, "disabled"):
        cust.disabled = 0 if r.get("active", True) else 1
    if hasattr(cust, "tax_id"):
        cust.tax_id = r.get("vat") or ""

    category_name = (r.get("category_display_name") or "").strip() or _first_category_name(
        r.get("category_id")
    )
    if category_name and frappe.db.exists("Customer Group", category_name):
        cust.customer_group = category_name
    else:
        category_id = _first_category_id(r.get("category_id"))
        if category_id:
            meta = frappe.get_meta("Customer Group")
            fieldname = ""
            if meta.has_field("custom_odoo_id"):
                fieldname = "custom_odoo_id"
            elif meta.has_field("odoo_id"):
                fieldname = "odoo_id"
            if fieldname:
                group_name = frappe.db.get_value("Customer Group", {fieldname: category_id}, "name")
                if group_name:
                    cust.customer_group = group_name

    sales_user_name = _m2o_display_name(r.get("user_id"))
    if hasattr(cust, "custom_salesperson") and sales_user_name:
        user = _find_user_by_full_name(sales_user_name)
        if user:
            cust.custom_salesperson = user

    sales_staff_name = (r.get("x_studio_sales_staff") or "").strip()
    if hasattr(cust, "custom_sales_staff"):
        employee = _find_employee_by_name(sales_staff_name)
        if not employee and sales_user_name:
            employee = _find_employee_by_user(_find_user_by_full_name(sales_user_name))
        if employee:
            cust.custom_sales_staff = employee

    rebate_value = None
    has_rebate = False
    if hasattr(cust, "custom_rebate"):
        has_rebate = bool(
            r.get("rebate_percentage")
            or r.get("rebate_display_name")
            or r.get("rebate_name")
        )
        rebate_value = _coerce_percent(r.get("rebate_percentage"))
        if not rebate_value:
            rebate_value = _parse_percent(r.get("rebate_display_name") or r.get("rebate_name"))
        if not rebate_value and (r.get("rebate_display_name") or "").strip():
            rebate_value = 22.0
        if has_rebate:
            cust.custom_rebate = rebate_value

    if hasattr(cust, "custom_old_customer_no"):
        cust.custom_old_customer_no = (r.get("x_studio_old_customer_no") or "").strip()

    if hasattr(cust, "custom_language"):
        lang_code = (r.get("lang") or "").strip()
        lang_name = _find_language_name(lang_code)
        if lang_name:
            cust.custom_language = lang_name

    if hasattr(cust, "custom_partner_type"):
        cust.custom_partner_type = (r.get("x_studio_partner_type") or "").strip()

    if hasattr(cust, "custom_old_rec"):
        cust.custom_old_rec = 1 if r.get("x_studio_old_rec") else 0

    cust.save(ignore_permissions=True)
    if hasattr(cust, "custom_rebate") and rebate_value is not None and has_rebate:
        if (cust.get("custom_rebate") or 0) != rebate_value:
            frappe.db.set_value(
                "Customer",
                cust.name,
                "custom_rebate",
                rebate_value,
                update_modified=False,
            )
        try:
            frappe.db.sql(
                "update `tabCustomer` set custom_rebate=%s where name=%s",
                (rebate_value, cust.name),
            )
        except Exception:
            pass
        stored_value = frappe.db.get_value("Customer", cust.name, "custom_rebate")
        if stored_value != rebate_value:
            frappe.log_error(
                title="Odoo Customer Rebate: value not persisted",
                message=(
                    f"Customer={cust.name} odoo_id={r.get('id')}\n"
                    f"expected={rebate_value} stored={stored_value}\n"
                ),
            )
    return cust


def _upsert_address(r: dict, customer_doc):
    if not customer_doc:
        return None

    odoo_id = int(r.get("id") or 0)
    if not odoo_id:
        return None

    address_type = "Billing"
    existing = _find_address_by_odoo_partner(odoo_id, address_type)
    if existing:
        addr = frappe.get_doc("Address", existing)
    else:
        addr = frappe.new_doc("Address")
        addr.flags.ignore_autoname = True
        addr.address_type = address_type
        addr.address_title = f"{customer_doc.customer_name[:80]}"
        addr.name = _make_short_name("ODOO-ADDR", odoo_id, address_type)

    addr.address_line1 = (r.get("street") or "").strip()
    addr.address_line2 = (r.get("street2") or "").strip()
    if not addr.address_line1:
        addr.address_line1 = addr.address_line2 or customer_doc.customer_name or "N/A"

    addr.city = (r.get("city") or _m2o_display_name(r.get("city_id")) or "").strip()
    if not addr.city:
        addr.city = "Undefined"

    state_name = (_m2o_display_name(r.get("state_id")) or "").strip()
    addr.state = state_name

    country_name = (_m2o_display_name(r.get("country_id")) or "").strip()
    if not country_name:
        country_code = (r.get("country_code") or "").strip()
        if country_code:
            country_name = frappe.db.get_value("Country", {"code": country_code}, "name") or ""
    if not country_name:
        country_name = "United Arab Emirates" if frappe.db.exists("Country", "United Arab Emirates") else ""
    if not country_name:
        return None
    addr.country = country_name

    addr.pincode = r.get("zip") or ""
    if hasattr(addr, "email_id"):
        addr.email_id = _safe_email(r.get("email") or "")
    if hasattr(addr, "phone"):
        addr.phone = _normalize_phone(r.get("phone") or r.get("mobile") or "")
    if hasattr(addr, "odoo_partner_id"):
        addr.odoo_partner_id = odoo_id

    _ensure_address_link(addr, customer_doc)
    addr.save(ignore_permissions=True)
    return addr


@frappe.whitelist()
def fetch_customers_raw(limit: int = 0, batch_size: int = 1000, run_async: int = 0):
    if not frappe.has_permission("Customer", "write"):
        frappe.throw("Not permitted to update Customers.")

    if int(run_async or 0):
        job = frappe.enqueue(
            "odoo_xml_rpc.api.odoo_customer_fetch._run_fetch_customers_raw",
            queue="long",
            job_name="fetch_customers_raw",
            limit=limit,
            batch_size=batch_size,
        )
        return {"job_id": job.id}

    return _run_fetch_customers_raw(limit=limit, batch_size=batch_size)


@frappe.whitelist()
def preview_customer_sync(sample_name: str = ""):
    if not frappe.has_permission("Customer", "read"):
        frappe.throw("Not permitted to read Customers.")

    c = get_client()
    fields = [
        "id", "name", "active", "write_date",
        "company_type",
        "vat",
        "street", "street2", "city", "zip",
        "state_id", "country_id", "city_id",
        "phone", "mobile", "email",
        "customer_rank",
        "x_studio_partner_type",
        "country_code",
        "category_id",
        "user_id",
        "x_studio_sales_staff",
        "rebate_name",
        "x_studio_old_customer_no",
        "x_studio_old_rec",
        "lang",
    ]
    domain = ["|", ["customer_rank", ">", 0], ["x_studio_partner_type", "=", "Customer"]]
    if sample_name:
        domain = ["&", "|", ["customer_rank", ">", 0], ["x_studio_partner_type", "=", "Customer"], ["name", "ilike", sample_name]]

    rows = c.search_read(
        model="res.partner",
        domain=domain,
        fields=fields,
        limit=1,
        offset=0,
        order="id asc",
        load=False,
    )
    if not rows:
        return {"odoo_raw": None, "converted": None}

    r = dict(rows[0])
    partner_id = int(r.get("id") or 0)
    if partner_id:
        try:
            rebate_rows = c.web_read("res.partner", [partner_id], ["rebate_name"])
        except Exception:
            rebate_rows = []
        if rebate_rows and isinstance(rebate_rows[0], dict):
            rebate_display = _m2o_display_name(rebate_rows[0].get("rebate_name"))
            if rebate_display:
                r["rebate_display_name"] = rebate_display
        if "rebate_display_name" not in r:
            try:
                rebate_rows = c.read("res.partner", [partner_id], ["rebate_name"])
            except Exception:
                rebate_rows = []
            if rebate_rows and isinstance(rebate_rows[0], dict):
                rebate_display = _m2o_display_name(rebate_rows[0].get("rebate_name"))
                if rebate_display:
                    r["rebate_display_name"] = rebate_display
    rebate_id = _collect_m2o_ids([r], "rebate_name")
    if rebate_id:
        name_map, pct_map = _fetch_rebate_config(c, rebate_id)
        rebate_display = name_map.get(rebate_id[0], "")
        if rebate_display:
            r["rebate_display_name"] = rebate_display
        if rebate_id[0] in pct_map:
            r["rebate_percentage"] = pct_map.get(rebate_id[0])
    category_name = _first_category_name(r.get("category_id"))
    if not category_name:
        category_id = _first_category_id(r.get("category_id"))
        if category_id:
            name_map = _fetch_name_map(c, "res.partner.category", [category_id])
            category_name = name_map.get(category_id, "")
    if category_name:
        r["category_display_name"] = category_name

    rebate_name = ""
    if isinstance(r.get("rebate_name"), dict):
        rebate_name = r.get("rebate_name", {}).get("display_name") or ""
    elif isinstance(r.get("rebate_name"), (list, tuple)) and len(r.get("rebate_name")) >= 2:
        rebate_name = r.get("rebate_name")[1] or ""
    else:
        rebate_id = _collect_m2o_ids([r], "rebate_name")
        if rebate_id:
            name_map, pct_map = _fetch_rebate_config(c, rebate_id)
            rebate_name = name_map.get(rebate_id[0], "")
            if rebate_id[0] in pct_map:
                r["rebate_percentage"] = pct_map.get(rebate_id[0])
    if rebate_name:
        r["rebate_display_name"] = rebate_name

    return {"odoo_raw": r, "converted": _build_customer_preview(r)}


def _run_fetch_customers_raw(limit: int = 0, batch_size: int = 1000):
    """
    Fetch customers from Odoo and return raw data without storing.
    """
    c = get_client()

    fields = [
        "id", "name", "active", "write_date",
        "company_type",
        "vat",
        "street", "street2", "city", "zip",
        "state_id", "country_id", "city_id",
        "phone", "mobile", "email",
        "customer_rank",
        "x_studio_partner_type",
        "country_code",
        "category_id",
        "user_id",
        "x_studio_sales_staff",
        "rebate_name",
        "x_studio_old_customer_no",
        "x_studio_old_rec",
        "lang",
    ]

    domain = ["|", ["customer_rank", ">", 0], ["x_studio_partner_type", "=", "Customer"]]
    limit = int(limit or 0)
    batch_size = int(batch_size or 1000)
    if batch_size <= 0:
        batch_size = 1000

    rows = []
    offset = 0
    while True:
        batch_limit = batch_size
        if limit:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            batch_limit = min(batch_limit, remaining)

        batch = c.search_read(
            model="res.partner",
            domain=domain,
            fields=fields,
            limit=batch_limit,
            offset=offset,
            order="id asc",
            load=False,
        )
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)

    partner_ids = [int(r.get("id") or 0) for r in rows if int(r.get("id") or 0)]
    rebate_by_partner = {}
    if partner_ids:
        try:
            rebate_rows = c.web_read("res.partner", partner_ids, ["rebate_name"])
        except Exception:
            rebate_rows = []
        for row in rebate_rows or []:
            try:
                rid = int(row.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if rid:
                rebate_by_partner[rid] = _m2o_display_name(row.get("rebate_name"))
        missing_ids = [pid for pid in partner_ids if not rebate_by_partner.get(pid)]
        if missing_ids:
            try:
                rebate_rows = c.read("res.partner", missing_ids, ["rebate_name"])
            except Exception:
                rebate_rows = []
            for row in rebate_rows or []:
                try:
                    rid = int(row.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                if rid:
                    rebate_by_partner[rid] = _m2o_display_name(row.get("rebate_name"))
    rebate_ids_direct = _collect_m2o_ids(rows, "rebate_name")
    rebate_by_id = {}
    rebate_pct_by_id = {}
    if rebate_ids_direct:
        rebate_by_id, rebate_pct_by_id = _fetch_rebate_config(c, rebate_ids_direct)

    city_ids = _collect_m2o_ids(rows, "city_id")
    state_ids = _collect_m2o_ids(rows, "state_id")
    country_ids = _collect_m2o_ids(rows, "country_id")
    category_ids = []
    for r in rows:
        cid = _first_category_id(r.get("category_id"))
        if cid:
            category_ids.append(cid)
    rebate_ids = _collect_m2o_ids(rows, "rebate_name")

    city_map = _fetch_name_map(c, "res.city", city_ids)
    state_map = _fetch_name_map(c, "res.country.state", state_ids)
    country_map = _fetch_name_map(c, "res.country", country_ids)
    category_map = _fetch_name_map(c, "res.partner.category", category_ids)
    rebate_map, rebate_pct_by_id = _fetch_rebate_config(c, rebate_ids)

    items = []
    created_customers = 0
    created_addresses = 0
    for r in rows:
        r = dict(r)
        city_val = r.get("city_id")
        state_val = r.get("state_id")
        country_val = r.get("country_id")

        city_name = _m2o_display_name(city_val) or city_map.get(int(city_val or 0), "")
        state_name = _m2o_display_name(state_val) or state_map.get(int(state_val or 0), "")
        country_name = _m2o_display_name(country_val) or country_map.get(int(country_val or 0), "")
        category_name = _first_category_name(r.get("category_id")) or category_map.get(
            _first_category_id(r.get("category_id")), ""
        )
        rebate_name = ""
        if isinstance(r.get("rebate_name"), dict):
            rebate_name = r.get("rebate_name", {}).get("display_name") or ""
        elif isinstance(r.get("rebate_name"), (list, tuple)) and len(r.get("rebate_name")) >= 2:
            rebate_name = r.get("rebate_name")[1] or ""
        else:
            rebate_id = _collect_m2o_ids([r], "rebate_name")
            if rebate_id:
                rebate_name = rebate_map.get(rebate_id[0], "")
        if not rebate_name:
            rebate_name = rebate_by_partner.get(int(r.get("id") or 0), "")
        if not rebate_name:
            rebate_ids = _collect_m2o_ids([r], "rebate_name")
            if rebate_ids:
                rebate_name = rebate_by_id.get(rebate_ids[0], "")
        if rebate_name:
            r["rebate_display_name"] = rebate_name
        rebate_id_val = int(r.get("rebate_name") or 0)
        if rebate_id_val in rebate_pct_by_id:
            r["rebate_percentage"] = rebate_pct_by_id.get(rebate_id_val)

        r["city_display_name"] = city_name
        r["state_display_name"] = state_name
        r["country_display_name"] = country_name
        r["category_display_name"] = category_name
        if rebate_name:
            r["rebate_display_name"] = rebate_name

        cust = _upsert_customer(r)
        if cust:
            created_customers += 1
        addr = _upsert_address(r, cust)
        if addr:
            created_addresses += 1

        items.append(r)

    return {
        "fetched": len(items),
        "created_customers": created_customers,
        "created_addresses": created_addresses,
        "items": items,
    }


@frappe.whitelist()
def get_fetch_customers_job_status(job_id: str):
    if not frappe.has_permission("Customer", "read"):
        frappe.throw("Not permitted to read Customers.")

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
