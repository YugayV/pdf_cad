import base64
import io
import json
import os
import re
from typing import List, Optional, Tuple

import ezdxf
import fitz  # PyMuPDF
import pandas as pd
import pdfplumber
import streamlit as st
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

CLAUDE_MODEL = "claude-opus-4-8"

SUPPORTED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}


def load_input_as_pdf_bytes(file_bytes: bytes, filename: str) -> bytes:
    """Принимает содержимое загруженного файла (PDF или изображение JPG/JPEG/PNG) и возвращает PDF-байты.
    Изображения оборачиваются в PDF из одной страницы, чтобы дальше все инструменты (текст, DXF, Vision,
    экспликация) работали единообразно независимо от исходного формата."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        return file_bytes
    if ext in SUPPORTED_IMAGE_EXTENSIONS:
        image_filetype = "jpeg" if ext in ("jpg", "jpeg") else ext
        img_doc = fitz.open(stream=file_bytes, filetype=image_filetype)
        try:
            return img_doc.convert_to_pdf()
        finally:
            img_doc.close()
    raise ValueError(f"Неподдерживаемый формат файла: .{ext or '?'}. Поддерживаются PDF, JPG/JPEG, PNG.")


def get_pdf_page_count() -> int:
    """Возвращает количество страниц в загруженном PDF (0, если PDF не загружен)."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return 0
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def extract_text_from_pdf(query: str = "") -> str:
    """Извлекает весь текст из загруженного PDF чертежа (по всем страницам). Используй, если нужно найти
    размеры, надписи или спецификации."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен."
    # Используем PyMuPDF, а не pdfplumber: pdfplumber реконструирует раскладку текста относительно ВСЕХ
    # векторных объектов на странице, что на чертежах с десятками/сотнями тысяч линий (штриховка, детальные
    # планы) может занимать десятки секунд на страницу; PyMuPDF не имеет этой проблемы.
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        text = "".join(page.get_text() + "\n" for page in doc)
    finally:
        doc.close()
    return text[:3000]  # Ограничиваем для контекста


class DetectedObject(BaseModel):
    name: str = Field(description="Название объекта на чертеже, например 'Шкаф навесной', 'Стена', 'Дверь'")
    quantity: int = Field(description="Количество объектов этого типа на чертеже")
    unit: str = Field(default="шт", description="Единица измерения: шт, м, м2, м3")
    width_mm: Optional[float] = Field(default=None, description="Ширина в мм, если указана на чертеже")
    height_mm: Optional[float] = Field(default=None, description="Высота в мм, если указана на чертеже")
    depth_mm: Optional[float] = Field(default=None, description="Глубина в мм, если указана на чертеже")


class DrawingAnalysis(BaseModel):
    objects: List[DetectedObject] = Field(default_factory=list, description="Список объектов на чертеже с количеством и габаритами")
    summary: str = Field(default="", description="Краткое текстовое резюме анализа чертежа")


def analyze_pdf_visuals_structured(query: str = "", page_number: int = 1) -> str:
    """Анализирует визуальную часть PDF-чертежа через Claude Vision и возвращает СТРУКТУРИРОВАННЫЙ список объектов
    (название, количество, единица измерения, габариты). Используй для точного подсчета объектов перед расчетом сметы.
    page_number - номер страницы для анализа, считая с 1 (в многостраничном проекте разные страницы - это разные
    планы/разрезы, их нельзя анализировать все вместе)."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен."

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if not (1 <= page_number <= doc.page_count):
        return f"Неверный номер страницы: {page_number}. В документе {doc.page_count} стр."
    page = doc.load_page(page_number - 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img_bytes = pix.tobytes("png")
    st.session_state["pdf_image_bytes"] = img_bytes
    base64_image = base64.b64encode(img_bytes).decode("utf-8")

    prompt_text = (
        "Ты инженер-чертежник. Внимательно изучи чертеж и посчитай ВСЕ объекты на нем "
        "(мебель, стены, двери, окна и т.п.), сгруппировав одинаковые элементы. "
        f"Дополнительное указание: {query or 'выполни общий подсчет всех объектов'}. "
        "Для каждого типа объекта укажи точное количество и габариты (мм), если они подписаны на чертеже."
    )
    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt_text},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": base64_image},
            },
        ]
    )

    try:
        llm = ChatAnthropic(model=CLAUDE_MODEL)
        structured_llm = llm.with_structured_output(DrawingAnalysis)
        result: DrawingAnalysis = structured_llm.invoke([message])
    except Exception as e:
        return f"Ошибка визуального анализа (проверьте ANTHROPIC_API_KEY): {e}"

    st.session_state["detected_objects"] = [obj.model_dump() for obj in result.objects]

    if not result.objects:
        return result.summary or "Объекты на чертеже не найдены."

    lines = [f"{o.name}: {o.quantity} {o.unit}" for o in result.objects]
    return f"{result.summary}\n\nНайдено типов объектов: {len(result.objects)}. " + "; ".join(lines)


