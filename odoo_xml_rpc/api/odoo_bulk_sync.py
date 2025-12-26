import frappe
from odoo_xml_rpc.integrations.odoo_client import get_client


def _upsert_odoo_product(odoo_id: int, pname: str):
    if not odoo_id:
        return

    docname = str(int(odoo_id))

    if frappe.db.exists("Odoo Products", docname):
        doc = frappe.get_doc("Odoo Products", docname)
    else:
        doc = frappe.new_doc("Odoo Products")
        doc.name = docname            # ← primary key
        doc.odoo_id = int(odoo_id)

    doc.product_name = pname or ""
    doc.save(ignore_permissions=True)


@frappe.whitelist()
def sync_products_bulk(batch_size=200, max_batches=5):
    batch_size = int(batch_size)
    max_batches = int(max_batches)

    c = get_client()
    total_synced = 0
    offset = 0
    synced_items = []

    for _ in range(max_batches):
        rows = c.search_read(
            model="product.template",
            domain=[["sale_ok", "=", True]],
            fields=["id", "name"],
            limit=batch_size,
            offset=offset,
            order="id asc",
        )

        if not rows:
            break

        for r in rows:
            odoo_id = r.get("id")
            pname = (r.get("name") or "").strip()

            if not odoo_id:
                continue

            odoo_id = int(odoo_id)

            # Find existing doc by odoo_id field
            existing_name = frappe.db.get_value("Odoo Products", {"odoo_id": odoo_id}, "name")

            if existing_name:
                doc = frappe.get_doc("Odoo Products", existing_name)
            else:
                doc = frappe.new_doc("Odoo Products")
                # optional: make primary key readable
                doc.name = str(odoo_id)
                doc.odoo_id = odoo_id

            doc.product_name = pname
            doc.save(ignore_permissions=True)

            synced_items.append({"odoo_id": odoo_id, "product_name": pname})

        frappe.db.commit()
        total_synced += len(rows)
        offset += batch_size

    return {"synced": total_synced, "items": synced_items}
@frappe.whitelist()
def fetch_products_page(page_size=200, offset=0):
    page_size = int(page_size)
    offset = int(offset)

    if page_size <= 0:
        page_size = 200
    if offset < 0:
        offset = 0

    c = get_client()
    rows = c.search_read(
        model="product.template",
        domain=[["sale_ok", "=", True]],
        fields=[
            "id",
            "name",
            "image_1920",
            "image_1024",
            "image_512",
            "image_256",
            "image_128",
            "qty_available",
            "virtual_available",
            "x_studio_brand",
            "barcode",
            "product_barcode",
            "warehouse_id",
            "company_id",
            "taxes_id",
            "tax_string",
        ],
        limit=page_size + 1,
        offset=offset,
        order="id asc",
    )

    has_more = len(rows) > page_size
    if has_more:
        rows = rows[:page_size]

    next_offset = (offset + len(rows)) if has_more else None

    return {
        "items": rows,
        "has_more": has_more,
        "next_offset": next_offset,
    }
