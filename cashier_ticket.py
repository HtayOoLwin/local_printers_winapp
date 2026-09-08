from __future__ import annotations

from html import escape



def _money(value) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount.is_integer():
        return f'{int(amount):,}'
    return f'{amount:,.2f}'


def _qty(value) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount.is_integer():
        return str(int(amount))
    return f'{amount:g}'


def build_cashier_bill_html(invoice: dict, restaurant_name: str = 'Restaurant') -> str:
    if int(invoice.get('docstatus') or 0) != 0:
        raise ValueError('Cashier auto print requires a Draft Sales Invoice')

    name = escape(str(invoice.get('name') or ''))
    customer = escape(
        str(invoice.get('customer_name') or invoice.get('customer') or '')
    )
    currency = escape(str(invoice.get('currency') or ''))
    creation = escape(str(invoice.get('creation') or ''))

    item_rows = []
    for item in invoice.get('items') or []:
        item_name = escape(
            str(item.get('item_name') or item.get('item_code') or '')
        )
        item_rows.append(
            '<tr>'
            f'<td class="qty">{escape(_qty(item.get("qty")))}</td>'
            f'<td class="desc">{item_name}</td>'
            f'<td class="rate">{escape(_money(item.get("rate")))}</td>'
            f'<td class="amt">{escape(_money(item.get("amount")))}</td>'
            '</tr>'
        )

    tax_rows = []
    for tax in invoice.get('taxes') or []:
        description = escape(
            str(tax.get('description') or tax.get('account_head') or 'Tax')
        )
        rate = float(tax.get('rate') or 0)
        label = f'{description} {_qty(rate)}%' if rate else description
        tax_rows.append(
            '<div class="total-row">'
            f'<span>{label}</span><span>{_money(tax.get("tax_amount"))}</span>'
            '</div>'
        )

    net_total = _money(invoice.get('net_total'))
    grand_total = _money(invoice.get('grand_total'))

    return f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{ size: 80mm auto; margin: 3mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: Arial, sans-serif; width: 74mm; margin: 0; font-size: 11px; color: #000; }}
h1 {{ font-size: 16px; text-align: center; margin: 0 0 2px; }}
h2 {{ font-size: 14px; text-align: center; margin: 0 0 6px; }}
.meta {{ line-height: 1.35; margin-bottom: 5px; }}
.rule {{ border-top: 1px dashed #000; margin: 5px 0; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
th {{ text-align: left; font-size: 9px; }}
td {{ vertical-align: top; padding: 2px 0; }}
.qty {{ width: 10%; }} .desc {{ width: 42%; }} .rate {{ width: 22%; text-align: right; }} .amt {{ width: 26%; text-align: right; }}
th.rate, th.amt {{ text-align: right; }}
.total-row {{ display: flex; justify-content: space-between; gap: 8px; margin: 2px 0; }}
.grand {{ font-size: 14px; font-weight: bold; }}
</style>
</head>
<body>
<h1>{escape(str(restaurant_name or 'Restaurant'))}</h1>
<h2>BILL</h2>
<div class="meta">
<div><b>{customer}</b></div>
<div>Invoice: {name}</div>
<div>Date: {creation}</div>
</div>
<div class="rule"></div>
<table>
<thead><tr><th class="qty">QTY</th><th class="desc">DESCRIPTION</th><th class="rate">RATE</th><th class="amt">AMOUNT</th></tr></thead>
<tbody>{''.join(item_rows)}</tbody>
</table>
<div class="rule"></div>
<div class="total-row"><span>Subtotal</span><span>{net_total}</span></div>
{''.join(tax_rows)}
<div class="rule"></div>
<div class="total-row grand"><span>GRAND TOTAL</span><span>{grand_total} {currency}</span></div>
</body>
</html>'''
