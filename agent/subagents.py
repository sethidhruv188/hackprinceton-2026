"""The four sub-agents (deepagents SubAgent specs), each with only its own tools, filtered by the caller's role."""
from agent import prompts as P
from agent import tools as T

SPECS = [
    dict(name="order_planner",
         description="Plans what to order, when and how much, by brand and container, from the engine's outputs. "
                     "Use for 'what should I order', order dates, quantities, containers, harvest buys.",
         template=P.ORDER_PLANNER,
         tools=[T.what_to_order, T.explain_product, T.festival_window, T.harvest_plan, T.products_at_risk, T.pending_questions]),
    dict(name="explainer",
         description="Explains in plain language why a product is flagged and how good the forecast is.",
         template=P.EXPLAINER,
         tools=[T.explain_product, T.forecast_quality, T.products_at_risk, T.harvest_plan]),
    dict(name="sourcing_researcher",
         description="Researches harvest timing and crop / supply news by producing country (web search, with URLs); "
                     "may record a sourced country alert. Use when a commodity is at risk or a country may have a problem.",
         template=P.SOURCING_RESEARCHER,
         tools=[T.harvest_plan, T.alternative_sources, T.country_alerts, T.products_at_risk, T.web_search, T.add_alerts]),
    dict(name="supplier_scout",
         description="Finds exporters of a commodity in its producing countries (web search) and records them as "
                     "unverified leads with source URLs; can draft an inquiry for the owner to send himself.",
         template=P.SUPPLIER_SCOUT,
         tools=[T.alternative_sources, T.consolidation_options, T.web_search, T.add_leads]),
]


def build(role):
    """SubAgent dicts for create_deep_agent(subagents=...)."""
    return [dict(name=s["name"], description=s["description"], system_prompt=P.sub_prompt(s["template"]),
                 tools=T.for_role(s["tools"], role)) for s in SPECS]