def search_excel_price(material_name: str) -> str:
    """Ищет материал или фурнитуру в Excel смете по названию (например, 'ЛДСП', 'Петли', 'Фасад') и возвращает его цену и единицы измерения."""
    df = st.session_state.get("excel_df")
    if df is None:
        return "Excel не загружен."
    
    # Ищем совпадения в первом столбце (индекс 0)
    mask = df.apply(lambda row: row.astype(str).str.contains(material_name, case=False, na=False, regex=False).any(), axis=1)
    results = df[mask]
    
    if results.empty:
        return f"Позиция '{material_name}' не найдена."
    
    response = []
    for _, row in results.iterrows():
        name = row[0]
        unit = row[1] if len(row) > 1 else "шт"
        qty = row[2] if len(row) > 2 else 0
        price = row[3] if len(row) > 3 else 0
        response.append(f"Найдено: {name} | Ед.изм: {unit} | Кол-во: {qty} | Цена: {price}")
    return "; ".join(response)


def summarize_smeta_costs() -> str:
    """Считает себестоимость по загруженному листу сметы (формат калькулятора материалов: несколько блоков
    "ИТОГО" по разделам — материалы, фурнитура, обивка и т.д.). Суммирует все блоки "ИТОГО" в листе.
    Используй для смет-калькуляторов по материалам, где нет единого списка 'объект-цена'."""
    df = st.session_state.get("excel_df")
    if df is None:
        return "Excel-смета не загружена."

    itogo_rows = []
    subtotal = 0.0
    for _, row in df.iterrows():
        label = str(row[0]).strip() if row[0] is not None else ""
        if label == "ИТОГО" and len(row) > 4 and pd.notna(row[4]):
            value = pd.to_numeric(row[4], errors="coerce")
            if pd.notna(value):
                itogo_rows.append(value)
                subtotal += value

    if not itogo_rows:
        return "Блоки 'ИТОГО' не найдены в этом листе сметы. Возможно, это не калькулятор материалов."

    st.session_state["smeta_subtotal"] = subtotal
    return (
        f"Найдено блоков 'ИТОГО': {len(itogo_rows)}. "
        f"Себестоимость по материалам и фурнитуре: {subtotal:,.2f}. "
        "Это себестоимость до транспорта/упаковки и торговой наценки — итоговая цена клиенту "
        "определяется дополнительно (см. блок 'СТОИМОСТЬ' в файле)."
    )


