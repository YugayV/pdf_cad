import streamlit as st
import pandas as pd
import os
from agent import get_agent
import io

# --- НАСТРОЙКА СТИЛЕЙ (МИНИМАЛИЗМ) ---
st.set_page_config(page_title="AI Furniture Brain", page_icon="📐", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
        /* Скрыть стандартное меню и футер Streamlit */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        
        /* Основной шрифт и фон */
        .stApp {
            background-color: #f8f9fa;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }
        
        /* Заголовки */
        h1, h2, h3 {
            color: #1a1d23 !important;
            font-weight: 600 !important;
        }
        
        /* Карточки для контента */
        .css-1y4p8pa, .css-1v0mbdj {
            background-color: #ffffff;
            padding: 2rem;
            border-radius: 12px;
            border: 1px solid #e8e8e8;
            box-shadow: 0 4px 6px rgba(0,0,0,0.02);
        }
        
        /* Кнопки */
        .stButton>button {
            background-color: #2c3e50;
            color: white;
            border-radius: 8px;
            border: none;
            padding: 0.6rem 1.5rem;
            font-weight: 500;
            transition: all 0.2s;
        }
        .stButton>button:hover {
            background-color: #1a252f;
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(44,62,80,0.2);
        }
        
        /* Боковая панель */
        .css-1d391kg {
            background-color: #ffffff;
            border-right: 1px solid #e8e8e8;
        }
        
        /* Поле ввода текста */
        .stTextArea>div>div {
            border-radius: 8px;
            border: 1px solid #e8e8e8 !important;
        }
    </style>
""", unsafe_allow_html=True)

# --- ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ ---
if "pdf_bytes" not in st.session_state:
    st.session_state.pdf_bytes = None
if "excel_df" not in st.session_state:
    st.session_state.excel_df = None
if "dxf_data" not in st.session_state:
    st.session_state.dxf_data = None
if "agent_response" not in st.session_state:
    st.session_state.agent_response = None

# --- ПРОВЕРКА API ---
if not os.getenv("OPENAI_API_KEY"):
    st.error("⚠️ Ошибка: OPENAI_API_KEY не найден в переменных окружения Railway!")
    st.stop()

# --- БОКОВОЕ МЕНЮ (ЗАГРУЗКА ДОКУМЕНТОВ) ---
with st.sidebar:
    st.markdown("### 📂 Документы")
    st.markdown("Загрузите файлы для анализа")
    
    uploaded_pdf = st.file_uploader("PDF Чертеж", type=["pdf"], label_visibility="collapsed")
    if uploaded_pdf:
        st.session_state.pdf_bytes = uploaded_pdf.getvalue()
        st.success("✅ PDF загружен", icon="✅")
        
    uploaded_excel = st.file_uploader("Excel Смета", type=["xlsx", "xls"], label_visibility="collapsed")
    if uploaded_excel:
        try:
            xls = pd.ExcelFile(uploaded_excel)
            sheet = st.selectbox("Лист сметы", xls.sheet_names)
            st.session_state.excel_df = pd.read_excel(xls, sheet_name=sheet, header=None)
            st.success("✅ Excel загружен", icon="✅")
        except Exception as e:
            st.error(f"Ошибка Excel: {e}")

# --- ГЛАВНЫЙ ЭКРАН ---
st.markdown("## 📐 AI-Аналитик: Чертежи и Сметы")
st.markdown("Загрузите документы в левом меню и поставьте задачу ИИ-агенту. Он проанализирует чертеж, сопоставит со сметой и подготовит файлы.")
st.markdown("---")

# Создаем вкладки
tab1, tab2, tab3 = st.tabs(["🤖 Рабочая область", "📊 Таблица сметы", "⬇️ Экспорт AutoCAD"])

with tab1:
    # Карточка с задачей
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.markdown("#### Задача для Агента")
        prompt = st.text_area(
            "Что нужно сделать?", 
            "Например: 1. Посмотри чертеж и найди все объекты. 2. Узнай цену ЛДСП и Фасадов в смете. 3. Посчитай итог. 4. Сгенерируй DXF файл.",
            label_visibility="collapsed",
            height=120
        )
    
    with col2:
        st.markdown("#### Управление")
        if st.button("🚀 Запустить Агента", use_container_width=True):
            if not st.session_state.pdf_bytes or st.session_state.excel_df is None:
                st.warning("Сначала загрузите PDF и Excel в левом меню!")
            else:
                with st.spinner("Агент анализирует данные..."):
                    try:
                        agent = get_agent()
                        context = "Ты инженер-аналитик. У тебя есть PDF и Excel. Выполни задачу шаг за шагом."
                        st.session_state.agent_response = agent.run(f"{context}\n\nЗадача: {prompt}")
                    except Exception as e:
                        st.session_state.agent_response = f"Ошибка: {e}"
        st.markdown("") # Отступ
        
    # Вывод результата
    if st.session_state.agent_response:
        st.markdown("---")
        st.markdown("#### 📝 Результат работы:")
        
        # Стилизованный вывод ответа
        st.markdown(
            f"""
            <div style="background-color: #ffffff; padding: 20px; border-radius: 10px; border-left: 4px solid #2c3e50; margin-top: 10px;">
                <p style="color: #333; line-height: 1.6;">{st.session_state.agent_response}</p>
            </div>
            """, 
            unsafe_allow_html=True
        )
    else:
        st.info("💡 Здесь появится ответ агента после запуска задачи.")

with tab2:
    st.markdown("#### Данные сметы")
    if st.session_state.excel_df is not None:
        # Стилизуем вывод датафрейма
        st.dataframe(
            st.session_state.excel_df, 
            use_container_width=True,
            height=500
        )
    else:
        st.warning("Excel файл не загружен.")

with tab3:
    st.markdown("#### Экспорт в AutoCAD")
    if st.session_state.dxf_data:
        st.success("Файл успешно сгенерирован агентом!")
        
        # Красивая карточка скачивания
        col1, col2 = st.columns([1, 3])
        with col1:
            st.download_button(
                label="⬇️ Скачать .DXF",
                data=st.session_state.dxf_data,
                file_name="furniture_plan.dxf",
                mime="application/dxf",
                use_container_width=True
            )
        with col2:
            st.markdown("Файл готов к открытию в AutoCAD или любом другом CAD-редакторе.")
    else:
        st.info("Файл DXF еще не создан. Попросите агента сгенерировать его в рабочей области.")