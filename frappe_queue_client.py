from __future__ import annotations

import json
from urllib.parse import quote

import requests


class AuthenticationError(RuntimeError):
    pass


class FrappeQueueClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, timeout: int = 30, session=None):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = session or requests.Session()
        self.headers = {
            'Authorization': f'token {api_key}:{api_secret}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _request(self, method: str, path: str, **kwargs):
        response = self.session.request(
            method,
            f'{self.base_url}{path}',
            headers=self.headers,
            timeout=self.timeout,
            **kwargs,
        )
        if response.status_code == 401:
            raise AuthenticationError(f'ERPNext API authentication failed (401): {response.text}')
        response.raise_for_status()
        payload = response.json()
        return payload.get('data', payload)

    @staticmethod
    def _resource_path(doctype: str, name: str | None = None) -> str:
        path = f'/api/resource/{quote(doctype, safe="")}'
        if name is not None:
            path += f'/{quote(name, safe="")}'
        return path

    def list_retryable(self, max_retries: int) -> list[dict]:
        filters = [
            ['Kitchen Print Queue', 'status', 'in', ['Pending', 'Error']],
            ['Kitchen Print Queue', 'retry_count', '<', int(max_retries)],
        ]
        params = {
            'fields': json.dumps([
                'name', 'sales_order', 'kitchen_counter', 'printer_name',
                'items_json', 'status', 'retry_count', 'last_error',
                'creation', 'modified'
            ]),
            'filters': json.dumps(filters),
            'order_by': 'creation asc',
            'limit_page_length': 20,
        }
        data = self._request('GET', self._resource_path('Kitchen Print Queue'), params=params)
        return list(data or [])

    def list_printing(self) -> list[dict]:
        params = {
            'fields': json.dumps(['name', 'status', 'retry_count', 'modified']),
            'filters': json.dumps([
                ['Kitchen Print Queue', 'status', '=', 'Printing'],
            ]),
            'order_by': 'modified asc',
            'limit_page_length': 100,
        }
        data = self._request('GET', self._resource_path('Kitchen Print Queue'), params=params)
        return list(data or [])

    def list_unqueued_sales_orders(self, limit: int = 20) -> list[dict]:
        params = {
            'fields': json.dumps(['name', 'creation']),
            'filters': json.dumps([
                ['Sales Order', 'docstatus', '=', 0],
                ['Sales Order', 'custom_kitchen_queue_created', '=', 0],
            ]),
            'order_by': 'creation asc',
            'limit_page_length': int(limit),
        }
        data = self._request('GET', self._resource_path('Sales Order'), params=params)
        return list(data or [])

    def list_draft_sales_orders(self, limit: int = 500) -> list[dict]:
        params = {
            'fields': json.dumps([
                'name',
                'creation',
                'modified',
                'custom_kitchen_queue_created',
                'custom_kitchen_print_snapshot',
            ]),
            'filters': json.dumps([
                ['Sales Order', 'docstatus', '=', 0],
            ]),
            'order_by': 'modified desc',
            'limit_page_length': int(limit),
        }
        data = self._request('GET', self._resource_path('Sales Order'), params=params)
        return list(data or [])

    def get(self, name: str) -> dict:
        return dict(self._request('GET', self._resource_path('Kitchen Print Queue', name)) or {})

    def update(self, name: str, values: dict) -> dict:
        return dict(self._request('PUT', self._resource_path('Kitchen Print Queue', name), json=values) or {})

    def get_sales_order(self, name: str) -> dict:
        return dict(self._request('GET', self._resource_path('Sales Order', name)) or {})

    def mark_sales_order_queued(self, name: str) -> dict:
        return dict(
            self._request(
                'PUT',
                self._resource_path('Sales Order', name),
                json={'custom_kitchen_queue_created': 1},
            )
            or {}
        )

    def update_sales_order_kitchen_state(self, name: str, snapshot: str) -> dict:
        return dict(
            self._request(
                'PUT',
                self._resource_path('Sales Order', name),
                json={
                    'custom_kitchen_queue_created': 1,
                    'custom_kitchen_print_snapshot': snapshot,
                },
            )
            or {}
        )

    def get_item(self, name: str) -> dict:
        return dict(self._request('GET', self._resource_path('Item', name)) or {})

    def get_kitchen_counter(self, name: str) -> dict:
        return dict(self._request('GET', self._resource_path('Kitchen Counter', name)) or {})

    def queue_exists(self, queue_key: str) -> bool:
        params = {
            'fields': json.dumps(['name']),
            'filters': json.dumps([
                ['Kitchen Print Queue', 'queue_key', '=', queue_key],
            ]),
            'limit_page_length': 1,
        }
        data = self._request('GET', self._resource_path('Kitchen Print Queue'), params=params)
        return bool(data)

    def create_queue(self, payload: dict) -> dict:
        return dict(
            self._request('POST', self._resource_path('Kitchen Print Queue'), json=payload) or {}
        )

    def list_cashier_retryable(self, max_retries: int) -> list[dict]:
        params = {
            'fields': json.dumps([
                'name', 'sales_invoice', 'sales_order', 'printer_name',
                'status', 'retry_count', 'last_error', 'creation', 'modified',
            ]),
            'filters': json.dumps([
                ['Cashier Print Queue', 'status', 'in', ['Pending', 'Error']],
                ['Cashier Print Queue', 'retry_count', '<', int(max_retries)],
            ]),
            'order_by': 'creation asc',
            'limit_page_length': 20,
        }
        data = self._request(
            'GET',
            self._resource_path('Cashier Print Queue'),
            params=params,
        )
        return list(data or [])

    def list_cashier_printing(self) -> list[dict]:
        params = {
            'fields': json.dumps(['name', 'status', 'retry_count', 'modified']),
            'filters': json.dumps([
                ['Cashier Print Queue', 'status', '=', 'Printing'],
            ]),
            'order_by': 'modified asc',
            'limit_page_length': 100,
        }
        data = self._request(
            'GET',
            self._resource_path('Cashier Print Queue'),
            params=params,
        )
        return list(data or [])

    def update_cashier_queue(self, name: str, values: dict) -> dict:
        return dict(
            self._request(
                'PUT',
                self._resource_path('Cashier Print Queue', name),
                json=values,
            )
            or {}
        )

    def get_sales_invoice(self, name: str) -> dict:
        return dict(
            self._request('GET', self._resource_path('Sales Invoice', name)) or {}
        )
