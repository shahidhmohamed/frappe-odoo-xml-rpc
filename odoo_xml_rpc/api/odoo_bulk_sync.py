import base64
import frappe
from odoo_xml_rpc.integrations.odoo_client import get_client

IMAGE_FIELDS_PRIORITY = ["image_1920", "image_1024", "image_512", "image_256", "image_128"]


def _pick_any_image(row: dict):
    """Return the first available Odoo image base64 string, or None."""
    for f in IMAGE_FIELDS_PRIORITY:
        v = row.get(f)
        if v:
            return v
    return None


def _attach_image_to_doc(doc, image_b64: str, overwrite: int = 0):
    """
    Store base64 image as a Frappe File and set doc.product_image to file_url.
    - overwrite=0: do nothing if product_image already set
    - overwrite=1: replace existing (also deletes old File rows for this doc+field)
    """
    if not image_b64:
        return

    if not hasattr(doc, "product_image"):
        return

    if doc.product_image and not overwrite:
        return

    try:
        content = base64.b64decode(image_b64)
    except Exception:
        return

    # If overwriting, delete old File records attached to this doc (optional)
    if overwrite and doc.product_image:
        # remove File rows linked to this doc (keeps disk cleanup separate; OK for dev)
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
        doc.product_image = None
        doc.save(ignore_permissions=True)

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
    doc.save(ignore_permissions=True)


def _upsert_odoo_product(odoo_id: int, pname: str, image_b64: str | None = None, overwrite_image: int = 0):
    if not odoo_id:
        return None

    odoo_id = int(odoo_id)
    pname = (pname or "").strip()

    existing_name = frappe.db.get_value("Odoo Products", {"odoo_id": odoo_id}, "name")

    if existing_name:
        doc = frappe.get_doc("Odoo Products", existing_name)
    else:
        doc = frappe.new_doc("Odoo Products")
        doc.odoo_id = odoo_id

    doc.product_name = pname
    doc.save(ignore_permissions=True)  # ensure doc.name exists

    if image_b64:
        _attach_image_to_doc(doc, image_b64, overwrite=overwrite_image)

    return doc


@frappe.whitelist()
def sync_products_bulk(batch_size=200, max_batches=5, with_images=0, overwrite_image=0):
    batch_size = int(batch_size)
    max_batches = int(max_batches)
    with_images = int(with_images)
    overwrite_image = int(overwrite_image)

    c = get_client()
    total_synced = 0
    offset = 0
    items = []

    fields = ["id", "name"]
    if with_images:
        fields += IMAGE_FIELDS_PRIORITY

    for _ in range(max_batches):
        rows = c.search_read(
            model="product.template",
            domain=[["sale_ok", "=", True]],
            fields=fields,
            limit=batch_size,
            offset=offset,
            order="id asc",
        )

        if not rows:
            break

        for r in rows:
            odoo_id = r.get("id")
            pname = r.get("name")

            if not odoo_id:
                continue

            img_b64 = _pick_any_image(r) if with_images else None

            doc = _upsert_odoo_product(
                odoo_id=int(odoo_id),
                pname=pname,
                image_b64=img_b64,
                overwrite_image=overwrite_image,
            )

            if doc:
                items.append({
                    "odoo_id": doc.odoo_id,
                    "product_name": doc.product_name,
                    "product_image": getattr(doc, "product_image", None),  # this is URL like /files/...
                })

        frappe.db.commit()
        total_synced += len(rows)
        offset += batch_size

    return {"synced": total_synced, "items": items}
