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

    def search_read(
        self,
        model: str,
        domain,
        fields,
        limit: int = 50,
        offset: int = 0,
        order: str = "",
        load: bool = False,
        context: dict | None = None,
    ):
        return self.execute_kw(
            model=model,
            method="search_read",
            args=[domain],
            kwargs={
                "fields": fields,
                "limit": int(limit),
                "offset": int(offset),
                "order": order,
                "load": bool(load),
                **({"context": context} if context else {}),
            },
        )

    def fields_get(self, model: str, fields: list[str]):
        field_list = [f for f in (fields or []) if f]
        return self.execute_kw(
            model=model,
            method="fields_get",
            args=[field_list],
            kwargs={"attributes": ["type", "relation"]},
        )

    def name_get(self, model: str, ids: list[int]):
        return self.execute_kw(
            model=model,
            method="name_get",
            args=[list(set(int(i) for i in ids if int(i) > 0))],
        )

    def web_read(self, model: str, ids: list[int], fields: list[str] | None = None):
        clean_ids = list(set(int(i) for i in ids if int(i) > 0))
        spec_list = fields or ["display_name", "name"]
        spec_dict = {field: {} for field in spec_list}
        try:
            return self.execute_kw(
                model=model,
                method="web_read",
                args=[clean_ids, spec_dict],
            )
        except frappe.ValidationError:
            try:
                return self.execute_kw(
                    model=model,
                    method="web_read",
                    args=[clean_ids, spec_list],
                )
            except frappe.ValidationError:
                try:
                    return self.execute_kw(
                        model=model,
                        method="web_read",
                        args=[clean_ids],
                        kwargs={"specification": spec_dict},
                    )
                except frappe.ValidationError:
                    try:
                        return self.execute_kw(
                            model=model,
                            method="web_read",
                            args=[clean_ids],
                            kwargs={"specification": spec_list},
                        )
                    except frappe.ValidationError:
                        return self.execute_kw(
                            model=model,
                            method="web_read",
                            args=[clean_ids],
                            kwargs={"fields": spec_list},
                        )

    def read(self, model: str, ids: list[int], fields: list[str] | None = None):
        clean_ids = list(set(int(i) for i in ids if int(i) > 0))
        return self.execute_kw(
            model=model,
            method="read",
            args=[clean_ids, fields or ["display_name", "name"]],
        )

    def execute_kw(self, model: str, method: str, args: list | None = None, kwargs: dict | None = None):
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
                    method,
                    args or [],
                    kwargs or {},
                ],
            },
            "id": int(time.time() * 1_000_000),
        }
        return self._post(payload)

    def _post(self, payload: dict):
        r = requests.post(self.url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()

        if data.get("error"):
            raise frappe.ValidationError(f"Odoo Error: {data['error']}")
        return data.get("result") or []


def get_client() -> OdooClient:
    # This DocType must exist and be saved with values:
    # Odoo Config (Single) fields:
    # odoo_url, odoo_db, odoo_uid, odoo_api_key (Password)
    s = frappe.get_single("Odoo Config")

    return OdooClient(
        url=s.odoo_url,
        db=s.odoo_db,
        uid=s.odoo_uid,
        api_key=s.get_password("odoo_api_key"),
        timeout=10,
    )
