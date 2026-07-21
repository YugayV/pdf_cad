from langchain.agents import AgentType, initialize_agent
from langchain.memory import ConversationBufferMemory
from langchain.tools import StructuredTool
from langchain_openai import ChatOpenAI

from tools import (
    add_room_label,
    add_wall,
    analyze_pdf_visuals_structured,
    calculate_estimate,
    extract_room_schedule,
    extract_text_from_pdf,
    generate_dxf_file,
    list_dxf_entities,
    remove_last_entity,
    reset_cad_project,
    search_excel_price,
    summarize_smeta_costs,
)

TOOL_FUNCTIONS = [
    extract_text_from_pdf,
    analyze_pdf_visuals_structured,
    extract_room_schedule,
    search_excel_price,
    calculate_estimate,
    summarize_smeta_costs,
    generate_dxf_file,
    add_wall,
    add_room_label,
    remove_last_entity,
    list_dxf_entities,
    reset_cad_project,
]


def get_agent():
    """Создает нового чат-агента с памятью для диалоговой работы над CAD-проектом:
    подсчет объектов/площадей, расчет сметы, генерация и правка DXF-чертежа через команды в чате."""
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    tools = [StructuredTool.from_function(func=fn) for fn in TOOL_FUNCTIONS]
    memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

    agent = initialize_agent(
        tools,
        llm,
        agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
        memory=memory,
        verbose=True,
        handle_parsing_errors=True,  # Важно, чтобы агент не падал при ошибках формата
    )
    return agent
