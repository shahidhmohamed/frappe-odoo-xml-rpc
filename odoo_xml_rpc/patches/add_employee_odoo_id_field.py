import frappe


def execute():
    fieldname = "custom_odoo_id"
    if frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": fieldname}):
        return

    doc = frappe.get_doc(
        {
            "doctype": "Custom Field",
            "dt": "Employee",
            "label": "Odoo ID",
            "fieldname": fieldname,
            "fieldtype": "Int",
            "unique": 1,
            "insert_after": "employee_name",
        }
    )
    doc.insert(ignore_permissions=True)