def calculate_estimate() -> str:
    """Сопоставляет объекты, распознанные на чертеже (analyze_pdf_visuals_structured), со сметой в Excel
    и считает итоговую стоимость по каждой позиции и по проекту в целом. Используй после подсчета объектов."""
    objects = st.session_state.get("detected_objects")
    df = st.session_state.get("excel_df")

    if not objects:
        return "Сначала выполните подсчет объектов на чертеже."
    if df is None:
        return "Excel-смета не загружена."

    rows = []
    total = 0.0
    for obj in objects:
        name = obj.get("name", "")
        qty = obj.get("quantity", 0) or 0

        mask = df.apply(lambda row: row.astype(str).str.contains(name, case=False, na=False, regex=False).any(), axis=1)
        matches = df[mask]

        if matches.empty:
            rows.append({
                "Объект": name, "Кол-во (чертеж)": qty, "Ед.": obj.get("unit", "шт"),
                "Цена за ед.": None, "Сумма": None, "Статус": "Не найдено в смете",
            })
            continue

        match = matches.iloc[0]
        unit = match[1] if len(match) > 1 else obj.get("unit", "шт")
        price = pd.to_numeric(match[3], errors="coerce") if len(match) > 3 else None
        amount = round(price * qty, 2) if price is not None and not pd.isna(price) else None
        if amount is not None:
            total += amount

        rows.append({
            "Объект": name, "Кол-во (чертеж)": qty, "Ед.": unit,
            "Цена за ед.": price, "Сумма": amount,
            "Статус": "OK" if amount is not None else "Цена не найдена",
        })

    estimate_df = pd.DataFrame(rows)
    st.session_state["estimate_df"] = estimate_df
    st.session_state["estimate_total"] = total

    missing = estimate_df[estimate_df["Статус"] != "OK"]
    warning = f" Внимание: {len(missing)} позиций без цены." if not missing.empty else ""
    return f"Расчет выполнен. Позиций: {len(rows)}. Итоговая стоимость: {total:,.2f}.{warning}"


class RoomEntry(BaseModel):
    number: str = Field(description="Номер помещения, например '101'")
    name: str = Field(description="Название помещения, например 'Кухня-столовая'")
    area_m2: float = Field(description="Площадь помещения в м2")


MAX_PAGE_DRAWING_OBJECTS = 5000  # выше этого порога страница - это детальный чертеж, а не таблица; пропускаем


