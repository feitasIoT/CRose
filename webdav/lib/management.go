package lib

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
)

type managementHandler struct {
	cfg   *Config
	store Store
}

func NewManagementHandler(cfg *Config, store Store) http.Handler {
	return &managementHandler{
		cfg:   cfg,
		store: store,
	}
}

func (h *managementHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, h.cfg.Management.Prefix)
	if path == "" {
		path = "/"
	}

	if path == h.cfg.Management.OpenAPIPath && r.Method == http.MethodGet {
		h.serveOpenAPI(w, r)
		return
	}

	if path == h.cfg.Management.DocsPath && r.Method == http.MethodGet {
		h.serveDocs(w, r)
		return
	}

	if path == "/health" && r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
		return
	}

	if !h.authorized(r) {
		http.Error(w, "Not authorized", http.StatusUnauthorized)
		return
	}

	switch {
	case path == "/global":
		h.handleGlobal(w, r)
		return
	case path == "/users":
		h.handleUsers(w, r)
		return
	case strings.HasPrefix(path, "/users/"):
		h.handleUser(w, r, strings.TrimPrefix(path, "/users/"))
		return
	default:
		http.NotFound(w, r)
		return
	}
}

func (h *managementHandler) authorized(r *http.Request) bool {
	auth := r.Header.Get("Authorization")
	const prefix = "Bearer "
	if !strings.HasPrefix(auth, prefix) {
		return false
	}
	token := strings.TrimSpace(strings.TrimPrefix(auth, prefix))
	if token == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(token), []byte(h.cfg.Management.Token)) == 1
}

