frappe.listview_settings["Customer"] = {
	refresh(listview) {
		// Keep default primary (Add Customer) and add sync as a menu item
		listview.page.add_menu_item(__("Sync Odoo Customer"), () => {
			frappe.call({
				method: "odoo_xml_rpc.api.odoo_customer_fetch.fetch_customers_raw",
				args: { limit: 0, batch_size: 1000 },
				freeze: true,
				freeze_message: __("Fetching customers from Odoo..."),
				callback: (r) => {
					const res = r.message || {};
					frappe.msgprint({
						title: __("Sync Completed"),
						indicator: "green",
						message: __("Customers created: {0}", [res.created_customers ?? 0]),
					});
					listview.refresh();
				},
			});
		});
	},
};
