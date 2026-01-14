frappe.listview_settings["Customer"] = {
	refresh(listview) {
		// Keep default primary (Add Customer) and add sync as a menu item
		listview.page.add_menu_item(__("Sync Odoo Customer"), () => {
			const startSync = () => {
				frappe.call({
					method: "odoo_xml_rpc.api.odoo_customer_fetch.fetch_customers_raw",
					args: { limit: 0, batch_size: 1000, run_async: 1 },
					freeze: true,
					freeze_message: __("Queueing customer sync..."),
					callback: (r) => {
						const res = r.message || {};
						const jobId = res.job_id || "";
						if (!jobId) {
							frappe.msgprint({
								title: __("Sync Failed"),
								indicator: "red",
								message: __("No job id returned from server."),
							});
							return;
						}

						frappe.dom.freeze(__("Syncing customers from Odoo..."));

						const poll = () => {
							frappe.call({
								method: "odoo_xml_rpc.api.odoo_customer_fetch.get_fetch_customers_job_status",
								args: { job_id: jobId },
								callback: (statusResp) => {
									const status = statusResp.message?.status;
									if (!status || status === "queued" || status === "started") {
										return;
									}

									clearInterval(timer);
									frappe.dom.unfreeze();
									if (status === "finished") {
										frappe.msgprint({
											title: __("Sync Completed"),
											indicator: "green",
											message: __("Customer sync finished successfully."),
										});
										listview.refresh();
										return;
									}

									frappe.msgprint({
										title: __("Sync Failed"),
										indicator: "red",
										message: statusResp.message?.error
											? __("Customer sync failed: {0}", [
													statusResp.message.error,
											  ])
											: __("Customer sync ended with status: {0}", [status]),
									});
								},
							});
						};

						const timer = setInterval(poll, 2000);
						poll();
					},
				});
			};

			const sampleName = "Nesto Hypermarket Sole Proprietorship LLC-Branch 1";
			frappe.call({
				method: "odoo_xml_rpc.api.odoo_customer_fetch.preview_customer_sync",
				args: { sample_name: sampleName },
				callback: (r) => {
					const res = r.message || {};
					const raw = res.odoo_raw || {};
					const converted = res.converted || {};
					const name = raw?.name || converted?.customer_name || sampleName;
					const rawText = frappe.utils.escape_html(JSON.stringify(raw, null, 2));
					const convertedText = frappe.utils.escape_html(
						JSON.stringify(converted, null, 2)
					);
					const message = `
						<div>
							<div><b>${__("Sample Customer")}</b>: ${name}</div>
							<div style="margin-top: 8px;"><b>${__("Odoo Response")}</b></div>
							<pre style="max-height: 240px; overflow: auto;">${rawText}</pre>
							<div style="margin-top: 8px;"><b>${__("Converted Response")}</b></div>
							<pre style="max-height: 240px; overflow: auto;">${convertedText}</pre>
						</div>
					`;
					frappe.confirm(message, startSync);
				},
				error: () => {
					frappe.confirm(__("Start customer sync?"), startSync);
				},	
			});
		});
	},
};
