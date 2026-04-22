package lib

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"time"
)

type redisStore struct {
	addr      string
	username  string
	password  string
	db        int
	keyPrefix string
}

func NewRedisStore(cfg Redis) (Store, error) {
	if strings.TrimSpace(cfg.Addr) == "" {
		return nil, errors.New("redis addr is empty")
	}
	return &redisStore{
		addr:      cfg.Addr,
		username:  cfg.Username,
		password:  cfg.Password,
		db:        cfg.DB,
		keyPrefix: cfg.KeyPrefix,
	}, nil
}

func (s *redisStore) globalKey() string { return s.keyPrefix + "global" }
func (s *redisStore) usersKey() string  { return s.keyPrefix + "users" }

func (s *redisStore) dial(ctx context.Context) (net.Conn, *bufio.Reader, error) {
	var d net.Dialer

	timeout := 5 * time.Second
	if deadline, ok := ctx.Deadline(); ok {
		if t := time.Until(deadline); t > 0 && t < timeout {
			timeout = t
		}
	}

	conn, err := d.DialContext(ctx, "tcp", s.addr)
	if err != nil {
		return nil, nil, err
	}

	_ = conn.SetDeadline(time.Now().Add(timeout))
	return conn, bufio.NewReader(conn), nil
}

func (s *redisStore) do(ctx context.Context, args ...string) (any, error) {
	conn, rd, err := s.dial(ctx)
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	if s.password != "" || s.username != "" {
		if s.username != "" {
			if _, err := redisCall(conn, rd, "AUTH", s.username, s.password); err != nil {
				return nil, err
			}
		} else {
			if _, err := redisCall(conn, rd, "AUTH", s.password); err != nil {
				return nil, err
			}
		}
	}

	if s.db != 0 {
		if _, err := redisCall(conn, rd, "SELECT", strconv.Itoa(s.db)); err != nil {
			return nil, err
		}
	}

	return redisCall(conn, rd, args...)
}

func (s *redisStore) GetGlobal(ctx context.Context) (UserPermissions, error) {
	v, err := s.do(ctx, "GET", s.globalKey())
	if err != nil {
		return UserPermissions{}, err
	}
	if v == nil {
		return UserPermissions{}, ErrNotFound
	}

	b, ok := v.([]byte)
	if !ok {
		return UserPermissions{}, errors.New("unexpected redis response type")
	}

	var p UserPermissions
	if err := json.Unmarshal(b, &p); err != nil {
		return UserPermissions{}, err
	}
	return p, nil
}

func (s *redisStore) SetGlobal(ctx context.Context, p UserPermissions) error {
	raw, err := json.Marshal(p)
	if err != nil {
		return err
	}
	_, err = s.do(ctx, "SET", s.globalKey(), string(raw))
	return err
}

func (s *redisStore) ListUsers(ctx context.Context) ([]User, error) {
	v, err := s.do(ctx, "HGETALL", s.usersKey())
	if err != nil {
		return nil, err
	}

	arr, ok := v.([]any)
	if !ok {
		if v == nil {
			return []User{}, nil
		}
		return nil, errors.New("unexpected redis response type")
	}

	out := make([]User, 0, len(arr)/2)
	for i := 0; i+1 < len(arr); i += 2 {
		val, ok := arr[i+1].([]byte)
		if !ok {
			return nil, errors.New("unexpected redis response type")
		}
		var u User
		if err := json.Unmarshal(val, &u); err != nil {
			return nil, err
		}
		out = append(out, u)
	}

	sortUsers(out)
	return out, nil
}

func (s *redisStore) GetUser(ctx context.Context, username string) (User, error) {
	v, err := s.do(ctx, "HGET", s.usersKey(), username)
	if err != nil {
		return User{}, err
	}
	if v == nil {
		return User{}, ErrNotFound
	}

	b, ok := v.([]byte)
	if !ok {
		return User{}, errors.New("unexpected redis response type")
	}

	var u User
	if err := json.Unmarshal(b, &u); err != nil {
		return User{}, err
	}
	return u, nil
}

func (s *redisStore) SetUser(ctx context.Context, u User) error {
	raw, err := json.Marshal(u)
	if err != nil {
		return err
	}
	_, err = s.do(ctx, "HSET", s.usersKey(), u.Username, string(raw))
	return err
}

func (s *redisStore) DeleteUser(ctx context.Context, username string) error {
	v, err := s.do(ctx, "HDEL", s.usersKey(), username)
	if err != nil {
		return err
	}
	n, ok := v.(int64)
	if !ok {
		return errors.New("unexpected redis response type")
	}
	if n == 0 {
		return ErrNotFound
	}
	return nil
}

func redisCall(w io.Writer, rd *bufio.Reader, args ...string) (any, error) {
	if err := writeRESP(w, args); err != nil {
		return nil, err
	}
	return readRESP(rd)
}

func writeRESP(w io.Writer, args []string) error {
	if _, err := fmt.Fprintf(w, "*%d\r\n", len(args)); err != nil {
		return err
	}
	for _, a := range args {
		if _, err := fmt.Fprintf(w, "$%d\r\n%s\r\n", len(a), a); err != nil {
			return err
		}
	}
	return nil
}

func readRESP(r *bufio.Reader) (any, error) {
	b, err := r.ReadByte()
	if err != nil {
		return nil, err
	}

	switch b {
	case '+':
		s, err := readLine(r)
		if err != nil {
			return nil, err
		}
		return s, nil
	case '-':
		s, err := readLine(r)
		if err != nil {
			return nil, err
		}
		return nil, errors.New(s)
	case ':':
		s, err := readLine(r)
		if err != nil {
			return nil, err
		}
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return nil, err
		}
		return n, nil
	case '$':
		s, err := readLine(r)
		if err != nil {
			return nil, err
		}
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return nil, err
		}
		if n == -1 {
			return nil, nil
		}
		buf := make([]byte, n+2)
		if _, err := io.ReadFull(r, buf); err != nil {
			return nil, err
		}
		return buf[:n], nil
	case '*':
		s, err := readLine(r)
		if err != nil {
			return nil, err
		}
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return nil, err
		}
		if n == -1 {
			return nil, nil
		}
		out := make([]any, 0, n)
		for i := int64(0); i < n; i++ {
			v, err := readRESP(r)
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
		return out, nil
	default:
		return nil, errors.New("invalid redis response")
	}
}

func readLine(r *bufio.Reader) (string, error) {
	s, err := r.ReadString('\n')
	if err != nil {
		return "", err
	}
	s = strings.TrimSuffix(s, "\n")
	s = strings.TrimSuffix(s, "\r")
	return s, nil
}
