frappe.listview_settings["Customer Group"] = {
	refresh(listview) {
		// Keep default primary (Add Customer) and add sync as a menu item
		listview.page.add_menu_item(__("Sync Odoo Customer Groups"), () => {
			frappe.call({
				method: "odoo_xml_rpc.api.odoo_customer_tag_fetch.sync_customer_groups_from_odoo",
				args: { limit: 0, batch_size: 1000 },
				freeze: true,
				freeze_message: __("Syncing customer groups from Odoo..."),
				callback: (r) => {
					const res = r.message || {};
					frappe.msgprint({
						title: __("Sync Completed"),
						indicator: "green",
						message: res.skipped_names?.length
							? __("Created: {0} (Skipped: {1})<br>Skipped: {2}", [
								res.created ?? 0,
								res.skipped ?? 0,
								res.skipped_names.join(", "),
							])
							: __("Created: {0} (Skipped: {1})", [
								res.created ?? 0,
								res.skipped ?? 0,
							]),
					});
					listview.refresh();
				},
			});
		});
	},
};
