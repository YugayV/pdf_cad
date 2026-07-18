import streamlit as st
import pandas as pd
import os
from agent import get_agent, process_vision_query
import io

st.set_page_config(page_title="AI Мозг: Чертежи и Сметы", layout="wide", page_icon="🧠")

# Инициализация состояния
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "excel_df" not in st.session_state:
    st.session_state.excel_df = None
if "dxf_data" not in st.session_state:
    st.session_state.dxf_data = None

st.title("🧠 AI-Агент: Анализ чертежей и смет")

# Проверка API ключа
if not os.getenv("OPENAI_API_KEY"):
    st.error("⚠️ OPENAI_API_KEY не найден. Добавьте его в переменные окружения Railway!")
    st.stop()

# --- Сайдбар для загрузки файлов ---
with st.sidebar:
    st.header("1. Загрузка данных")
    uploaded_pdf = st.file_uploader("PDF Чертеж", type=["pdf"])
    uploaded_excel = st.file_uploader("Excel Смета", type=["xlsx", "xls"])
    
    if uploaded_pdf:
        st.session_state.pdf_bytes = uploaded_pdf.getvalue()
        st.success("PDF загружен!")
        
    if uploaded_excel:
        # Читаем первый лист (можно добавить выпадающий список)
        xls = pd.ExcelFile(uploaded_excel)
        sheet = st.selectbox("Выберите лист", xls.sheet_names)
        st.session_state.excel_df = pd.read_excel(xls, sheet_name=sheet, header=None)
        st.success(f"Лист '{sheet}' загружен!")

# --- Главный экран ---
tab1, tab2, tab3 = st.tabs(["💬 Диалог с агентом", "📊 Просмотр сметы", "📐 AutoCAD (DXF)"])

with tab1:
    st.subheader("Поставьте задачу ИИ-мозгу")
    
    prompt = st.text_area(
        "Что нужно сделать?", 
        "Например: 1. Посмотри на чертеж (analyze_pdf_visuals) и посчитай количество шкафов. 2. Узнай цену ЛДСП в Excel. 3. Посчитай примерную стоимость корпуса. 4. Сгенерируй DXF файл."
    )
    
    if st.button("🚀 Запустить Агента", type="primary"):
        if not st.session_state.pdf_bytes or st.session_state.excel_df is None:
            st.warning("Загрузите и PDF, и Excel в левом меню!")
        else:
            with st.spinner("Агент анализирует данные и выполняет задачу..."):
                try:
                    agent = get_agent()
                    
                    # Даем агенту системный контекст
                    context = """
                    Ты — главный инженер-аналитик мебельной фабрики.
                    У тебя есть доступ к PDF чертежу (через инструменты) и Excel смете.
                    Шаг 1: Если задача требует визуального анализа, вызови analyze_pdf_visuals, а затем используй функцию process_vision_query (она доступна в контексте) для анализа картинки.
                    Шаг 2: Ищи цены в Excel через search_excel_price.
                    Шаг 3: Генерируй DXF через generate_dxf_file.
                    Отвечай структурированно и по делу.
                    """
                    
                    # Запускаем агента
                    response = agent.run(f"{context}\n\nЗадача пользователя: {prompt}")
                    
                    st.success("Задача выполнена!")
                    st.markdown("### 📝 Ответ Агента:")
                    st.write(response)
                    
                except Exception as e:
                    st.error(f"Ошибка: {e}")

with tab2:
    st.subheader("Данные сметы")
    if st.session_state.excel_df is not None:
        st.dataframe(st.session_state.excel_df, use_container_width=True)
    else:
        st.info("Загрузите Excel файл.")

with tab3:
    st.subheader("Сгенерированный файл AutoCAD")
    if st.session_state.dxf_data:
        st.success("Файл готов!")
        st.download_button(
            label="⬇️ Скачать DXF",
            data=st.session_state.dxf_data,
            file_name="furniture_plan.dxf",
            mime="application/dxf"
        )
    else:
        st.info("Файл DXF еще не сгенерирован. Попросите агента сделать это в вкладке 'Диалог'.")