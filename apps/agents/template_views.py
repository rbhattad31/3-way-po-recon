"""Agent template views — reference pages for end users."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
from apps.core.enums import AgentType, RecommendationType


@login_required
def agent_reference(request):
    """Shows all agents, their tools, prompts, and how they work."""
    agents_info = []
    for agent_type_val, agent_cls in AGENT_CLASS_REGISTRY.items():
        instance = agent_cls()
        label = AgentType(agent_type_val).label
        agents_info.append({
            "type": agent_type_val,
            "label": label,
            "description": agent_cls.__doc__ or "",
            "system_prompt": instance.system_prompt,
            "allowed_tools": instance.allowed_tools,
        })

    # Build tool info from the live registry
    from apps.tools.registry.base import ToolRegistry
    all_tools = ToolRegistry.get_all()
    tools_info = []
    for name, tool in sorted(all_tools.items()):
        tools_info.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema.get("properties", {}),
            "required": tool.parameters_schema.get("required", []),
        })

    recommendation_types = [
        {"value": val, "label": label}
        for val, label in RecommendationType.choices
    ]

    # Collect all prompts used in the application
    from apps.extraction.services.extraction_adapter import EXTRACTION_SYSTEM_PROMPT
    prompts = [
        {
            "name": "Invoice Extraction",
            "category": "Extraction",
            "icon": "bi-file-earmark-text",
            "color": "primary",
            "description": (
                "Used by the extraction pipeline when processing uploaded invoice PDFs. "
                "After Azure Document Intelligence performs OCR, this prompt instructs "
                "Azure OpenAI GPT-4o to extract structured invoice data from the raw text."
            ),
            "used_in": "apps/extraction/services/extraction_adapter.py",
            "model": "Azure OpenAI GPT-4o (temperature: 0.0)",
            "prompt_text": EXTRACTION_SYSTEM_PROMPT,
        },
    ]
    # Add each agent's system prompt
    for agent in agents_info:
        prompts.append({
            "name": agent["label"],
            "category": "Agent",
            "icon": "bi-robot",
            "color": "success",
            "description": agent["description"],
            "used_in": "apps/agents/services/agent_classes.py",
            "model": "Azure OpenAI GPT-4o (temperature: 0.1)",
            "prompt_text": agent["system_prompt"],
        })

    return render(request, "agents/reference.html", {
        "agents_info": agents_info,
        "tools_info": tools_info,
        "recommendation_types": recommendation_types,
        "prompts": prompts,
        "max_tool_rounds": 6,
    })
