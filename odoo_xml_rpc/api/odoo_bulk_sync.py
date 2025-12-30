import base64
import hashlib
import frappe
from odoo_xml_rpc.integrations.odoo_client import get_client

IMAGE_FIELDS_PRIORITY = ["image_1920", "image_1024", "image_512", "image_256", "image_128"]


def _pick_any_image(row: dict):
    for f in IMAGE_FIELDS_PRIORITY:
        v = row.get(f)
        if v:
            return v
    return None


def _b64_hash(image_b64: str) -> str:
    try:
        raw = base64.b64decode(image_b64)
    except Exception:
        return ""
    return hashlib.sha256(raw).hexdigest()


def _attach_image_to_doc(doc, image_b64: str, overwrite: int = 0, only_if_changed: int = 1):
    """
    Save image to File and set doc.product_image = file_url
    - overwrite=1: always replace existing image
    - only_if_changed=1: compare hash and skip if same
    """
    if not image_b64:
        return
    if not hasattr(doc, "product_image"):
        return

    new_hash = _b64_hash(image_b64)
    if not new_hash:
        return

    existing_hash = getattr(doc, "product_image_hash", None)

    # If image already same and we are not forcing overwrite -> skip
    if only_if_changed and existing_hash == new_hash and doc.product_image and not overwrite:
        return

    # Delete old attached File rows (optional but keeps things clean)
    old_files = frappe.db.get_all(
        "File",
        filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
        fields=["name"],
    )
    for f in old_files:
        try:
            frappe.delete_doc("File", f["name"], ignore_permissions=True, force=True)
        except Exception:
            pass

    try:
        content = base64.b64decode(image_b64)
    except Exception:
        return

    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": f"odoo_product_{doc.odoo_id}.png",
        "attached_to_doctype": doc.doctype,
        "attached_to_name": doc.name,
        "content": content,
        "is_private": 0,
    })
    file_doc.save(ignore_permissions=True)

    doc.product_image = file_doc.file_url

    if hasattr(doc, "product_image_hash"):
        doc.product_image_hash = new_hash

    doc.save(ignore_permissions=True)


def _upsert_odoo_product(r: dict):
    """
    r contains: id, name, write_date, active, sale_ok,
                default_code, barcode, list_price,
                x_studio_brand, company_id, sales_count, qty_available,
                images...
    """
    odoo_id = r.get("id")
    if not odoo_id:
        return None

    odoo_id = int(odoo_id)
    pname = (r.get("name") or "").strip()

    existing_name = frappe.db.get_value("Odoo Products", {"odoo_id": odoo_id}, "name")
    if existing_name:
        doc = frappe.get_doc("Odoo Products", existing_name)
    else:
        doc = frappe.new_doc("Odoo Products")
        doc.odoo_id = odoo_id

    # Main fields
    if hasattr(doc, "product_name"):
        doc.product_name = pname

    # Mirror Odoo state
    wdate = r.get("write_date")
    active = int(bool(r.get("active", True)))
    sale_ok = int(bool(r.get("sale_ok", True)))

    if hasattr(doc, "odoo_write_date"):
        doc.odoo_write_date = wdate
    if hasattr(doc, "odoo_active"):
        doc.odoo_active = active
    if hasattr(doc, "odoo_sale_ok"):
        doc.odoo_sale_ok = sale_ok

    # Optional business fields
    if hasattr(doc, "default_code"):
        doc.default_code = r.get("default_code")
    if hasattr(doc, "barcode"):
        doc.barcode = r.get("barcode")
    if hasattr(doc, "list_price"):
        doc.list_price = r.get("list_price")

    # NEW fields
    if hasattr(doc, "x_studio_brand"):
        doc.x_studio_brand = r.get("x_studio_brand")

    # company_id may come as [id, "Company Name"] or just id
    comp = r.get("company_id")
    comp_id = None
    if isinstance(comp, (list, tuple)) and comp:
        comp_id = comp[0]
    else:
        comp_id = comp

    if hasattr(doc, "company_id"):
        doc.company_id = comp_id

    if hasattr(doc, "sales_count"):
        doc.sales_count = r.get("sales_count") or 0

    if hasattr(doc, "qty_available"):
        doc.qty_available = r.get("qty_available") or 0

    doc.save(ignore_permissions=True)
    return doc