func (h *managementHandler) handleGlobal(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		p, err := h.store.GetGlobal(r.Context())
		if err != nil && err != ErrNotFound {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		if err == ErrNotFound {
			p = h.cfg.UserPermissions
		}
		writeJSON(w, http.StatusOK, p)
	case http.MethodPut, http.MethodPatch:
		base, err := h.store.GetGlobal(r.Context())
		if err != nil && err != ErrNotFound {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		if err == ErrNotFound {
			base = h.cfg.UserPermissions
		}
		var req globalUpsert
		if err := readJSON(r, &req); err != nil {
			http.Error(w, "Invalid JSON", http.StatusBadRequest)
			return
		}
		p, err := req.apply(base)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if err := p.Validate(); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if err := h.store.SetGlobal(r.Context(), p); err != nil {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, p)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func (h *managementHandler) handleUsers(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		users, err := h.store.ListUsers(r.Context())
		if err != nil {
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		out := make([]userPublic, 0, len(users))
		for _, u := range users {
			out = append(out, userPublic{
				Username:        u.Username,
				UserPermissions: u.UserPermissions,
				PasswordSet:     u.Password != "",
			})
		}
		writeJSON(w, http.StatusOK, out)
	case http.MethodPost:
		var req userUpsert
		if err := readJSON(r, &req); err != nil {
			http.Error(w, "Invalid JSON", http.StatusBadRequest)
			return
		}
		if strings.TrimSpace(req.Username) == "" {
			http.Error(w, "username is required", http.StatusBadRequest)
			return
		}
		h.handleUserUpsert(w, r, req.Username, req, true)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func (h *managementHandler) handleUser(w http.ResponseWriter, r *http.Request, username string) {
	if username == "" {
		http.NotFound(w, r)
		return
	}

	switch r.Method {
	case http.MethodGet:
		u, err := h.store.GetUser(r.Context(), username)
		if err != nil {
			if err == ErrNotFound {
				http.NotFound(w, r)
				return
			}
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, userPublic{
			Username:        u.Username,
			UserPermissions: u.UserPermissions,
			PasswordSet:     u.Password != "",
		})
	case http.MethodPut, http.MethodPatch:
		var req userUpsert
		if err := readJSON(r, &req); err != nil {
			http.Error(w, "Invalid JSON", http.StatusBadRequest)
			return
		}
		h.handleUserUpsert(w, r, username, req, false)
	case http.MethodDelete:
		err := h.store.DeleteUser(r.Context(), username)
		if err != nil {
			if err == ErrNotFound {
				http.NotFound(w, r)
				return
			}
			http.Error(w, "Internal server error", http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func (h *managementHandler) handleUserUpsert(w http.ResponseWriter, r *http.Request, username string, req userUpsert, creating bool) {
	global, err := h.store.GetGlobal(r.Context())
	if err != nil && err != ErrNotFound {
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}
	if err == ErrNotFound {
		global = h.cfg.UserPermissions
	}
	if global.RulesBehavior == "" {
		global.RulesBehavior = RulesOverwrite
	}

	var base *User
	existing, err := h.store.GetUser(r.Context(), username)
	if err != nil && err != ErrNotFound {
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}
	if err == nil {
		base = &existing
	} else if creating {
		base = nil
	} else {
		base = nil
	}

	u, err := req.apply(username, base, global, h.cfg.NoPassword)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	if err := u.Validate(h.cfg.NoPassword); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	if err := h.store.SetUser(r.Context(), u); err != nil {
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}

	writeJSON(w, http.StatusOK, userPublic{
		Username:        u.Username,
		UserPermissions: u.UserPermissions,
		PasswordSet:     u.Password != "",
	})
}

type userPublic struct {
	Username string `json:"username"`
	UserPermissions
	PasswordSet bool `json:"passwordSet"`
}

type userUpsert struct {
	Username      string         `json:"username,omitempty"`
	Password      *string        `json:"password,omitempty"`
	Directory     *string        `json:"directory,omitempty"`
	Permissions   *Permissions   `json:"permissions,omitempty"`
	Rules         *[]*Rule       `json:"rules,omitempty"`
	RulesBehavior *RulesBehavior `json:"rulesBehavior,omitempty"`
}

func (u userUpsert) apply(username string, base *User, global UserPermissions, noPassword bool) (User, error) {
	var out User
	if base != nil {
		out = *base
	} else {
		out = User{
			UserPermissions: global,
		}
	}

	out.Username = username

	if u.Directory != nil {
		out.Directory = *u.Directory
	}
	if u.Permissions != nil {
		out.Permissions = *u.Permissions
	}
	if u.RulesBehavior != nil {
		out.RulesBehavior = *u.RulesBehavior
	}
	if u.Rules != nil {
		out.Rules = normalizeRules(*u.Rules)
	}
	if u.Password != nil {
		out.Password = *u.Password
	}

	if out.RulesBehavior == "" {
		out.RulesBehavior = RulesOverwrite
	}

	if base == nil && !noPassword && strings.TrimSpace(out.Password) == "" {
		return User{}, errors.New("password is required")
	}

	return out, nil
}

type globalUpsert struct {
	Directory     *string        `json:"directory,omitempty"`
	Permissions   *Permissions   `json:"permissions,omitempty"`
	Rules         *[]*Rule       `json:"rules,omitempty"`
	RulesBehavior *RulesBehavior `json:"rulesBehavior,omitempty"`
}

func (g globalUpsert) apply(base UserPermissions) (UserPermissions, error) {
	out := base

	if g.Directory != nil {
		out.Directory = *g.Directory
	}
	if g.Permissions != nil {
		out.Permissions = *g.Permissions
	}
	if g.RulesBehavior != nil {
		out.RulesBehavior = *g.RulesBehavior
	}
	if g.Rules != nil {
		out.Rules = normalizeRules(*g.Rules)
	}
	if out.RulesBehavior == "" {
		out.RulesBehavior = RulesOverwrite
	}

	return out, nil
}

func normalizeRules(rules []*Rule) []*Rule {
	if len(rules) == 0 {
		return rules
	}

	out := make([]*Rule, 0, len(rules))
	for _, r := range rules {
		if r == nil {
			continue
		}
		if r.Path == "" && r.Regex == nil {
			continue
		}
		out = append(out, r)
	}
	return out
}

func readJSON(r *http.Request, v any) error {
	defer r.Body.Close()
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
