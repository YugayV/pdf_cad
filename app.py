import streamlit as st
import pandas as pd
import streamlit.components.v1 as components
from agent import get_agent
from tools import (
    analyze_pdf_visuals_structured,
    calculate_estimate,
    extract_room_schedule,
    extract_text_from_pdf,
    generate_3d_preview_html,
    generate_dxf_file,
    get_pdf_page_count,
    load_input_as_pdf_bytes,
    render_pdf_preview,
    search_excel_price,
    summarize_smeta_costs,
)

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
for key in (
    "pdf_bytes", "excel_df", "dxf_data", "dxf_doc", "agent_response",
    "detected_objects", "estimate_df", "estimate_total", "pdf_preview_bytes",
    "room_schedule", "room_schedule_total", "chat_agent", "preview_3d_html",
):
    if key not in st.session_state:
        st.session_state[key] = None
if "chat_log" not in st.session_state:
    st.session_state.chat_log = []
if "page_number" not in st.session_state:
    st.session_state.page_number = 1

# --- БОКОВОЕ МЕНЮ (ЗАГРУЗКА ДОКУМЕНТОВ) ---
with st.sidebar:
    st.markdown("### 📂 Документы")
    st.markdown("Загрузите файлы для анализа")
    
    uploaded_pdf = st.file_uploader(
        "Чертеж (PDF, JPG, JPEG, PNG)", type=["pdf", "jpg", "jpeg", "png"], label_visibility="collapsed",
    )
    if uploaded_pdf:
        try:
            st.session_state.pdf_bytes = load_input_as_pdf_bytes(uploaded_pdf.getvalue(), uploaded_pdf.name)
            if uploaded_pdf.name.lower().endswith((".pdf",)):
                st.success("✅ PDF загружен", icon="✅")
            else:
                st.success("✅ Изображение загружено и сконвертировано в PDF", icon="✅")
        except Exception as e:
            st.error(f"Не удалось обработать файл: {e}")

    if st.session_state.pdf_bytes:
        page_count = get_pdf_page_count()
        if page_count > 1:
            st.number_input(
                f"Страница чертежа (всего {page_count})",
                min_value=1, max_value=page_count, value=1, step=1, key="page_number",
                help="Многостраничный документ: план, разрезы, фасады, экспликации — обычно на разных "
                     "страницах, поэтому анализ/DXF/предпросмотр работают с одной выбранной страницей.",
            )
        else:
            st.session_state.page_number = 1


    uploaded_excel = st.file_uploader("Excel Смета", type=["xlsx", "xls"], label_visibility="collapsed")
    if uploaded_excel:
        try:
            xls = pd.ExcelFile(uploaded_excel)
            sheet = st.selectbox("Лист сметы", xls.sheet_names)
            st.session_state.excel_df = pd.read_excel(xls, sheet_name=sheet, header=None)
            st.success("✅ Excel загружен", icon="✅")
        except Exception as e:
            st.error(f"Ошибка Excel: {e}")

