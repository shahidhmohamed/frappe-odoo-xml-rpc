import frappe
from odoo_xml_rpc.integrations.odoo_client import get_client
from frappe.utils import validate_email_address
import re
# -------------------------------------------------------------------
# Odoo -> Frappe (ERPNext) Customers + Addresses + Link (Dynamic Link)
# -------------------------------------------------------------------
# Requires (recommended):
#   Customer: custom field `custom_odoo_id` (Int, Unique)
#   Address:  custom field `odoo_partner_id` (Int, Index)
#
# Links Address -> Customer using Address.links (child table: Dynamic Link)
# Maps your fields:
#   street  -> address_line1
#   street2 -> address_line2
#   city or city_id.display_name -> city
#   state_id.display_name -> state
#   country_id.display_name -> country
#   zip -> pincode
# -------------------------------------------------------------------

MAX_DOCNAME = 140


def _safe_set(doc, fieldname: str, value):
    if hasattr(doc, fieldname):
        setattr(doc, fieldname, value)


def _m2o_name(v):
    """
    Odoo many2one might be:
      - [id, "Name"]
      - {"id": 1, "display_name": "Name"}   (as in your sample)
      - False / None
    """
    if not v:
        return ""
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return v[1] or ""
    if isinstance(v, dict):
        return v.get("display_name") or ""
    return ""


def _derive_emirate_from_state(state_name: str) -> str:
    # "Sharjah (AE)" -> "Sharjah"
    if not state_name:
        return ""
    return state_name.split("(")[0].strip()


def _normalize_phone(value: str) -> str:
    if not value:
        return ""
    cleaned = value.replace("\u202a", "").replace("\u202c", "")
    cleaned = cleaned.replace("\u202b", "").replace("\u202d", "").replace("\u202e", "")
    cleaned = cleaned.replace("\u2066", "").replace("\u2067", "").replace("\u2068", "").replace("\u2069", "")
    cleaned = cleaned.replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("+"):
        rest = "".join(ch for ch in cleaned[1:] if ch.isdigit())
        return "+" + rest
    return "".join(ch for ch in cleaned if ch.isdigit())


def _get_country_name_from_odoo(r: dict) -> str:
    # Odoo gives country_id as dict/list -> "United Arab Emirates"
    return (_m2o_name(r.get("country_id")) or (r.get("country") or "")).strip()


def _normalize_country_for_erpnext(country_name: str) -> str:
    """
    Address.country is a Link to Country.
    We try to match an existing Country by:
      - exact name
      - country_name='XX' or name='XX'
    If not found, return original (save may fail if it doesn't exist).
    """
    if not country_name:
        return ""

    # Exact match on name
    if frappe.db.exists("Country", country_name):
        return country_name

    # Try match on common fields
    found = frappe.db.get_value(
        "Country",
        {"country_name": country_name},
        "name",
    )
    if found:
        return found

    # Some setups store name as full name already; nothing else to do.
    return country_name


# ----------------------------
# Customer upsert
# ----------------------------

def _upsert_customer(r: dict):
    custom_odoo_id = r.get("id")
    if not custom_odoo_id:
        return None
    custom_odoo_id = int(custom_odoo_id)

    customer_name = (r.get("name") or "").strip()
    if not customer_name:
        return None

    meta = frappe.get_meta("Customer")
    if not meta.has_field("custom_odoo_id"):
        frappe.throw("Customer doctype is missing custom field `custom_odoo_id` (Int, Unique).")

    existing = frappe.db.get_value("Customer", {"custom_odoo_id": custom_odoo_id}, "name")
    if not existing and frappe.db.exists("Customer", str(custom_odoo_id)):
        existing = str(custom_odoo_id)
    if not existing:
        # If VAT matches an existing Customer, attach the Odoo ID to prevent duplicates.
        vat = (r.get("vat") or "").strip()
        if vat and meta.has_field("tax_id"):
            match = frappe.db.get_value("Customer", {"tax_id": vat}, "name")
            if match:
                existing = match

    if existing:
        cust = frappe.get_doc("Customer", existing)
        if not getattr(cust, "custom_odoo_id", None):
            cust.custom_odoo_id = custom_odoo_id
    else:
        cust = frappe.new_doc("Customer")
        cust.custom_odoo_id = custom_odoo_id
        cust.flags.ignore_autoname = True
        cust.name = str(custom_odoo_id)

        # Defaults (adjust if you want)
        if hasattr(cust, "customer_group") and not getattr(cust, "customer_group", None):
            cust.customer_group = "All Customer Groups"
        if hasattr(cust, "territory") and not getattr(cust, "territory", None):
            cust.territory = "All Territories"
        if hasattr(cust, "customer_type") and not getattr(cust, "customer_type", None):
            cust.customer_type = "Company" if (r.get("company_type") == "company") else "Individual"

    cust.customer_name = customer_name[:140]


    # Optional mappings (only if field exists)
    _safe_set(cust, "tax_id", r.get("vat"))
    _safe_set(cust, "odoo_write_date", r.get("write_date"))

    if hasattr(cust, "disabled"):
        cust.disabled = 0 if r.get("active", True) else 1

    cust.save(ignore_permissions=True)
    return cust


