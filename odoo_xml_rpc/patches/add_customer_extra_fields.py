import frappe


def _get_custom_field_name(fieldname: str) -> str | None:
    return frappe.db.get_value("Custom Field", {"dt": "Customer", "fieldname": fieldname}, "name")


def _field_exists(fieldname: str) -> bool:
    meta = frappe.get_meta("Customer")
    if meta.has_field(fieldname):
        return True
    return bool(_get_custom_field_name(fieldname))


def execute():
    fields = [
        {
            "label": "Rebate",
            "fieldname": "custom_rebate",
            "fieldtype": "Percent",
            "default": "0",
        },
        {
            "label": "Old Customer No.",
            "fieldname": "custom_old_customer_no",
            "fieldtype": "Data",
        },
        {
            "label": "Language",
            "fieldname": "custom_language",
            "fieldtype": "Link",
            "options": "Language",
        },
        {
            "label": "Old Rec.",
            "fieldname": "custom_old_rec",
            "fieldtype": "Check",
            "default": "0",
        },
        {
            "label": "Partner Type",
            "fieldname": "custom_partner_type",
            "fieldtype": "Data",
        },
        {
            "label": "Sales Staff",
            "fieldname": "custom_sales_staff",
            "fieldtype": "Link",
            "options": "Employee",
        },
        {
            "label": "Salesperson",
            "fieldname": "custom_salesperson",
            "fieldtype": "Link",
            "options": "User",
        },
        {
            "label": "Fiscal Position",
            "fieldname": "custom_fiscal_position",
            "fieldtype": "Data",
        },
        {
            "label": "Company ID",
            "fieldname": "custom_company_id",
            "fieldtype": "Link",
            "options": "Company",
        },
        {
            "label": "Reference",
            "fieldname": "custom_reference",
            "fieldtype": "Data",
        },
        {
            "label": "Company",
            "fieldname": "custom_company",
            "fieldtype": "Link",
            "options": "Company",
        },
    ]

    insert_after = "customer_group"

    for field in fields:
        existing_custom = _get_custom_field_name(field["fieldname"])
        if existing_custom:
            frappe.db.set_value(
                "Custom Field",
                existing_custom,
                {
                    "fieldtype": field.get("fieldtype"),
                    "options": field.get("options") or "",
                    "default": field.get("default") or "",
                },
                update_modified=False,
            )
            insert_after = field["fieldname"]
            continue

        if _field_exists(field["fieldname"]):
            insert_after = field["fieldname"]
            continue

        doc = frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": "Customer",
                "insert_after": insert_after,
                **field,
            }
        )
        doc.insert(ignore_permissions=True)
        insert_after = field["fieldname"]
