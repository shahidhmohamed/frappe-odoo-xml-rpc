import frappe

def sync_odoo_products_job():
    # background job (safe)
    try:
        frappe.enqueue(
            "odoo_xml_rpc.api.odoo_bulk_sync.sync_products_incremental",
            queue="long",
            timeout=1800,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Odoo Products Incremental Sync Failed")
