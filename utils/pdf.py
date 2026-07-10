import io
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT


def _get_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='DocTitle', fontSize=18, leading=22,
                              spaceAfter=4, alignment=TA_CENTER, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='DocSub', fontSize=10, leading=14,
                              spaceAfter=2, alignment=TA_CENTER, textColor=colors.grey))
    styles.add(ParagraphStyle(name='SectionHead', fontSize=11, leading=14,
                              spaceBefore=12, spaceAfter=6, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='SmallRight', fontSize=9, leading=12,
                              alignment=TA_RIGHT, textColor=colors.grey))
    return styles


def _header_table(company, doc_type, doc_id, date_str, styles):
    data = [
        [Paragraph(company, styles['DocTitle']), ''],
        [Paragraph(f'{doc_type} #{doc_id}', styles['DocSub']),
         Paragraph(f'Date: {date_str}', styles['SmallRight'])],
    ]
    t = Table(data, colWidths=[280, 280])
    t.setStyle(TableStyle([
        ('SPAN', (0, 0), (1, 0)),
        ('ALIGN', (0, 0), (1, 0), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (1, 0), 2),
        ('TOPPADDING', (0, 1), (0, 1), 0),
    ]))
    return t


def _info_block(rows, styles):
    data = [[Paragraph(k, styles['SmallRight']), Paragraph(v, styles['Normal'])] for k, v in rows]
    t = Table(data, colWidths=[100, 460])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    return t


def _footer(company, styles):
    return Paragraph(f'{company} &mdash; Thank you for your business.', styles['DocSub'])


