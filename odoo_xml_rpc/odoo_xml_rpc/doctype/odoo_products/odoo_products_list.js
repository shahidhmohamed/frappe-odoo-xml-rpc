// Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
frappe.listview_settings["Odoo Products"] = {
	refresh(listview) {
		// Remove existing primary action (Add).
		listview.page.clear_primary_action();

		// Make the sync button primary (appears before Add).
		listview.page.set_primary_action(__("Sync"), () => {
			frappe.call({
				method: "odoo_xml_rpc.api.odoo_bulk_sync.sync_products_incremental",
				freeze: true,
				freeze_message: __("Fetching products from Odoo..."),
				callback: (r) => {
					const res = r.message || {};
					frappe.msgprint({
						title: __("Sync Completed"),
						indicator: "green",
						message: __(
							"Synced: {0}<br>Cursor: {1} / {2}",
							[
								res.synced ?? 0,
								res.cursor_write_date || "-",
								res.cursor_last_id ?? 0,
							]
						),
					});
					listview.refresh();
				},
			});
		});
	},
};
