{
    "name": "CRose IoT AI",
    "version": "1.0",
    "summary": "AI features for CRose IoT platform",
    "description": """
    AI add-on for CRose IoT Platform.
    ==================================
    - Manage AI models, prompts, datasets, and training tasks
    - Build and query AI knowledge bases
    - Enable AI chat and AI-assisted flow generation
    """,
    "category": "Tools",
    "author": "Feitas",
    "license": "LGPL-3",
    "depends": ["feitas_iot"],
    "data": [
        "data/data.xml",
        "data/ai_partner_data.xml",
        "data/crons.xml",
        "security/ir.model.access.csv",
        "wizards/data_model_ai_flow_wizard_views.xml",
        "wizards/ai_knowledge_rag_wizard_views.xml",
        "wizards/ai_chat_wizard_views.xml",
        "views/ai_views.xml",
        "views/ai_knowledge_views.xml",
        "views/data_model_views.xml",
        "views/nr_flow_views.xml",
        "views/menu_actions.xml",
        "views/crose_settings_views.xml",
    ],
    "installable": True,
    "application": False,
}