def generate_invoice_pdf(company, currency, sale, product):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=25*mm, rightMargin=25*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = _get_styles()
    elems = []

    date_str = sale['sale_date'][:10] if isinstance(sale['sale_date'], str) else sale['sale_date'].strftime('%Y-%m-%d')
    elems.append(_header_table(company, 'INVOICE', sale['id'], date_str, styles))
    elems.append(Spacer(1, 10))

    info_rows = [
        ('Customer:', sale['customer_name'] or 'Walk-in'),
        ('Product:', product['title']),
        ('Category:', product['category'] or '-'),
    ]
    elems.append(_info_block(info_rows, styles))
    elems.append(Spacer(1, 12))

    header = ['Item', 'Qty', 'Unit Price', 'Total']
    body = [header, [
        product['title'],
        str(sale['quantity_sold']),
        f'{currency} {sale["unit_price"]:,.0f}',
        f'{currency} {sale["total_amount"]:,.0f}',
    ]]
    t = Table(body, colWidths=[220, 60, 110, 110])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#343a40')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 8))

    totals = [
        ['', '', Paragraph('<b>Subtotal:</b>', styles['Normal']),
         Paragraph(f'{currency} {sale["total_amount"]:,.0f}', styles['Normal'])],
        ['', '', Paragraph('<b>Profit:</b>', styles['Normal']),
         Paragraph(f'{currency} {sale["profit"]:,.0f}', styles['Normal'])],
    ]
    tt = Table(totals, colWidths=[220, 60, 110, 110])
    tt.setStyle(TableStyle([
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elems.append(tt)
    elems.append(Spacer(1, 20))
    elems.append(_footer(company, styles))

    doc.build(elems)
    buf.seek(0)
    return buf


def generate_receipt_pdf(company, currency, sale, product):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=25*mm, rightMargin=25*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = _get_styles()
    elems = []

    date_str = sale['sale_date'][:10] if isinstance(sale['sale_date'], str) else sale['sale_date'].strftime('%Y-%m-%d')
    elems.append(_header_table(company, 'RECEIPT', sale['id'], date_str, styles))
    elems.append(Spacer(1, 10))

    info_rows = [
        ('Customer:', sale['customer_name'] or 'Walk-in'),
        ('Product:', product['title']),
        ('Qty:', str(sale['quantity_sold'])),
        ('Unit Price:', f'{currency} {sale["unit_price"]:,.0f}'),
    ]
    elems.append(_info_block(info_rows, styles))
    elems.append(Spacer(1, 16))

    total_data = [
        [Paragraph('<b>TOTAL PAID:</b>', styles['Normal']),
         Paragraph(f'<b>{currency} {sale["total_amount"]:,.0f}</b>', styles['Normal'])],
    ]
    tt = Table(total_data, colWidths=[350, 210])
    tt.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#d4edda')),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    elems.append(tt)
    elems.append(Spacer(1, 20))
    elems.append(_footer(company, styles))

    doc.build(elems)
    buf.seek(0)
    return buf


def generate_stock_report_pdf(company, currency, products, totals):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = _get_styles()
    elems = []

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    elems.append(_header_table(company, 'STOCK VALUATION REPORT', '', date_str, styles))
    elems.append(Spacer(1, 12))

    header = ['#', 'Product', 'Category', 'Qty', f'Unit Cost ({currency})', f'Stock Value ({currency})']
    rows = [header]
    for i, p in enumerate(products, 1):
        rows.append([
            str(i), p['title'], p['category'] or '-',
            str(p['quantity']),
            f'{p["buying_price"]:,.0f}',
            f'{p["buying_price"] * p["quantity"]:,.0f}',
        ])
    rows.append(['', '', '', '', 'TOTAL', f'{totals["total_value"]:,.0f}'])

    t = Table(rows, colWidths=[25, 160, 80, 40, 90, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#343a40')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.grey),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e9ecef')),
        ('FONTNAME', (4, -1), (-1, -1), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 12))

    summary = [
        f'Total Products: {totals["total_products"]}',
        f'Total Units: {totals["total_units"]}',
        f'Total Invested: {currency} {totals["total_value"]:,.0f}',
        f'Potential Revenue: {currency} {totals["potential_revenue"]:,.0f}',
        f'Potential Profit: {currency} {totals["potential_profit"]:,.0f}',
    ]
    for s in summary:
        elems.append(Paragraph(s, styles['Normal']))
    elems.append(Spacer(1, 12))
    elems.append(_footer(company, styles))

    doc.build(elems)
    buf.seek(0)
    return buf


def generate_sales_report_pdf(company, currency, sales, totals, date_from, date_to):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = _get_styles()
    elems = []

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    elems.append(_header_table(company, 'SALES REPORT', '', date_str, styles))
    elems.append(Paragraph(
        f'Period: {date_from} to {date_to}', styles['DocSub']))
    elems.append(Spacer(1, 12))

    header = ['#', 'Date', 'Product', 'Customer', 'Qty', f'Amount ({currency})', f'Profit ({currency})']
    rows = [header]
    for i, s in enumerate(sales, 1):
        d = s['sale_date'][:10] if isinstance(s['sale_date'], str) else s['sale_date'].strftime('%Y-%m-%d')
        rows.append([
            str(i), d, s['title'], s['customer_name'] or '-',
            str(s['quantity_sold']),
            f'{s["total_amount"]:,.0f}',
            f'{s["profit"]:,.0f}',
        ])
    rows.append(['', '', '', '', 'TOTAL', f'{totals["total_amount"]:,.0f}', f'{totals["total_profit"]:,.0f}'])

    t = Table(rows, colWidths=[25, 65, 120, 80, 30, 80, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#343a40')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (4, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.grey),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e9ecef')),
        ('FONTNAME', (5, -1), (-1, -1), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 12))

    summary = [
        f'Total Sales: {totals["total_count"]}',
        f'Total Revenue: {currency} {totals["total_amount"]:,.0f}',
        f'Total Profit: {currency} {totals["total_profit"]:,.0f}',
    ]
    if totals['total_amount'] > 0:
        margin = totals['total_profit'] / totals['total_amount'] * 100
        summary.append(f'Profit Margin: {margin:.1f}%')
    for s in summary:
        elems.append(Paragraph(s, styles['Normal']))
    elems.append(Spacer(1, 12))
    elems.append(_footer(company, styles))

    doc.build(elems)
    buf.seek(0)
    return buf


def generate_pnl_report_pdf(company, currency, data, date_from, date_to):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=25*mm, rightMargin=25*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = _get_styles()
    elems = []

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    elems.append(_header_table(company, 'PROFIT & LOSS STATEMENT', '', date_str, styles))
    elems.append(Paragraph(
        f'Period: {date_from} to {date_to}', styles['DocSub']))
    elems.append(Spacer(1, 16))

    rows = [
        [Paragraph('<b>REVENUE</b>', styles['Normal']), ''],
        ['  Sales Revenue', f'{currency} {data["revenue"]:,.0f}'],
        ['', ''],
        [Paragraph('<b>COST OF GOODS SOLD</b>', styles['Normal']), ''],
        ['  Cost of Products Sold', f'{currency} {data["cogs"]:,.0f}'],
        ['', ''],
        [Paragraph('<b>GROSS PROFIT</b>', styles['Normal']),
         f'{currency} {data["gross_profit"]:,.0f}'],
        ['', ''],
        [Paragraph('<b>OPERATING EXPENSES</b>', styles['Normal']), ''],
    ]
    for exp in data.get('expense_breakdown', []):
        rows.append([f'  {exp["category"] or "Uncategorized"}', f'{currency} {exp["amount"]:,.0f}'])
    rows.append(['  Total Expenses', f'{currency} {data["total_expenses"]:,.0f}'])
    rows.append(['', ''])
    rows.append([Paragraph('<b>NET PROFIT</b>', styles['Normal']),
                 f'<b>{currency} {data["net_profit"]:,.0f}</b>'])

    t = Table(rows, colWidths=[350, 200])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
        ('LINEBELOW', (0, 3), (-1, 3), 1, colors.black),
        ('LINEBELOW', (0, 6), (-1, 6), 2, colors.black),
        ('LINEBELOW', (0, 8), (-1, 8), 1, colors.black),
        ('LINEBELOW', (0, -1), (-1, -1), 2, colors.black),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d4edda') if data['net_profit'] >= 0 else colors.HexColor('#f8d7da')),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 20))

    summary_lines = [
        f'Revenue: {currency} {data["revenue"]:,.0f}',
        f'COGS: {currency} {data["cogs"]:,.0f}',
        f'Gross Profit: {currency} {data["gross_profit"]:,.0f}',
        f'Expenses: {currency} {data["total_expenses"]:,.0f}',
        f'Net Profit: {currency} {data["net_profit"]:,.0f}',
    ]
    if data['revenue'] > 0:
        summary_lines.append(f'Net Margin: {data["net_profit"] / data["revenue"] * 100:.1f}%')
    for line in summary_lines:
        elems.append(Paragraph(line, styles['Normal']))
    elems.append(Spacer(1, 12))
    elems.append(_footer(company, styles))

    doc.build(elems)
    buf.seek(0)
    return buf