# ----------------------------
# Address upsert + link
# ----------------------------

def _find_address_by_odoo_partner(odoo_partner_id: int, address_type: str):
    meta = frappe.get_meta("Address")
    if meta.has_field("odoo_partner_id"):
        return frappe.db.get_value(
            "Address",
            {"odoo_partner_id": odoo_partner_id, "address_type": address_type},
            "name",
        )
    return None


def _find_address_by_customer(customer_doc, address_type: str):
    rows = frappe.db.get_all(
        "Dynamic Link",
        filters={
            "link_doctype": "Customer",
            "link_name": customer_doc.name,
            "parenttype": "Address",
        },
        fields=["parent"],
    )
    if not rows:
        return None
    parent_names = [r.parent for r in rows if r.parent]
    if not parent_names:
        return None
    return frappe.db.get_value(
        "Address",
        {"name": ["in", parent_names], "address_type": address_type},
        "name",
    )


def _ensure_address_link(addr, customer_doc):
    # Address.links is a table of Dynamic Link rows
    if not hasattr(addr, "links"):
        return

    for row in (addr.links or []):
        if row.link_doctype == "Customer" and row.link_name == customer_doc.name:
            return

    addr.append("links", {"link_doctype": "Customer", "link_name": customer_doc.name})


def _upsert_address(r: dict, customer_doc):
    if not customer_doc:
        return None

    odoo_partner_id = int(r.get("id") or 0)
    if not odoo_partner_id:
        return None

    # You can change this to "Office" if you prefer
    address_type = "Billing"

    addr_meta = frappe.get_meta("Address")
    has_odoo_partner_id = addr_meta.has_field("odoo_partner_id")

    if has_odoo_partner_id:
        existing_addr_name = _find_address_by_odoo_partner(odoo_partner_id, address_type)
    else:
        existing_addr_name = _find_address_by_customer(customer_doc, address_type)

    if existing_addr_name:
        addr = frappe.get_doc("Address", existing_addr_name)
    else:
        addr = frappe.new_doc("Address")
        addr.address_type = address_type

        # Keep title readable but not crazy long
        safe_title = (customer_doc.customer_name or "")[:80]
        addr.address_title = f"{safe_title} - {address_type}"[:140]

        # ✅ force short safe docname
        addr.name = _make_short_name("ODOO-ADDR", odoo_partner_id, address_type)

    # --- Your exact Odoo fields mapping ---
    addr.address_line1 = (r.get("street") or "").strip()
    if not addr.address_line1:
        # Skip address creation if required address line 1 is missing
        return None

    addr.address_line2 = (r.get("street2") or "").strip()

    # Prefer 'city' if present, else city_id.display_name
    addr.city = (r.get("city") or _m2o_name(r.get("city_id")) or "").strip()
    if not addr.city:
        # Skip address creation if required city is missing
        return None

    # state_id.display_name
    state_name = (_m2o_name(r.get("state_id")) or (r.get("state") or "")).strip()
    addr.state = state_name

    # country_id.display_name -> Country link name
    country_name = _get_country_name_from_odoo(r)
    if not country_name:
        country_code = (r.get("country_code") or "").strip()
        if country_code:
            country_name = (
                frappe.db.get_value("Country", {"code": country_code}, "name")
                or frappe.db.get_value("Country", {"country_code": country_code}, "name")
            )

    if not country_name:
        # Skip address creation if required country is missing
        return None

    addr.country = _normalize_country_for_erpnext(country_name)

    # zip false -> ""
    addr.pincode = r.get("zip") or ""

    # Optional standard fields (clean + safe)
    if hasattr(addr, "email_id"):
        addr.email_id = _clean_email(r.get("email") or "")

    if hasattr(addr, "phone"):
        # Handles cases like: "+971 50 644 7821/ 055 511 5828" (keeps first valid)
        addr.phone = _normalize_phone(r.get("phone") or r.get("mobile") or "")

    # Your custom field: emirate (Select) if exists
    if hasattr(addr, "emirate"):
        addr.emirate = _derive_emirate_from_state(state_name)

    # Store Odoo partner id on Address if custom field exists
    if has_odoo_partner_id:
        addr.odoo_partner_id = odoo_partner_id

    # Link Address -> Customer
    _ensure_address_link(addr, customer_doc)

    # Save with validation fallback so one bad email/phone doesn't kill the sync
    try:
        addr.save(ignore_permissions=True)
    except Exception as e:
        msg = str(e)

        # If email invalid, clear it and retry once
        if "valid Email Address" in msg and hasattr(addr, "email_id"):
            addr.email_id = ""
            addr.save(ignore_permissions=True)
            return addr

        # If phone invalid, clear it and retry once
        if "valid Phone Number" in msg and hasattr(addr, "phone"):
            addr.phone = ""
            addr.save(ignore_permissions=True)
            return addr

        # Any other error should bubble up
        raise

    return addr

