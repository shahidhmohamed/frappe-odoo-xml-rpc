import frappe


def execute():
    fieldname = "custom_sales_staff"
    custom_field = frappe.db.get_value(
        "Custom Field", {"dt": "Customer", "fieldname": fieldname}, "name"
    )
    if custom_field:
        frappe.db.set_value(
            "Custom Field",
            custom_field,
            {"fieldtype": "Data", "options": "", "default": ""},
            update_modified=False,
        )

    try:
        frappe.db.sql(
            "alter table `tabCustomer` modify column custom_sales_staff varchar(140)"
        )
    except Exception:
        pass
