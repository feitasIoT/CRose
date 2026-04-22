package lib

import (
	"encoding/json"
	"html/template"
	"net/http"
)

func (h *managementHandler) serveOpenAPI(w http.ResponseWriter, r *http.Request) {
	spec := map[string]any{
		"openapi": "3.0.3",
		"info": map[string]any{
			"title":   "webdav management",
			"version": "1.0.0",
		},
		"servers": []map[string]any{
			{"url": h.cfg.Management.Prefix},
		},
		"paths": map[string]any{
			"/health": map[string]any{
				"get": map[string]any{
					"responses": map[string]any{
						"200": map[string]any{"description": "OK"},
					},
				},
			},
			"/global": map[string]any{
				"get": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"responses": map[string]any{
						"200": map[string]any{
							"description": "OK",
							"content": map[string]any{
								"application/json": map[string]any{
									"schema": map[string]any{"$ref": "#/components/schemas/UserPermissions"},
								},
							},
						},
					},
				},
				"put": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"requestBody": map[string]any{
						"required": true,
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/GlobalUpsert"},
							},
						},
					},
					"responses": map[string]any{
						"200": map[string]any{
							"description": "OK",
							"content": map[string]any{
								"application/json": map[string]any{
									"schema": map[string]any{"$ref": "#/components/schemas/UserPermissions"},
								},
							},
						},
					},
				},
				"patch": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"requestBody": map[string]any{
						"required": true,
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/GlobalUpsert"},
							},
						},
					},
					"responses": map[string]any{
						"200": map[string]any{
							"description": "OK",
							"content": map[string]any{
								"application/json": map[string]any{
									"schema": map[string]any{"$ref": "#/components/schemas/UserPermissions"},
								},
							},
						},
					},
				},
			},
			"/users": map[string]any{
				"get": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"responses": map[string]any{
						"200": map[string]any{
							"description": "OK",
							"content": map[string]any{
								"application/json": map[string]any{
									"schema": map[string]any{
										"type":  "array",
										"items": map[string]any{"$ref": "#/components/schemas/UserPublic"},
									},
								},
							},
						},
					},
				},
				"post": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"requestBody": map[string]any{
						"required": true,
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/UserUpsert"},
							},
						},
					},
					"responses": map[string]any{
						"200": map[string]any{
							"description": "OK",
							"content": map[string]any{
								"application/json": map[string]any{
									"schema": map[string]any{"$ref": "#/components/schemas/UserPublic"},
								},
							},
						},
					},
				},
			},
			"/users/{username}": map[string]any{
				"parameters": []map[string]any{
					{
						"name":     "username",
						"in":       "path",
						"required": true,
						"schema":   map[string]any{"type": "string"},
					},
				},
				"get": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"responses": map[string]any{
						"200": map[string]any{
							"description": "OK",
							"content": map[string]any{
								"application/json": map[string]any{
									"schema": map[string]any{"$ref": "#/components/schemas/UserPublic"},
								},
							},
						},
					},
				},
				"put": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"requestBody": map[string]any{
						"required": true,
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/UserUpsert"},
							},
						},
					},
					"responses": map[string]any{
						"200": map[string]any{
							"description": "OK",
							"content": map[string]any{
								"application/json": map[string]any{
									"schema": map[string]any{"$ref": "#/components/schemas/UserPublic"},
								},
							},
						},
					},
				},
				"patch": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"requestBody": map[string]any{
						"required": true,
						"content": map[string]any{
							"application/json": map[string]any{
								"schema": map[string]any{"$ref": "#/components/schemas/UserUpsert"},
							},
						},
					},
					"responses": map[string]any{
						"200": map[string]any{
							"description": "OK",
							"content": map[string]any{
								"application/json": map[string]any{
									"schema": map[string]any{"$ref": "#/components/schemas/UserPublic"},
								},
							},
						},
					},
				},
				"delete": map[string]any{
					"security": []map[string]any{{"bearerAuth": []any{}}},
					"responses": map[string]any{
						"204": map[string]any{"description": "No Content"},
					},
				},
			},
		},
		"components": map[string]any{
			"securitySchemes": map[string]any{
				"bearerAuth": map[string]any{
					"type":   "http",
					"scheme": "bearer",
				},
			},
			"schemas": map[string]any{
				"Permissions": map[string]any{
					"type":        "string",
					"description": "Permissions encoded as a string: none, R, CR, CRUD, etc.",
				},
				"Rule": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"permissions": map[string]any{"$ref": "#/components/schemas/Permissions"},
						"path":        map[string]any{"type": "string"},
						"regex":       map[string]any{"type": "string"},
					},
				},
				"UserPermissions": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"directory":     map[string]any{"type": "string"},
						"permissions":   map[string]any{"$ref": "#/components/schemas/Permissions"},
						"rulesBehavior": map[string]any{"type": "string", "enum": []any{string(RulesOverwrite), string(RulesAppend)}},
						"rules": map[string]any{
							"type":  "array",
							"items": map[string]any{"$ref": "#/components/schemas/Rule"},
						},
					},
				},
				"UserPublic": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"username":      map[string]any{"type": "string"},
						"passwordSet":   map[string]any{"type": "boolean"},
						"directory":     map[string]any{"type": "string"},
						"permissions":   map[string]any{"$ref": "#/components/schemas/Permissions"},
						"rulesBehavior": map[string]any{"type": "string", "enum": []any{string(RulesOverwrite), string(RulesAppend)}},
						"rules": map[string]any{
							"type":  "array",
							"items": map[string]any{"$ref": "#/components/schemas/Rule"},
						},
					},
				},
				"UserUpsert": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"username":      map[string]any{"type": "string"},
						"password":      map[string]any{"type": "string"},
						"directory":     map[string]any{"type": "string"},
						"permissions":   map[string]any{"$ref": "#/components/schemas/Permissions"},
						"rulesBehavior": map[string]any{"type": "string", "enum": []any{string(RulesOverwrite), string(RulesAppend)}},
						"rules": map[string]any{
							"type":  "array",
							"items": map[string]any{"$ref": "#/components/schemas/Rule"},
						},
					},
				},
				"GlobalUpsert": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"directory":     map[string]any{"type": "string"},
						"permissions":   map[string]any{"$ref": "#/components/schemas/Permissions"},
						"rulesBehavior": map[string]any{"type": "string", "enum": []any{string(RulesOverwrite), string(RulesAppend)}},
						"rules": map[string]any{
							"type":  "array",
							"items": map[string]any{"$ref": "#/components/schemas/Rule"},
						},
					},
				},
			},
		},
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(spec)
}

func (h *managementHandler) serveDocs(w http.ResponseWriter, r *http.Request) {
	openapiURL := h.cfg.Management.Prefix + h.cfg.Management.OpenAPIPath
	tpl := template.Must(template.New("docs").Parse(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>webdav management docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: {{ .OpenAPIURL | js }},
      dom_id: '#swagger-ui',
      presets: [
        SwaggerUIBundle.presets.apis
      ],
      layout: "BaseLayout"
    });
  </script>
</body>
</html>`))

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = tpl.Execute(w, map[string]string{
		"OpenAPIURL": openapiURL,
	})
}