# ----------------------------
# Public sync function
# ----------------------------

@frappe.whitelist()
def sync_customers_with_addresses(limit: int = 5000):
    """
    Fetch Odoo customers and store into:
      - Customer
      - Address
    Link Address -> Customer via Address.links (Dynamic Link)

    Domain used: customer_rank > 0 OR x_studio_partner_type = Customer
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

    rows = c.search_read(
        model="res.partner",
        domain=["|", ["customer_rank", ">", 0], ["x_studio_partner_type", "=", "Customer"]],
        fields=fields,
        limit=int(limit),
        order="id asc",
    )

    synced_customers = 0
    synced_addresses = 0
    items = []

    for r in rows:
        cust = _upsert_customer(r)
        if not cust:
            continue

        synced_customers += 1

        addr = _upsert_address(r, cust)
        if addr:
            synced_addresses += 1

        items.append({
            "odoo_partner_id": int(r.get("id") or 0),
            "customer": cust.name,
            "address": addr.name if addr else None,
        })

    frappe.db.commit()

    return {
        "fetched": len(rows),
        "synced_customers": synced_customers,
        "synced_addresses": synced_addresses,
        "items": items,
    }


def _clean_email(value: str) -> str:
    if not value:
        return ""

    e = (value or "").strip()

    # common junk characters / separators
    e = e.replace("\u202a", "").replace("\u202c", "")
    e = e.replace("\u2066", "").replace("\u2067", "").replace("\u2068", "").replace("\u2069", "")

    # remove trailing dots (your exact case: "...uae.")
    e = e.rstrip(".")

    # if multiple emails come in one field, keep the first
    if "," in e:
        e = e.split(",")[0].strip()
    if ";" in e:
        e = e.split(";")[0].strip()

    # validate; return blank if invalid (so sync doesn't crash)
    try:
        validate_email_address(e, throw=True)
        return e
    except Exception:
        return ""
    
def _normalize_phone(value: str) -> str:
    """
    Handles:
      "+971 50 644 7821/ 055 511 5828"  -> "+971506447821" (keeps first number)
      "055 511 5828"                   -> "0555115828"
      "00..."                          -> "+..."
    Strategy:
      - split on common separators (/, ;, , , |, 'and')
      - clean each candidate
      - return the first plausible candidate
    """
    if not value:
        return ""

    s = str(value)

    # remove direction marks / hidden unicode
    for ch in ["\u202a", "\u202c", "\u202b", "\u202d", "\u202e",
               "\u2066", "\u2067", "\u2068", "\u2069"]:
        s = s.replace(ch, "")

    # split if multiple numbers are present
    parts = re.split(r"[\/,;|]|(?:\s+and\s+)", s, flags=re.IGNORECASE)
    parts = [p.strip() for p in parts if p and p.strip()]

    def clean_one(p: str) -> str:
        p = p.strip()
        # keep + if present, drop everything else non-digit
        if p.startswith("00"):
            p = "+" + p[2:]
        if p.startswith("+"):
            return "+" + "".join(ch for ch in p[1:] if ch.isdigit())
        return "".join(ch for ch in p if ch.isdigit())

    # choose first “reasonable length” candidate after cleaning
    for p in parts:
        c = clean_one(p)
        digits_len = len(c[1:]) if c.startswith("+") else len(c)
        if 7 <= digits_len <= 15:   # typical phone length bounds
            return c

    # fallback: clean whole string (better than crash)
    return clean_one(s)




def _slug(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^A-Za-z0-9 _-]+", "", s)
    return s.strip()

def _make_short_name(prefix: str, odoo_id: int, extra: str = "") -> str:
    """
    Build a stable, short docname <= 140 chars.
    Example: "ODOO-CUST-12345" or "ODOO-ADDR-12345-Billing"
    """
    base = f"{prefix}-{int(odoo_id)}"
    if extra:
        base = f"{base}-{_slug(extra)}"

    if len(base) <= MAX_DOCNAME:
        return base

    # hard truncate if still too long
    return base[:MAX_DOCNAME]
