package lib

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestManagementOpenAPIAndUsers(t *testing.T) {
	t.Parallel()

	cfg := writeAndParseConfig(t, `
directory: /
permissions: R
management:
  enabled: true
  prefix: /api
  openapi_path: /openapi.json
  docs_path: /docs
  token: test-token
`, ".yml")

	handler, err := NewHandler(cfg)
	require.NoError(t, err)

	srv := httptest.NewServer(handler)
	defer srv.Close()

	t.Run("OpenAPI no auth", func(t *testing.T) {
		resp, err := http.Get(srv.URL + "/api/openapi.json")
		require.NoError(t, err)
		defer resp.Body.Close()
		require.Equal(t, http.StatusOK, resp.StatusCode)
	})

	t.Run("Users require auth", func(t *testing.T) {
		resp, err := http.Get(srv.URL + "/api/users")
		require.NoError(t, err)
		defer resp.Body.Close()
		require.Equal(t, http.StatusUnauthorized, resp.StatusCode)
	})

	t.Run("Health no auth", func(t *testing.T) {
		resp, err := http.Get(srv.URL + "/api/health")
		require.NoError(t, err)
		defer resp.Body.Close()
		require.Equal(t, http.StatusOK, resp.StatusCode)
	})

	t.Run("Create user and list", func(t *testing.T) {
		body := map[string]any{
			"username":    "alice",
			"password":    "alice",
			"directory":   "/tmp",
			"permissions": "R",
			"rules":       []any{},
		}
		raw, err := json.Marshal(body)
		require.NoError(t, err)

		req, err := http.NewRequest(http.MethodPost, srv.URL+"/api/users", bytes.NewReader(raw))
		require.NoError(t, err)
		req.Header.Set("Authorization", "Bearer test-token")
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		require.NoError(t, err)
		defer resp.Body.Close()
		require.Equal(t, http.StatusOK, resp.StatusCode)

		req2, err := http.NewRequest(http.MethodGet, srv.URL+"/api/users", nil)
		require.NoError(t, err)
		req2.Header.Set("Authorization", "Bearer test-token")
		resp2, err := http.DefaultClient.Do(req2)
		require.NoError(t, err)
		defer resp2.Body.Close()
		require.Equal(t, http.StatusOK, resp2.StatusCode)

		var out []map[string]any
		require.NoError(t, json.NewDecoder(resp2.Body).Decode(&out))
		require.Len(t, out, 1)
		require.Equal(t, "alice", out[0]["username"])
		_, hasDirectory := out[0]["directory"]
		require.True(t, hasDirectory)
	})
}
