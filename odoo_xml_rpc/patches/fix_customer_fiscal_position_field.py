import frappe


def execute():
    name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Customer", "fieldname": "custom_fiscal_position"},
        "name",
    )
    if not name:
        return

    frappe.db.set_value(
        "Custom Field",
        name,
        {"fieldtype": "Data", "options": "", "default": ""},
        update_modified=False,
    )
