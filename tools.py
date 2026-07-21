import pdfplumber
import fitz  # PyMuPDF
import ezdxf
import io
import pandas as pd
from PIL import Image
import streamlit as st

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

def search_excel_price(material_name: str) -> str:
    """Ищет материал или фурнитуру в Excel смете по названию (например, 'ЛДСП', 'Петли', 'Фасад') и возвращает его цену и единицы измерения."""
    df = st.session_state.get("excel_df")
    if df is None:
        return "Excel не загружен."
    
    # Ищем совпадения в первом столбце (индекс 0)
    mask = df.apply(lambda row: row.astype(str).str.contains(material_name, case=False, na=False).any(), axis=1)
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

def generate_dxf_file(instructions: str = "") -> str:
    """Генерирует файл AutoCAD (.dxf) на основе извлеченных из PDF векторных линий и прямоугольников."""
    pdf_bytes = st.session_state.get("pdf_bytes")
    if not pdf_bytes:
        return "PDF не загружен, невозможно создать DXF."
    
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            h = page.height
            # Перенос линий
            for line in page.lines:
                msp.add_line((line['x1'], h - line['top']), (line['x2'], h - line['bottom']))
            # Перенос прямоугольников
            for rect in page.rects:
                x0, y0, x1, y1 = rect['x0'], rect['top'], rect['x1'], rect['bottom']
                msp.add_lwpolyline([(x0, h-y0), (x1, h-y0), (x1, h-y1), (x0, h-y1)], close=True)
    
    buffer = io.StringIO()
    doc.write(buffer)
    st.session_state["dxf_data"] = buffer.getvalue().encode('utf-8')
    return "DXF файл успешно сгенерирован и готов к скачиванию в интерфейсе."