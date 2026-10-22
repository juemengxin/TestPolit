"""Minimal dependency-free XLSX generation for project asset exports."""

from __future__ import annotations

import json
from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile


XLSX_MIME_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
HEADERS = ['ID', '名称', '类型', '所属父级', '平台', '前置条件', '测试步骤', '预期结果', '结构化配置', '正文', '版本', '更新时间']


def _column_name(index: int) -> str:
    """Convert a zero-based column index to the Excel A1 column label."""
    label = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label


def _cell(value: object, reference: str) -> str:
    """Serialize values as literal worksheet cells, never executable formulas."""
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{reference}" t="n"><v>{value}</v></c>'
    text = str(value if value is not None else '')[:32000]
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def _entity_row(entity) -> list[object]:
    config = entity.config if isinstance(entity.config, dict) else {}
    steps = config.get('steps') or config.get('design_steps') or []
    parent_name = entity.parent.name if entity.parent_id else ''
    return [
        entity.pk,
        entity.name,
        entity.kind,
        parent_name,
        config.get('platform', ''),
        config.get('preconditions', ''),
        json.dumps(steps, ensure_ascii=False, indent=2) if steps else '',
        config.get('expected', ''),
        json.dumps(config, ensure_ascii=False, indent=2),
        entity.content,
        entity.version,
        entity.updated_at.isoformat(),
    ]


def export_entities_xlsx(entities) -> bytes:
    """Build an Excel workbook with one row per authorized project entity."""
    rows = [HEADERS, *(_entity_row(entity) for entity in entities)]
    last_column = _column_name(len(HEADERS))
    xml_rows = []
    for row_number, row in enumerate(rows, start=1):
        xml_rows.append(
            f'<row r="{row_number}">'
            + ''.join(_cell(value, f'{_column_name(column)}{row_number}') for column, value in enumerate(row, start=1))
            + '</row>'
        )

    widths = [10, 32, 18, 24, 12, 30, 48, 32, 52, 52, 10, 24]
    cols = ''.join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_column}{len(rows)}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols>{cols}</cols><sheetData>{"".join(xml_rows)}</sheetData>'
        f'<autoFilter ref="A1:{last_column}{len(rows)}"/>'
        '</worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="测试用例" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    package_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )

    workbook_file = BytesIO()
    with ZipFile(workbook_file, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', package_relationships)
        archive.writestr('xl/workbook.xml', workbook)
        archive.writestr('xl/_rels/workbook.xml.rels', workbook_relationships)
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    return workbook_file.getvalue()
