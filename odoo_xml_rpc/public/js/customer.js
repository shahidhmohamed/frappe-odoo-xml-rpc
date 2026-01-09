// frappe.ui.form.on("Customer", {
// 	refresh(frm) {
// 		if (frm.custom_buttons && frm.custom_buttons["Fetch from Odoo"]) {
// 			return;
// 		}

// 		frm.add_custom_button(
// 			__("Fetch from Odoo"),
// 			() => {
// 				frappe.call({
// 					method: "odoo_xml_rpc.api.odoo_customer_sync.sync_customers_with_addresses",
// 					args: { limit: 0, batch_size: 1000 },
// 					freeze: true,
// 					freeze_message: __("Fetching customers from Odoo..."),
// 					callback(r) {
// 						if (!r.message) {
// 							return;
// 						}
// 						const msg = __("Fetched {0}, synced {1} customers and {2} addresses.", [
// 							r.message.fetched,
// 							r.message.synced_customers,
// 							r.message.synced_addresses,
// 						]);
// 						frappe.msgprint(msg);
// 					},
// 				});
// 			},
// 			__("Odoo")
// 		);
// 	},
// });