def render_agent_response(empty_message: str) -> None:
    if st.session_state.agent_response:
        st.markdown("---")
        st.markdown("#### 📝 Результат:")
        st.markdown(
            f"""
            <div style="background-color: #ffffff; padding: 20px; border-radius: 10px; border-left: 4px solid #2c3e50; margin-top: 10px;">
                <p style="color: #333; line-height: 1.6;">{st.session_state.agent_response}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.info(empty_message)


# --- ГЛАВНЫЙ ЭКРАН ---
st.markdown("## 📐 PDF → AutoCAD: анализ, подсчёт и графика")
st.markdown("Загрузите документы в левом меню: извлечение текста, ИИ-подсчёт объектов, расчёт сметы, генерация DXF (в т.ч. псевдо-3D) и визуализация.")
st.markdown("---")

# Создаем вкладки
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🤖 Рабочая область", "🧮 Подсчёт и смета", "📊 Таблица сметы", "⬇️ Экспорт AutoCAD", "📈 Графика", "💬 CAD-чат",
])

with tab1:
    st.markdown("#### Локальные действия")
    pdf_col, excel_col, dxf_col = st.columns(3)

    with pdf_col:
        if st.button("📄 Извлечь текст из PDF", use_container_width=True):
            if not st.session_state.pdf_bytes:
                st.session_state.agent_response = "Сначала загрузите PDF в левом меню."
            else:
                with st.spinner("Извлекаю текст из PDF..."):
                    st.session_state.agent_response = extract_text_from_pdf()

    with excel_col:
        material_query = st.text_input(
            "Поиск по смете",
            placeholder="Например: ЛДСП",
            label_visibility="collapsed"
        )
        if st.button("🔎 Найти в Excel", use_container_width=True):
            if st.session_state.excel_df is None:
                st.session_state.agent_response = "Сначала загрузите Excel в левом меню."
            elif not material_query.strip():
                st.session_state.agent_response = "Введите название материала для поиска."
            else:
                with st.spinner("Ищу позицию в смете..."):
                    st.session_state.agent_response = search_excel_price(material_query.strip())

    with dxf_col:
        if st.button("📐 Сгенерировать DXF", use_container_width=True):
            if not st.session_state.pdf_bytes:
                st.session_state.agent_response = "Сначала загрузите PDF в левом меню."
            else:
                with st.spinner("Генерирую DXF..."):
                    st.session_state.agent_response = generate_dxf_file(
                        st.session_state.get("dxf_scale", 1.0),
                        st.session_state.get("dxf_wall_height", 0),
                        st.session_state.get("page_number", 1),
                    )

    render_agent_response("💡 Здесь появятся результаты локальной обработки PDF, Excel или DXF.")

with tab2:
    st.markdown("#### Подсчёт объектов на чертеже и расчёт сметы")
    st.caption("Требует ключ ANTHROPIC_API_KEY для визуального анализа (Claude Vision).")

    count_col, calc_col = st.columns(2)
    with count_col:
        vision_query = st.text_input(
            "Что посчитать (необязательно)",
            placeholder="Например: посчитай только шкафы",
        )
        if st.button("🔍 Посчитать объекты (Vision)", use_container_width=True):
            if not st.session_state.pdf_bytes:
                st.session_state.agent_response = "Сначала загрузите PDF в левом меню."
            else:
                with st.spinner("Анализирую чертеж..."):
                    st.session_state.agent_response = analyze_pdf_visuals_structured(
                        vision_query.strip(), st.session_state.get("page_number", 1)
                    )

    with calc_col:
        if st.button("💰 Рассчитать смету (по объектам)", use_container_width=True):
            with st.spinner("Считаю смету..."):
                st.session_state.agent_response = calculate_estimate()

    st.caption(
        "Если смета — это калькулятор материалов по комнатам (блоки «ИТОГО» на листе, "
        "а не список «объект → цена»), используйте расчёт себестоимости ниже."
    )
    if st.button("🧾 Посчитать себестоимость по текущему листу сметы"):
        with st.spinner("Суммирую блоки ИТОГО..."):
            st.session_state.agent_response = summarize_smeta_costs()

    if st.session_state.detected_objects:
        st.markdown("##### Распознанные объекты")
        st.dataframe(pd.DataFrame(st.session_state.detected_objects), use_container_width=True)

    if st.session_state.estimate_df is not None:
        st.markdown("##### Смета по проекту")
        st.dataframe(st.session_state.estimate_df, use_container_width=True)
        st.metric("Итоговая стоимость", f"{st.session_state.estimate_total:,.2f}")

    st.markdown("---")
    st.markdown("##### Площади помещений (экспликация)")
    st.caption("Работает локально без ИИ, если в PDF есть таблица с колонками «Наименование»/«Площадь».")
    if st.button("📐 Извлечь экспликацию помещений"):
        if not st.session_state.pdf_bytes:
            st.session_state.agent_response = "Сначала загрузите PDF в левом меню."
        else:
            with st.spinner("Ищу таблицу помещений..."):
                st.session_state.agent_response = extract_room_schedule()

    if st.session_state.room_schedule:
        st.dataframe(pd.DataFrame(st.session_state.room_schedule), use_container_width=True)
        st.metric("Суммарная площадь", f"{st.session_state.room_schedule_total} м²")

    render_agent_response("💡 Здесь появятся результаты подсчёта объектов, сметы и площадей.")

with tab3:
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

with tab4:
    st.markdown("#### Экспорт в AutoCAD")
    scale_col, height_col = st.columns(2)
    with scale_col:
        st.number_input(
            "Масштаб (мм на единицу PDF)",
            min_value=0.001, value=1.0, step=0.1, key="dxf_scale",
            help="Например 0.3528 для перевода точек PDF (1/72 дюйма) в миллиметры 1:1, либо реальный масштаб чертежа.",
        )
    with height_col:
        st.number_input(
            "Высота стен, мм (0 = без 3D)",
            min_value=0, value=0, step=100, key="dxf_wall_height",
            help="Если больше 0, линии и прямоугольники чертежа получают вертикальную экструзию (псевдо-3D) на эту высоту.",
        )
    if st.session_state.dxf_data:
        st.success("Файл успешно сгенерирован локально!")

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
        st.info("Файл DXF еще не создан. Нажмите «Сгенерировать DXF» на вкладке «Рабочая область», затем скачайте его здесь.")

with tab5:
    st.markdown("#### Графика")

    preview_col, chart_col = st.columns(2)

    with preview_col:
        st.markdown("##### Предпросмотр чертежа")
        if st.button("🖼️ Обновить предпросмотр", use_container_width=True):
            if not st.session_state.pdf_bytes:
                st.warning("Сначала загрузите PDF в левом меню.")
            else:
                render_pdf_preview(page_number=st.session_state.get("page_number", 1))
        if st.session_state.pdf_preview_bytes:
            st.image(st.session_state.pdf_preview_bytes, use_column_width=True)
        else:
            st.info("Нажмите «Обновить предпросмотр», чтобы отрендерить первую страницу PDF.")

    with chart_col:
        st.markdown("##### Стоимость по объектам")
        if st.session_state.estimate_df is not None:
            chart_df = st.session_state.estimate_df.dropna(subset=["Сумма"]).set_index("Объект")["Сумма"]
            if not chart_df.empty:
                st.bar_chart(chart_df)
            else:
                st.info("Нет позиций с рассчитанной суммой для отображения.")
        else:
            st.info("Сначала выполните расчёт сметы на вкладке «Подсчёт и смета».")

    st.markdown("---")
    st.markdown("##### 🧊 3D-модель плана")
    st.caption(
        "Стены выдавливаются из линий/прямоугольников выбранной страницы чертежа. Мышью можно вращать и "
        "приближать. Использует масштаб со вкладки «Экспорт AutoCAD» — если пропорции выглядят странно, "
        "настройте масштаб там."
    )

    wall3d_col, thickness3d_col, build3d_col = st.columns([1, 1, 1])
    with wall3d_col:
        st.number_input("Высота стен, мм", min_value=100, value=2700, step=100, key="preview_3d_wall_height")
    with thickness3d_col:
        st.number_input("Толщина стен, мм", min_value=10, value=150, step=10, key="preview_3d_wall_thickness")
    with build3d_col:
        st.markdown("")
        st.markdown("")
        if st.button("🧊 Построить 3D-модель", use_container_width=True):
            if not st.session_state.pdf_bytes:
                st.warning("Сначала загрузите PDF в левом меню.")
            else:
                with st.spinner("Строю 3D-модель..."):
                    st.session_state.preview_3d_html = generate_3d_preview_html(
                        wall_height_mm=st.session_state.get("preview_3d_wall_height", 2700),
                        wall_thickness_mm=st.session_state.get("preview_3d_wall_thickness", 150),
                        scale=st.session_state.get("dxf_scale", 1.0),
                        page_number=st.session_state.get("page_number", 1),
                    )

    if st.session_state.preview_3d_html:
        components.html(st.session_state.preview_3d_html, height=620, scrolling=False)
    else:
        st.info("Нажмите «Построить 3D-модель», чтобы выдавить стены выбранной страницы чертежа в 3D.")

with tab6:
    st.markdown("#### CAD-чат: правки и дополнения к чертежу")
    st.caption(
        "Опишите, что добавить или изменить в текущем CAD-проекте — например «добавь стену от (0,0) до "
        "(5000,0) высотой 2700 мм» или «покажи, что сейчас есть в проекте». Требует ключ ANTHROPIC_API_KEY. "
        "Начните с вкладки «Экспорт AutoCAD», чтобы загрузить чертёж из PDF как основу проекта, либо стройте "
        "его с нуля прямо здесь."
    )

    for role, content in st.session_state.chat_log:
        with st.chat_message(role):
            st.markdown(content)

    chat_prompt = st.chat_input("Например: добавь стену от (0,0) до (5000,0) высотой 2700 мм")
    if chat_prompt:
        st.session_state.chat_log.append(("user", chat_prompt))
        with st.chat_message("user"):
            st.markdown(chat_prompt)
        with st.chat_message("assistant"):
            with st.spinner("Обрабатываю..."):
                try:
                    if st.session_state.chat_agent is None:
                        st.session_state.chat_agent = get_agent()
                    result = st.session_state.chat_agent.invoke({"input": chat_prompt})
                    answer = result.get("output", str(result))
                except Exception as e:
                    answer = f"Ошибка чат-агента (проверьте ANTHROPIC_API_KEY): {e}"
            st.markdown(answer)
        st.session_state.chat_log.append(("assistant", answer))

    if st.session_state.dxf_data:
        st.download_button(
            "⬇️ Скачать текущий CAD-проект (.dxf)",
            data=st.session_state.dxf_data,
            file_name="cad_project.dxf",
            mime="application/dxf",
        )