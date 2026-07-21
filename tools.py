import base64
import io
from typing import List, Optional

import ezdxf
import fitz  # PyMuPDF
import pandas as pd
import pdfplumber
import streamlit as st
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

def extract_text_from_pdf(query: str = "") -> str:
    """Извлекает весь текст из загруженного PDF чертежа. Используй, если нужно найти размеры, надписи или спецификации."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен."
    text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text[:3000] # Ограничиваем для контекста

def analyze_pdf_visuals(query: str = "") -> str:
    """Анализирует визуальную часть PDF (чертеж) с помощью ИИ. Используй, чтобы найти объекты (шкафы, стены), посчитать их количество или понять геометрию."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен."

    # Конвертируем первую страницу PDF в картинку
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap()
    img_bytes = pix.tobytes("png")

    # Возвращаем путь к картинке для Vision-модели (обработка в agent.py)
    st.session_state["pdf_image_bytes"] = img_bytes
    return "Изображение первой страницы PDF сохранено. Передай его в систему для визуального анализа."


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


def analyze_pdf_visuals_structured(query: str = "") -> str:
    """Анализирует визуальную часть PDF-чертежа через GPT-4o Vision и возвращает СТРУКТУРИРОВАННЫЙ список объектов
    (название, количество, единица измерения, габариты). Используй для точного подсчета объектов перед расчетом сметы."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен."

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
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
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
        ]
    )

    try:
        llm = ChatOpenAI(model="gpt-4o", temperature=0)
        structured_llm = llm.with_structured_output(DrawingAnalysis)
        result: DrawingAnalysis = structured_llm.invoke([message])
    except Exception as e:
        return f"Ошибка визуального анализа (проверьте OPENAI_API_KEY): {e}"

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


def render_pdf_preview(dpi: int = 150) -> Optional[bytes]:
    """Рендерит первую страницу PDF-чертежа в PNG для визуального предпросмотра в интерфейсе."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return None

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img_bytes = pix.tobytes("png")
    st.session_state["pdf_preview_bytes"] = img_bytes
    return img_bytes


def generate_dxf_file(scale: float = 1.0) -> str:
    """Генерирует файл AutoCAD (.dxf) на основе извлеченных из PDF векторных линий, прямоугольников и кривых.
    scale - коэффициент масштабирования (мм на единицу PDF), позволяет привести чертеж к реальным размерам."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен, невозможно создать DXF."

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$INSUNITS"] = ezdxf.units.MM

    for layer_name, color in (("LINES", 7), ("RECTS", 5), ("CURVES", 3)):
        if layer_name not in doc.layers:
            doc.layers.add(layer_name, dxfattribs={"color": color})

    msp = doc.modelspace()
    total_entities = 0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            h = page.height

            def to_dxf(x, y, h=h):
                return (round(x * scale, 3), round((h - y) * scale, 3))

            for line in page.lines:
                start = to_dxf(line["x0"], line["y0"])
                end = to_dxf(line["x1"], line["y1"])
                msp.add_line(start, end, dxfattribs={"layer": "LINES"})
                total_entities += 1

            for rect in page.rects:
                x0, y0, x1, y1 = rect["x0"], rect["top"], rect["x1"], rect["bottom"]
                points = [to_dxf(x0, y0), to_dxf(x1, y0), to_dxf(x1, y1), to_dxf(x0, y1)]
                msp.add_lwpolyline(points, close=True, dxfattribs={"layer": "RECTS"})
                total_entities += 1

            for curve in page.curves:
                pts = curve.get("pts") or []
                if len(pts) >= 2:
                    points = [to_dxf(px, py) for px, py in pts]
                    msp.add_lwpolyline(points, dxfattribs={"layer": "CURVES"})
                    total_entities += 1

    if total_entities == 0:
        return "В PDF не найдено векторных линий/фигур для конвертации в DXF."

    buffer = io.StringIO()
    doc.write(buffer)
    st.session_state["dxf_data"] = buffer.getvalue().encode("utf-8")
    return f"DXF файл успешно сгенерирован ({total_entities} объектов, масштаб {scale}) и готов к скачиванию."