def _get_settings():
    return frappe.get_single("Odoo Sync Settings")


@frappe.whitelist()
def sync_products_incremental():
    """
    Incremental sync using cursor (write_date, id).
    Safe paging rule:
      (write_date > last_date) OR (write_date = last_date AND id > last_id)
    """
    settings = _get_settings()

    limit = int(settings.sync_limit or 200)
    with_images = int(settings.sync_with_images or 0)
    overwrite_images = int(settings.overwrite_images or 0)
    only_if_changed = int(settings.only_update_images_if_changed or 1)

    last_date = settings.last_product_sync_write_date  # Datetime
    last_id = int(settings.last_product_sync_last_id or 0)

    c = get_client()

    fields = [
        "id", "name", "write_date",
        "active", "sale_ok",
        "default_code", "barcode", "list_price",

        # NEW:
        "x_studio_brand",
        "company_id",
        "sales_count",
        "qty_available",
    ]
    if with_images:
        fields += IMAGE_FIELDS_PRIORITY

    total = 0
    items = []

    # Track newest cursor we processed
    max_date = last_date
    max_id = last_id

    while True:
        domain = [["sale_ok", "in", [True, False]]]
        # Note: we don't filter by sale_ok=True because we want to detect it turning False too.

        if last_date:
            domain += [
                "|",
                ["write_date", ">", last_date],
                "&", ["write_date", "=", last_date], ["id", ">", last_id],
            ]

        rows = c.search_read(
            model="product.template",
            domain=domain,
            fields=fields,
            limit=limit,
            order="write_date asc, id asc",
        )

        if not rows:
            break

        for r in rows:
            doc = _upsert_odoo_product(r)

            if doc and with_images:
                img_b64 = _pick_any_image(r)
                if img_b64:
                    _attach_image_to_doc(
                        doc,
                        img_b64,
                        overwrite=overwrite_images,
                        only_if_changed=only_if_changed
                    )

            # advance cursor
            wdate = r.get("write_date")
            oid = int(r.get("id") or 0)

            if wdate:
                if (max_date is None) or (wdate > max_date) or (wdate == max_date and oid > max_id):
                    max_date = wdate
                    max_id = oid

            if doc:
                items.append({
                    "odoo_id": doc.odoo_id,
                    "product_name": getattr(doc, "product_name", None),
                    "odoo_write_date": getattr(doc, "odoo_write_date", None),
                    "product_image": getattr(doc, "product_image", None),

                    "x_studio_brand": getattr(doc, "x_studio_brand", None),
                    "company_id": getattr(doc, "company_id", None),
                    "sales_count": getattr(doc, "sales_count", None),
                    "qty_available": getattr(doc, "qty_available", None),
                })

        frappe.db.commit()
        total += len(rows)

        # Move paging cursor forward for next loop
        last_date = max_date
        last_id = max_id

        if len(rows) < limit:
            break

    # save cursor
    if max_date:
        settings.last_product_sync_write_date = max_date
        settings.last_product_sync_last_id = int(max_id or 0)
        settings.save(ignore_permissions=True)
        frappe.db.commit()

    return {
        "synced": total,
        "cursor_write_date": str(settings.last_product_sync_write_date),
        "cursor_last_id": int(settings.last_product_sync_last_id or 0),
        "items": items,
    }


# Optional: backward compatible endpoint (if old URLs/jobs still call sync_products_bulk)
@frappe.whitelist()
def sync_products_bulk(*args, **kwargs):
    return sync_products_incremental()
