package lib

import (
	"context"
	"errors"
)

var ErrNotFound = errors.New("not found")

type Store interface {
	GetGlobal(ctx context.Context) (UserPermissions, error)
	SetGlobal(ctx context.Context, p UserPermissions) error

	ListUsers(ctx context.Context) ([]User, error)
	GetUser(ctx context.Context, username string) (User, error)
	SetUser(ctx context.Context, u User) error
	DeleteUser(ctx context.Context, username string) error
}