def _simple_pages(pdf_bytes: bytes) -> set:
    """Быстро (через PyMuPDF) находит страницы с небольшим числом векторных объектов - таблицы и текстовые
    листы. Детальные чертежи (штриховка, десятки тысяч линий) на порядок медленнее разбираются в pdfplumber,
    поэтому такие страницы нужно заранее исключать из тяжелых операций (поиск таблиц), чтобы не подвешивать
    обработку на несколько минут."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return {i for i, page in enumerate(doc) if len(page.get_drawings()) <= MAX_PAGE_DRAWING_OBJECTS}
    finally:
        doc.close()


ROOM_LIST_LINE = re.compile(r"^(\d{1,3})\.\s*(.+?)\s*[-–—]\s*(\d+[.,]?\d*)\s*(?:м2|м²|m2)?$", re.IGNORECASE)
AREA_TOKEN = re.compile(r"(\d+[.,]?\d*)\s*(?:м2|м²|m2)", re.IGNORECASE)


def _parse_room_list_text(text: str) -> Tuple[List["RoomEntry"], Optional[float]]:
    """Резервный разбор экспликации, оформленной обычным нумерованным списком в тексте чертежа
    (например '1. ТАМБУР - 8,6' ... 'ИТОГО: S ПОЛЕЗНАЯ - 150,0 М2'), а не бордюрной таблицей."""
    rooms: List[RoomEntry] = []
    total: Optional[float] = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = ROOM_LIST_LINE.match(line)
        if match:
            number, name, area_raw = match.groups()
            try:
                area = float(area_raw.replace(",", "."))
            except ValueError:
                continue
            rooms.append(RoomEntry(number=number, name=name.strip(), area_m2=area))
            continue
        if "итого" in line.lower():
            # Берем ПОСЛЕДНЕЕ число вида "N м2" в строке - само слово "итого" и текст перед числом
            # (например "S ПОЛЕЗНАЯ 1-ГО ЭТАЖА") часто содержат посторонние цифры.
            area_tokens = AREA_TOKEN.findall(line)
            if area_tokens:
                try:
                    total = float(area_tokens[-1].replace(",", "."))
                except ValueError:
                    pass
    return rooms, total


def extract_room_schedule() -> str:
    """Извлекает таблицу экспликации помещений (№, наименование, площадь) из PDF-чертежа, если она есть,
    и сверяет сумму площадей с итоговой площадью в таблице. Используй для точного подсчета площадей комнат."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен."

    rooms: List[RoomEntry] = []
    stated_total: Optional[float] = None
    simple_pages = _simple_pages(pdf_bytes)
    skipped = 0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_index, page in enumerate(pdf.pages):
            if page_index not in simple_pages:
                skipped += 1
                continue
            for table in page.extract_tables():
                if not table or not table[0]:
                    continue
                header = [str(c or "").strip().lower() for c in table[0]]
                if not any("наимен" in c for c in header) or not any("площад" in c for c in header):
                    continue

                name_idx = next((i for i, c in enumerate(header) if "наимен" in c), 1)
                area_idx = next((i for i, c in enumerate(header) if "площад" in c), len(header) - 1)
                num_idx = next((i for i, c in enumerate(header) if c.strip() in ("№", "n", "no")), 0)

                for data_row in table[1:]:
                    if not data_row or len(data_row) <= max(name_idx, area_idx):
                        continue
                    name = str(data_row[name_idx] or "").strip()
                    area_raw = str(data_row[area_idx] or "").strip().replace(",", ".")
                    number = str(data_row[num_idx] or "").strip()
                    if not name and not area_raw:
                        continue
                    try:
                        area = float(area_raw)
                    except ValueError:
                        continue
                    if not number.isdigit() and area:
                        # Итоговая строка без номера (например "99,51 м²")
                        stated_total = area
                        continue
                    rooms.append(RoomEntry(number=number, name=name, area_m2=area))

    skip_note = f" ({skipped} стр. пропущено как слишком детальные чертежи)" if skipped else ""
    list_fallback = False
    if not rooms:
        # Резервный вариант: помещения оформлены нумерованным текстовым списком, а не таблицей.
        # Текстовое извлечение через PyMuPDF быстрое даже на очень детальных страницах, поэтому
        # здесь можно смотреть весь документ без ограничения по сложности страницы.
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            for page in doc:
                found_rooms, found_total = _parse_room_list_text(page.get_text())
                if found_rooms:
                    rooms.extend(found_rooms)
                    if found_total is not None:
                        stated_total = found_total
                    list_fallback = True
        finally:
            doc.close()

    if not rooms:
        return (
            "Таблица/список экспликации помещений не найдены в PDF (нет извлекаемого текста с колонками "
            f"'Наименование'/'Площадь' и нет нумерованного списка вида '1. Название - Площадь'){skip_note}."
        )

    st.session_state["room_schedule"] = [r.model_dump() for r in rooms]
    computed_total = round(sum(r.area_m2 for r in rooms), 2)
    st.session_state["room_schedule_total"] = computed_total

    lines = [f"{r.number} {r.name}: {r.area_m2} м2" for r in rooms]
    check = ""
    if stated_total is not None:
        diff = round(computed_total - stated_total, 2)
        check = (
            f" Указанная в чертеже общая площадь: {stated_total} м2. Расхождение с суммой по помещениям: {diff} м2."
            if abs(diff) > 0.01
            else f" Совпадает с указанной в чертеже общей площадью ({stated_total} м2)."
        )

    source_note = " (формат: нумерованный список)" if list_fallback else ""
    return f"Помещений найдено: {len(rooms)}. Суммарная площадь: {computed_total} м2.{check}{skip_note}{source_note} " + "; ".join(lines)


