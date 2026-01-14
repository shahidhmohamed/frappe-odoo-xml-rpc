import frappe
from odoo_xml_rpc.integrations.odoo_client import get_client


@frappe.whitelist()
def sync_customer_groups_from_odoo(limit: int = 0, batch_size: int = 1000):
    if not frappe.has_permission("Customer Group", "write"):
        frappe.throw("Not permitted to update Customer Groups.")

    parent_group = "All Customer Groups"
    if not frappe.db.exists("Customer Group", parent_group):
        frappe.throw(f"Missing parent Customer Group: {parent_group}")

    limit = int(limit or 0)
    batch_size = int(batch_size or 1000)
    if batch_size <= 0:
        batch_size = 1000

    c = get_client()
    fields = ["id", "name"]
    domain = []

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
            model="res.partner.category",
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

    created = 0
    skipped = 0
    skipped_names = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name:
            skipped += 1
            skipped_names.append("(blank)")
            continue
        if frappe.db.exists("Customer Group", name):
            skipped += 1
            skipped_names.append(name)
            continue

        doc = frappe.new_doc("Customer Group")
        doc.customer_group_name = name
        doc.parent_customer_group = parent_group
        doc.is_group = 0
        doc.save(ignore_permissions=True)
        created += 1

    return {
        "fetched": len(rows),
        "created": created,
        "skipped": skipped,
        "skipped_names": skipped_names,
    }
