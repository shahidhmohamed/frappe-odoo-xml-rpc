import frappe


def execute():
    rows = frappe.db.sql(
        """
        select
            dl.link_name as customer,
            a.name,
            a.creation,
            a.address_type,
            a.address_title,
            a.address_line1,
            a.address_line2,
            a.city,
            a.state,
            a.country,
            a.pincode,
            a.phone,
            a.email_id
        from `tabAddress` a
        inner join `tabDynamic Link` dl
            on dl.parent = a.name
            and dl.parenttype = 'Address'
            and dl.parentfield = 'links'
            and dl.link_doctype = 'Customer'
        """,
        as_dict=True,
    )

    groups = {}
    for row in rows:
        key = (
            row.get("customer"),
            row.get("address_type"),
            row.get("address_title"),
            row.get("address_line1"),
            row.get("address_line2"),
            row.get("city"),
            row.get("state"),
            row.get("country"),
            row.get("pincode"),
            row.get("phone"),
            row.get("email_id"),
        )
        groups.setdefault(key, []).append(row)

    for group in groups.values():
        if len(group) <= 1:
            continue
        group.sort(key=lambda r: r.get("creation") or "")
        keep = group[0]["name"]
        drop = [r["name"] for r in group[1:]]
        if not drop:
            continue
        # move customer links to the kept address
        frappe.db.sql(
            """
            update `tabDynamic Link`
            set parent=%(keep)s
            where parenttype='Address'
              and parentfield='links'
              and link_doctype='Customer'
              and parent in %(drop)s
            """,
            {"drop": tuple(drop), "keep": keep},
        )
        # remove duplicate addresses
        frappe.db.delete("Address", {"name": ["in", drop]})
