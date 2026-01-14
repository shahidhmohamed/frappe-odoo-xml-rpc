import frappe


def execute():
    fieldname = "custom_odoo_id"
    existing_name = frappe.db.get_value(
        "Custom Field", {"dt": "User", "fieldname": fieldname}, "name"
    )
    if existing_name:
        frappe.db.set_value(
            "Custom Field",
            existing_name,
            {"unique": 0, "default": "", "reqd": 0},
            update_modified=False,
        )
        return

    doc = frappe.get_doc(
        {
            "doctype": "Custom Field",
            "dt": "User",
            "label": "Odoo ID",
            "fieldname": fieldname,
            "fieldtype": "Int",
            "unique": 0,
            "insert_after": "email",
        }
    )
    doc.insert(ignore_permissions=True)
