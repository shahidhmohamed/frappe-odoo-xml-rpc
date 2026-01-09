import re
import frappe
from frappe.utils import validate_email_address
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


def _collect_m2o_ids(rows, fieldname: str) -> list[int]:
    ids = []
    for r in rows:
        val = r.get(fieldname)
        if isinstance(val, (int, float)) and int(val) > 0:
            ids.append(int(val))
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

    cust.customer_name = (r.get("name") or "").strip()[:140]
    if hasattr(cust, "disabled"):
        cust.disabled = 0 if r.get("active", True) else 1
    if hasattr(cust, "tax_id"):
        cust.tax_id = r.get("vat") or ""

    cust.save(ignore_permissions=True)
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
        addr.address_title = f"{customer_doc.customer_name[:80]} - {address_type}"[:140]
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
def fetch_customers_raw(limit: int = 0, batch_size: int = 1000):
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
            load=True,
        )
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)

    city_ids = _collect_m2o_ids(rows, "city_id")
    state_ids = _collect_m2o_ids(rows, "state_id")
    country_ids = _collect_m2o_ids(rows, "country_id")

    city_map = _fetch_name_map(c, "res.city", city_ids)
    state_map = _fetch_name_map(c, "res.country.state", state_ids)
    country_map = _fetch_name_map(c, "res.country", country_ids)

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

        r["city_display_name"] = city_name
        r["state_display_name"] = state_name
        r["country_display_name"] = country_name

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
