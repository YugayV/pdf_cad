from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, AgentType
from langchain_core.messages import HumanMessage
from tools import extract_text_from_pdf, analyze_pdf_visuals, search_excel_price, generate_dxf_file
import streamlit as st
import base64

def process_vision_query(prompt: str) -> str:
    """Прямой вызов Vision модели для анализа картинки чертежа."""
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    img_bytes = st.session_state.get("pdf_image_bytes")
    
    if not img_bytes:
        return "Сначала вызови инструмент analyze_pdf_visuals."
    
    base64_image = base64.b64encode(img_bytes).decode('utf-8')
    
    message = HumanMessage(
        content=[
            {"type": "text", "text": f"Ты инженер-чертежник. Проанализируй этот чертеж мебели/помещения. Задача: {prompt}. Опиши объекты, их размеры и количество."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
        ]
    )
    response = llm.invoke([message])
    return response.content

def get_agent():
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    
    tools = [
        extract_text_from_pdf,
        analyze_pdf_visuals,
        search_excel_price,
        generate_dxf_file
    ]
    
    agent = initialize_agent(
        tools,
        llm,
        agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
        verbose=True,
        handle_parsing_errors=True # Важно, чтобы агент не падал при ошибках формата
    )
    return agent