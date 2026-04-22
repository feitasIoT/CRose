package lib

import "context"

func seedStoreFromConfigIfEmpty(ctx context.Context, s Store, cfg *Config) error {
	if _, err := s.GetGlobal(ctx); err == ErrNotFound {
		if err := s.SetGlobal(ctx, cfg.UserPermissions); err != nil {
			return err
		}
	} else if err != nil {
		return err
	}

	users, err := s.ListUsers(ctx)
	if err != nil {
		return err
	}
	if len(users) == 0 && len(cfg.Users) > 0 {
		for _, u := range cfg.Users {
			if err := s.SetUser(ctx, u); err != nil {
				return err
			}
		}
	}

	return nil
}