def render_pdf_preview(dpi: int = 150, page_number: int = 1) -> Optional[bytes]:
    """Рендерит страницу PDF-чертежа в PNG для визуального предпросмотра в интерфейсе.
    page_number - номер страницы, считая с 1."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return None

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_number = max(1, min(page_number, doc.page_count))
    page = doc.load_page(page_number - 1)
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img_bytes = pix.tobytes("png")
    st.session_state["pdf_preview_bytes"] = img_bytes
    return img_bytes


DEFAULT_LAYERS = (
    ("LINES", 7), ("RECTS", 5), ("CURVES", 3), ("WALLS", 1), ("LABELS", 2),
)


def _new_dxf_doc() -> "ezdxf.document.Drawing":
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$INSUNITS"] = ezdxf.units.MM
    for layer_name, color in DEFAULT_LAYERS:
        if layer_name not in doc.layers:
            doc.layers.add(layer_name, dxfattribs={"color": color})
    return doc


def _get_dxf_doc() -> "ezdxf.document.Drawing":
    """Возвращает текущий CAD-проект в памяти (создает новый пустой, если его еще нет)."""
    doc = st.session_state.get("dxf_doc")
    if doc is None:
        doc = _new_dxf_doc()
        st.session_state["dxf_doc"] = doc
    return doc


def _export_dxf_doc(doc) -> bytes:
    buffer = io.StringIO()
    doc.write(buffer)
    data = buffer.getvalue().encode("utf-8")
    st.session_state["dxf_data"] = data
    return data


MAX_DXF_ENTITIES = 200_000  # защита от многочасовой генерации/неоткрываемых файлов на сверхдетальных чертежах


def generate_dxf_file(scale: float = 1.0, wall_height_mm: float = 0, page_number: int = 1) -> str:
    """Генерирует файл AutoCAD (.dxf) на основе извлеченных из PDF векторных линий, прямоугольников и кривых
    ОДНОЙ страницы. Начинает новый CAD-проект в памяти, к которому потом можно добавлять элементы через чат
    (add_wall и т.п.). scale - коэффициент масштабирования (мм на единицу PDF), позволяет привести чертеж к
    реальным размерам. wall_height_mm - если больше 0, линии и прямоугольники получают вертикальную экструзию
    (thickness) на эту высоту в мм — при открытии в AutoCAD они отображаются как 3D-стены (псевдо-3D).
    page_number - номер страницы для конвертации, считая с 1 (в многостраничном проекте страницы обычно
    содержат разные, несовместимые по масштабу и содержанию виды - план, разрезы, фасады - поэтому
    конвертируется только одна выбранная страница, а не все сразу)."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен, невозможно создать DXF."

    doc = _new_dxf_doc()
    msp = doc.modelspace()
    total_entities = 0
    truncated = False
    wall_attribs = {"thickness": wall_height_mm} if wall_height_mm else {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not (1 <= page_number <= len(pdf.pages)):
            return f"Неверный номер страницы: {page_number}. В документе {len(pdf.pages)} стр."
        page = pdf.pages[page_number - 1]
        h = page.height

        def to_dxf(x, y):
            return (round(x * scale, 3), round((h - y) * scale, 3))

        for line in page.lines:
            if total_entities >= MAX_DXF_ENTITIES:
                truncated = True
                break
            start = to_dxf(line["x0"], line["y0"])
            end = to_dxf(line["x1"], line["y1"])
            msp.add_line(start, end, dxfattribs={"layer": "LINES", **wall_attribs})
            total_entities += 1

        for rect in page.rects:
            if total_entities >= MAX_DXF_ENTITIES:
                truncated = True
                break
            x0, y0, x1, y1 = rect["x0"], rect["top"], rect["x1"], rect["bottom"]
            points = [to_dxf(x0, y0), to_dxf(x1, y0), to_dxf(x1, y1), to_dxf(x0, y1)]
            msp.add_lwpolyline(points, close=True, dxfattribs={"layer": "RECTS", **wall_attribs})
            total_entities += 1

        for curve in page.curves:
            if total_entities >= MAX_DXF_ENTITIES:
                truncated = True
                break
            pts = curve.get("pts") or []
            if len(pts) >= 2:
                points = [to_dxf(px, py) for px, py in pts]
                msp.add_lwpolyline(points, dxfattribs={"layer": "CURVES"})
                total_entities += 1

    if total_entities == 0:
        return f"На странице {page_number} не найдено векторных линий/фигур для конвертации в DXF."

    st.session_state["dxf_doc"] = doc
    _export_dxf_doc(doc)
    extrusion_note = f", экструзия стен {wall_height_mm} мм (псевдо-3D)" if wall_height_mm else ""
    truncation_note = (
        f" Внимание: чертеж очень детальный, перенесены первые {MAX_DXF_ENTITIES} объектов из большего числа."
        if truncated
        else ""
    )
    return (
        f"DXF файл успешно сгенерирован (стр. {page_number}, {total_entities} объектов, масштаб {scale}"
        f"{extrusion_note}) и готов к скачиванию. Этот чертеж также стал текущим CAD-проектом — можно "
        f"добавлять элементы командами в чате.{truncation_note}"
    )


def add_wall(x0: float, y0: float, x1: float, y1: float, height_mm: float = 2700) -> str:
    """Добавляет стену (прямую линию) в текущий CAD-проект в памяти. Координаты x0,y0,x1,y1 в мм.
    height_mm - высота стены, задает 3D-экструзию (thickness), по умолчанию 2700 мм (стандартный этаж)."""
    doc = _get_dxf_doc()
    msp = doc.modelspace()
    msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "WALLS", "thickness": height_mm})
    _export_dxf_doc(doc)
    return f"Стена добавлена: ({x0}, {y0}) -> ({x1}, {y1}), высота {height_mm} мм. Всего объектов в проекте: {len(msp)}."


