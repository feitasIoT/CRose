{
    "name": "CRose IoT Platform",
    "version": "1.0",
    "summary": "CRose IoT platform for building connected applications",
    "description": """
    CRose makes IoT development simpler and smarter.
    ===============================================
    Simpler
    -------
    - Build IoT applications with a visual flow editor
    - Support multiple protocols, including MQTT, HTTP, and CoAP
    Smarter
    -------
    - Support AI models such as TensorFlow and PyTorch
    - Support NPM package management without manual dependency installation
    """,
    "category": "Tools",
    "author": "Feitas",
    "license": "LGPL-3",
    "depends": ["base", "web", "mail", "spreadsheet"],
    "data": [
        "data/crons.xml",
        "data/data.xml",
        "data/ai_partner_data.xml",

        "security/ir.model.access.csv",

        "views/instance_views.xml",
        "views/editor_views.xml",
        "views/crose_component_views.xml",
        "views/crose_nr_package_views.xml",
        "views/mqtt_user_views.xml",
        "views/edge_agent_views.xml",
        "views/nr_flow_views.xml",
        "views/node_views.xml",
        
        "views/data_address_views.xml",
        "views/knowledge_views.xml",
        "views/data_model_views.xml",
        "views/mqtt_topic_views.xml",
        "views/ai_views.xml",
        "views/nr_tag_views.xml",
        "views/nr_flow_param_views.xml",
        "views/data_log_views.xml",
        "views/res_partner_views.xml",
        

        "views/menu_actions.xml",
        
    ],
    'assets': {
        'spreadsheet.o_spreadsheet': [
            'feitas_iot/static/src/bundle/actions/*.js',
            'feitas_iot/static/src/bundle/actions/*.xml',
        ],
        'web.assets_backend': [
            'feitas_iot/static/src/js/editor_embed.js',
            'feitas_iot/static/src/js/overview_dashboard.js',
            'feitas_iot/static/src/js/data_model_spreadsheet_action_loader.js',
            'feitas_iot/static/src/xml/editor_templates.xml',
            'feitas_iot/static/src/xml/overview_templates.xml',
            'feitas_iot/static/src/scss/instance_kanban.scss',
        ],
    },
    "installable": True,
    "application": True,
}
