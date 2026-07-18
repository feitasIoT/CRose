{
    "name": "CRose IoT Platform",
    "version": "1.0",
    "summary": "CRose IoT platform for building connected applications",
    "description": """
CRose makes IoT development simpler and smarter.
=====================================================
Simpler
-------
- Build IoT applications with a visual flow editor
- Support multiple protocols, including MQTT, HTTP, and CoAP

Extensible
----------
- Extend with add-on modules such as AI capabilities
- Support NPM package management without manual dependency installation


    """,
    "category": "Tools",
    "author": "Feitas",
    "license": "LGPL-3",
    "depends": ["base", "web", "mail", "spreadsheet"],
    "data": [
        "data/data.xml",
        "data/crons.xml",

        "security/groups.xml",
        "security/ir.model.access.csv",

        "wizards/nr_instance_wizard_views.xml",
        "wizards/edge_node_deploy_wizard_views.xml",
        "views/nr_instance_views.xml",
        "views/editor_views.xml",
        "views/crose_component_views.xml",
        "views/crose_nr_package_views.xml",
        "views/mqtt_user_views.xml",
        "views/gateway_mqtt_user_views.xml",
        "views/edge_node_views.xml",
        "views/nr_flow_views.xml",
        "views/nr_node_views.xml",
        "views/data_address_views.xml",
        "views/data_asset_views.xml",
        "views/agent_package_views.xml",
        "views/alert_views.xml",
        "views/data_model_views.xml",
        "views/mqtt_topic_views.xml",
        "views/nr_tag_views.xml",
        "views/nr_flow_param_views.xml",
        "views/data_log_views.xml",
        "views/res_partner_views.xml",
        "views/login_layout_templates.xml",

        "views/menu_actions.xml",
        "views/crose_settings_views.xml",
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
        'web.assets_frontend': [
            'feitas_iot/static/src/scss/login_layout.scss',
        ],
    },
    "installable": True,
    "application": True,
}
