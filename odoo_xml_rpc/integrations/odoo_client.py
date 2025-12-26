import time
import frappe
import requests


class OdooClient:
    def __init__(self, url: str, db: str, uid: int, api_key: str, timeout: int = 10):
        self.url = url
        self.db = db
        self.uid = int(uid)
        self.api_key = api_key
        self.timeout = timeout

    def search_read(self, model: str, domain, fields, limit: int = 50, offset: int = 0, order: str = ""):
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": "object",
                "method": "execute_kw",
                "args": [
                    self.db,
                    self.uid,
                    self.api_key,
                    model,
                    "search_read",
                    [domain],
                    {
                        "fields": fields,
                        "limit": int(limit),
                        "offset": int(offset),
                        "order": order,
                        "load": False,
                    },
                ],
            },
            "id": int(time.time() * 1_000_000),
        }

        r = requests.post(self.url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()

        if data.get("error"):
            raise frappe.ValidationError(f"Odoo Error: {data['error']}")
        return data.get("result") or []


def get_client() -> OdooClient:
    # This DocType must exist and be saved with values:
    # Odoo Settings (Single) fields:
    # odoo_url, odoo_db, odoo_uid, odoo_api_key (Password)
    s = frappe.get_single("Odoo Config")

    return OdooClient(
        url=s.odoo_url,
        db=s.odoo_db,
        uid=s.odoo_uid,
        api_key=s.get_password("odoo_api_key"),
        timeout=10,
    )
