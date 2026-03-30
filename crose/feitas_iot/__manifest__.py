{
    "name": "CRose物联网平台",
    "version": "1.0",
    "summary": "China Rose(月季)，我出生地的市花，也是我儿时...",
    "description": """
    CRose物联网平台让物联网变得更简单、更智能。
    ============================================
    更简单
    ----------
    - 可视化的流程编辑器，无需编程即可创建物联网应用
    - 支持多种协议，包括MQTT、HTTP、CoAP等
    更智能
    ----------
    - 支持AI模型，包括TensorFlow、PyTorch等
    - 支持NPM包管理，无需手动安装依赖
    """,
    "category": "Tools",
    "author": "Feitas",
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