def add_room_label(x: float, y: float, text: str, height_mm: float = 250) -> str:
    """Добавляет текстовую подпись (название или площадь комнаты) в точке x,y (мм) текущего CAD-проекта."""
    doc = _get_dxf_doc()
    msp = doc.modelspace()
    entity = msp.add_text(text, dxfattribs={"layer": "LABELS", "height": height_mm})
    entity.set_placement((x, y))
    _export_dxf_doc(doc)
    return f"Подпись '{text}' добавлена в точке ({x}, {y}). Всего объектов в проекте: {len(msp)}."


def remove_last_entity() -> str:
    """Удаляет последний добавленный объект в текущем CAD-проекте (отмена последнего действия)."""
    doc = st.session_state.get("dxf_doc")
    if doc is None:
        return "CAD-проект пуст."
    msp = doc.modelspace()
    entities = list(msp)
    if not entities:
        return "CAD-проект пуст."
    last = entities[-1]
    dxftype = last.dxftype()
    msp.delete_entity(last)
    _export_dxf_doc(doc)
    return f"Последний объект ({dxftype}) удален. Осталось объектов: {len(msp)}."


def list_dxf_entities() -> str:
    """Показывает сводку по текущему CAD-проекту в памяти: количество объектов по типам и слоям.
    Используй, чтобы понять текущее состояние чертежа перед тем, как вносить правки."""
    doc = st.session_state.get("dxf_doc")
    if doc is None:
        return "CAD-проект пуст. Сначала сгенерируйте DXF из PDF (generate_dxf_file) или добавьте элементы."
    msp = doc.modelspace()
    entities = list(msp)
    if not entities:
        return "CAD-проект пуст (0 объектов)."

    types: dict = {}
    layers: dict = {}
    for e in entities:
        types[e.dxftype()] = types.get(e.dxftype(), 0) + 1
        layer = e.dxf.layer
        layers[layer] = layers.get(layer, 0) + 1

    return f"Объектов всего: {len(entities)}. По типам: {types}. По слоям: {layers}."


def reset_cad_project() -> str:
    """Полностью очищает текущий CAD-проект в памяти и начинает новый пустой чертеж."""
    doc = _new_dxf_doc()
    st.session_state["dxf_doc"] = doc
    _export_dxf_doc(doc)
    return "Новый пустой CAD-проект создан."


def extract_wall_segments(scale: float = 1.0, page_number: int = 1) -> List[Tuple[float, float, float, float]]:
    """Извлекает отрезки стен (линии и стороны прямоугольников) с ОДНОЙ страницы PDF в реальных координатах
    (мм), для 3D-визуализации плана в браузере. Использует ту же логику переноса координат, что и DXF-экспорт."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return []

    segments: List[Tuple[float, float, float, float]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not (1 <= page_number <= len(pdf.pages)):
            return []
        page = pdf.pages[page_number - 1]
        h = page.height

        def to_xy(x, y):
            return (round(x * scale, 2), round((h - y) * scale, 2))

        for line in page.lines:
            if len(segments) >= MAX_DXF_ENTITIES:
                break
            x0, y0 = to_xy(line["x0"], line["y0"])
            x1, y1 = to_xy(line["x1"], line["y1"])
            segments.append((x0, y0, x1, y1))

        for rect in page.rects:
            if len(segments) >= MAX_DXF_ENTITIES:
                break
            p0 = to_xy(rect["x0"], rect["top"])
            p1 = to_xy(rect["x1"], rect["top"])
            p2 = to_xy(rect["x1"], rect["bottom"])
            p3 = to_xy(rect["x0"], rect["bottom"])
            segments.extend([(*p0, *p1), (*p1, *p2), (*p2, *p3), (*p3, *p0)])

    return segments


_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "vendor")


def _read_vendor_js(filename: str) -> str:
    path = os.path.join(_VENDOR_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def generate_3d_preview_html(
    wall_height_mm: float = 2700, wall_thickness_mm: float = 150, scale: float = 1.0, page_number: int = 1,
) -> str:
    """Строит самодостаточную 3D-сцену (Three.js) из линий/прямоугольников одной страницы чертежа - стены
    выдавливаются на заданную высоту, с камерой, светом и автоповоротом. Возвращает готовый HTML для вставки
    через streamlit.components.v1.html. Three.js встроен в HTML напрямую (без CDN), поэтому работает при
    любом хостинге. Используй для наглядного 3D-просмотра плана в интерфейсе."""
    segments = extract_wall_segments(scale, page_number)
    if not segments:
        return ""

    three_js = _read_vendor_js("three.min.js")
    orbit_controls_js = _read_vendor_js("OrbitControls.js")
    segments_json = json.dumps(segments)

    return f"""
