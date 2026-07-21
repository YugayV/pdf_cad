from langchain.agents import AgentType, initialize_agent
from langchain_openai import ChatOpenAI
from tools import (
    analyze_pdf_visuals_structured,
    calculate_estimate,
    extract_text_from_pdf,
    generate_dxf_file,
    search_excel_price,
    summarize_smeta_costs,
)


def get_agent():
    llm = ChatOpenAI(model="gpt-4o", temperature=0)

    tools = [
        extract_text_from_pdf,
        analyze_pdf_visuals_structured,
        search_excel_price,
        calculate_estimate,
        summarize_smeta_costs,
        generate_dxf_file,
    ]

    agent = initialize_agent(
        tools,
        llm,
        agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
        verbose=True,
        handle_parsing_errors=True,  # Важно, чтобы агент не падал при ошибках формата
    )
    return agent