frappe.listview_settings["Employee"] = {
	refresh(listview) {
		listview.page.add_menu_item(__("Sync Odoo Employees"), () => {
			frappe.call({
				method: "odoo_xml_rpc.api.odoo_employee_fetch.fetch_employees_raw",
				args: { limit: 0, batch_size: 200, run_async: 1, overwrite_images: 1 },
				freeze: true,
				freeze_message: __("Queueing employee sync..."),
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

					frappe.dom.freeze(__("Syncing employees from Odoo..."));

					const poll = () => {
						frappe.call({
							method: "odoo_xml_rpc.api.odoo_employee_fetch.get_fetch_employees_job_status",
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
										message: __("Employee sync finished successfully."),
									});
									listview.refresh();
									return;
								}

								frappe.msgprint({
									title: __("Sync Failed"),
									indicator: "red",
									message: statusResp.message?.error
										? __("Employee sync failed: {0}", [statusResp.message.error])
										: __("Employee sync ended with status: {0}", [status]),
								});
							},
						});
					};

					const timer = setInterval(poll, 2000);
					poll();
				},
			});
		});
	},
};