<div id="viewer3d" style="width:100%; height:600px; background:linear-gradient(#dfe9f3,#f7fafc);
     border-radius:12px; overflow:hidden;"></div>
<script>{three_js}</script>
<script>{orbit_controls_js}</script>
<script>
(function () {{
    const segments = {segments_json};
    const wallHeight = {wall_height_mm};
    const wallThickness = {wall_thickness_mm};
    const container = document.getElementById('viewer3d');

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    segments.forEach(function (s) {{
        minX = Math.min(minX, s[0], s[2]); maxX = Math.max(maxX, s[0], s[2]);
        minY = Math.min(minY, s[1], s[3]); maxY = Math.max(maxY, s[1], s[3]);
    }});
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const size = Math.max(maxX - minX, maxY - minY, 1000);

    // Высота стен и габариты плана бывают в разных, несовместимых единицах (например план еще не
    // приведен к реальному масштабу) - camera подстраивается под БОЛЬШЕЕ из двух измерений, чтобы сцена
    // всегда была видна целиком, а не "внутри" стены.
    const maxDim = Math.max(size, wallHeight * 2.5);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, container.clientWidth / 600, maxDim / 1000, maxDim * 10);
    camera.position.set(cx + maxDim * 0.9, maxDim * 0.8, cy + maxDim * 0.9);

    const renderer = new THREE.WebGLRenderer({{ antialias: true }});
    renderer.setSize(container.clientWidth, 600);
    renderer.shadowMap.enabled = true;
    container.appendChild(renderer.domElement);

    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(cx, wallHeight / 3, cy);
    controls.autoRotate = true;
    controls.autoRotateSpeed = 1.2;
    controls.enableDamping = true;

    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    const sun = new THREE.DirectionalLight(0xffffff, 0.85);
    sun.position.set(cx + maxDim, maxDim * 1.2, cy + maxDim);
    sun.castShadow = true;
    scene.add(sun);

    const floor = new THREE.Mesh(
        new THREE.PlaneGeometry(size * 1.6, size * 1.6),
        new THREE.MeshStandardMaterial({{ color: 0xf2ede4 }})
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(cx, 0, cy);
    floor.receiveShadow = true;
    scene.add(floor);

    const wallMat = new THREE.MeshStandardMaterial({{ color: 0x8ea9c1 }});
    segments.forEach(function (seg) {{
        const x0 = seg[0], y0 = seg[1], x1 = seg[2], y1 = seg[3];
        const dx = x1 - x0, dy = y1 - y0;
        const length = Math.sqrt(dx * dx + dy * dy);
        if (length < 1) return;
        const mesh = new THREE.Mesh(
            new THREE.BoxGeometry(length, wallHeight, wallThickness),
            wallMat
        );
        mesh.position.set((x0 + x1) / 2, wallHeight / 2, (y0 + y1) / 2);
        mesh.rotation.y = -Math.atan2(dy, dx);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        scene.add(mesh);
    }});

    function animate() {{
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    }}
    animate();

    window.addEventListener('resize', function () {{
        camera.aspect = container.clientWidth / 600;
        camera.updateProjectionMatrix();
        renderer.setSize(container.clientWidth, 600);
    }});
}})();
</script>
"""