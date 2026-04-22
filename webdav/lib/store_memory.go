package lib

import (
	"context"
	"sync"
)

type memoryStore struct {
	mu     sync.RWMutex
	global UserPermissions
	users  map[string]User
}

func NewMemoryStore() Store {
	return &memoryStore{
		users: map[string]User{},
	}
}

func NewStoreFromConfig(cfg *Config) Store {
	s := &memoryStore{
		global: cfg.UserPermissions,
		users:  map[string]User{},
	}
	for _, u := range cfg.Users {
		s.users[u.Username] = u
	}
	return s
}

func (s *memoryStore) GetGlobal(ctx context.Context) (UserPermissions, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.global, nil
}

func (s *memoryStore) SetGlobal(ctx context.Context, p UserPermissions) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.global = p
	return nil
}

func (s *memoryStore) ListUsers(ctx context.Context) ([]User, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	out := make([]User, 0, len(s.users))
	for _, u := range s.users {
		out = append(out, u)
	}
	sortUsers(out)
	return out, nil
}

func (s *memoryStore) GetUser(ctx context.Context, username string) (User, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	u, ok := s.users[username]
	if !ok {
		return User{}, ErrNotFound
	}
	return u, nil
}

func (s *memoryStore) SetUser(ctx context.Context, u User) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.users[u.Username] = u
	return nil
}

func (s *memoryStore) DeleteUser(ctx context.Context, username string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.users[username]; !ok {
		return ErrNotFound
	}
	delete(s.users, username)
	return nil
}
