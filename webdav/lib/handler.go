package lib

import (
	"context"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/rs/cors"
	"go.uber.org/zap"
	"golang.org/x/net/webdav"
)

type Handler struct {
	cfg         *Config
	store       Store
	ls          webdav.LockSystem
	logFunc     func(*http.Request, error)
	noPassword  bool
	behindProxy bool
	management  http.Handler
}

func NewHandler(c *Config) (http.Handler, error) {
	ls := webdav.NewMemLS()

	logFunc := func(r *http.Request, err error) {
		lZap := getRequestLogger(r, c.BehindProxy)
		lZap.Debug("handle webdav request", zap.String("method", r.Method), zap.String("path", r.URL.Path), zap.Error(err))
	}

	var store Store
	if c.Redis.Enabled {
		s, err := NewRedisStore(c.Redis)
		if err != nil {
			return nil, err
		}
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()

		if c.Redis.SeedFromConfig {
			if err := seedStoreFromConfigIfEmpty(ctx, s, c); err != nil {
				return nil, err
			}
		}

		if _, err := s.GetGlobal(ctx); err != nil && err != ErrNotFound {
			return nil, err
		}
		if _, err := s.ListUsers(ctx); err != nil {
			return nil, err
		}

		store = s
	} else {
		store = NewStoreFromConfig(c)
	}

	h := &Handler{
		cfg:         c,
		store:       store,
		ls:          ls,
		logFunc:     logFunc,
		noPassword:  c.NoPassword,
		behindProxy: c.BehindProxy,
	}

	if c.Management.Enabled {
		h.management = NewManagementHandler(c, store)
	}

	if c.CORS.Enabled {
		return cors.New(cors.Options{
			AllowCredentials:   c.CORS.Credentials,
			AllowedOrigins:     c.CORS.AllowedHosts,
			AllowedMethods:     c.CORS.AllowedMethods,
			AllowedHeaders:     c.CORS.AllowedHeaders,
			ExposedHeaders:     c.CORS.ExposedHeaders,
			OptionsPassthrough: false,
		}).Handler(h), nil
	}

	if !c.Redis.Enabled && len(c.Users) == 0 {
		zap.L().Warn("unprotected config: no users have been set, so no authentication will be used")
	}
	if c.Redis.Enabled {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		users, err := store.ListUsers(ctx)
		if err != nil {
			return nil, err
		}
		if len(users) == 0 {
			zap.L().Warn("unprotected config: no users have been set in redis, so no authentication will be used")
		}
	}

	if c.NoPassword {
		zap.L().Warn("unprotected config: password check is disabled, only intended when delegating authentication to another service")
	}

	return h, nil
}

// ServeHTTP determines if the request is for this plugin, and if all prerequisites are met.
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	lZap := getRequestLogger(r, h.behindProxy)

	if h.cfg.Management.Enabled && strings.HasPrefix(r.URL.Path, h.cfg.Management.Prefix) {
		h.management.ServeHTTP(w, r)
		return
	}

	global, err := h.store.GetGlobal(r.Context())
	if err != nil && err != ErrNotFound {
		lZap.Error("failed to load global permissions", zap.Error(err))
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}
	if err == ErrNotFound {
		global = h.cfg.UserPermissions
	}

	userPerms := global
	directory := global.Directory
	username := ""

	// Authentication
	users, err := h.store.ListUsers(r.Context())
	if err != nil {
		lZap.Error("failed to list users", zap.Error(err))
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}

	if len(users) > 0 {
		w.Header().Set("WWW-Authenticate", `Basic realm="Restricted"`)

		// Gets the correct user for this request.
		var password string
		var ok bool
		username, password, ok = r.BasicAuth()
		if !ok {
			http.Error(w, "Not authorized", http.StatusUnauthorized)
			return
		}

		u, err := h.store.GetUser(r.Context(), username)
		if err != nil {
			// Log invalid username
			lZap.Info("invalid username", zap.String("username", username))
			http.Error(w, "Not authorized", http.StatusUnauthorized)
			return
		}

		if !h.noPassword && !u.checkPassword(password) {
			// Log invalid password
			lZap.Info("invalid password", zap.String("username", username))
			http.Error(w, "Not authorized", http.StatusUnauthorized)
			return
		}

		userPerms = u.UserPermissions
		directory = u.Directory

		// Log successful authorization
		lZap.Info("user authorized", zap.String("username", username))
	}

	webdavHandler := webdav.Handler{
		Prefix: h.cfg.Prefix,
		FileSystem: Dir{
			Dir:     webdav.Dir(directory),
			noSniff: h.cfg.NoSniff,
		},
		LockSystem: &lockSystem{
			LockSystem: h.ls,
			directory:  directory,
		},
		Logger: h.logFunc,
	}

	// Convert the HTTP request into an internal request type
	req, err := newRequest(r, h.cfg.Prefix)
	if err != nil {
		lZap.Info("invalid request path or destination", zap.Error(err))
		http.Error(w, "Invalid request path or destination", http.StatusBadRequest)
		return
	}

	// Checks for user permissions relatively to this PATH.
	allowed := userPerms.Allowed(req, func(filename string) bool {
		_, err := webdavHandler.FileSystem.Stat(r.Context(), filename)
		return !os.IsNotExist(err)
	})

	lZap.Debug("allowed & method & path", zap.Bool("allowed", allowed), zap.String("method", r.Method), zap.String("path", r.URL.Path), zap.String("username", username))

	if !allowed {
		w.WriteHeader(http.StatusForbidden)
		return
	}

	if r.Method == "HEAD" {
		w = responseWriterNoBody{w}
	}

	// Excerpt from RFC4918, section 9.4:
	//
	// 		GET, when applied to a collection, may return the contents of an
	//		"index.html" resource, a human-readable view of the contents of
	//		the collection, or something else altogether.
	//
	//    Similarly, since the definition of HEAD is a GET without a response
	// 		message body, the semantics of HEAD are unmodified when applied to
	// 		collection resources.
	//
	// GET (or HEAD), when applied to collection, will return the same as PROPFIND method.
	if (r.Method == "GET" || r.Method == "HEAD") && strings.HasPrefix(r.URL.Path, webdavHandler.Prefix) {
		info, err := webdavHandler.FileSystem.Stat(r.Context(), strings.TrimPrefix(r.URL.Path, webdavHandler.Prefix))
		if err == nil && info.IsDir() {
			r.Method = "PROPFIND"

			if r.Header.Get("Depth") == "" {
				r.Header.Add("Depth", "1")
			}
		}
	}

	// Runs the WebDAV.
	webdavHandler.ServeHTTP(w, r)
}

// getRequestLogger creates a zap.Logger using the request remote ip.
func getRequestLogger(r *http.Request, behindProxy bool) *zap.Logger {
	// Retrieve the real client IP address using the updated helper function
	remoteAddr := getRealRemoteIP(r, behindProxy)

	return zap.L().With(zap.String("remote_address", remoteAddr))
}

// getRealRemoteIP retrieves the client's actual IP address, considering reverse proxies.
func getRealRemoteIP(r *http.Request, behindProxy bool) string {
	if behindProxy {
		if ip := r.Header.Get("X-Forwarded-For"); ip != "" {
			return ip
		}
	}
	return r.RemoteAddr
}

type responseWriterNoBody struct {
	http.ResponseWriter
}

func (w responseWriterNoBody) Write(data []byte) (int, error) {
	return len(data), nil
}
