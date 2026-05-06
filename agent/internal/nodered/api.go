package nodered

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	baseURL    string
	token      string
	apiVersion string
	http       *http.Client
}

func NewClient(baseURL, token, apiVersion string) *Client {
	return &Client{
		baseURL:    strings.TrimRight(baseURL, "/"),
		token:      token,
		apiVersion: apiVersion,
		http:       &http.Client{Timeout: 30 * time.Second},
	}
}

func (c *Client) DeployFlows(body json.RawMessage, deployType, apiVersion string) (string, error) {
	u := c.baseURL + "/flows"
	req, err := http.NewRequest(http.MethodPost, u, bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	ver := apiVersion
	if ver == "" {
		ver = c.apiVersion
	}
	if ver != "" {
		req.Header.Set("Node-RED-API-Version", ver)
	}
	if deployType != "" {
		req.Header.Set("Node-RED-Deployment-Type", deployType)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNoContent {
		return "", nil
	}
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(resp.Body)
		return "", errors.New(string(b))
	}
	var r struct {
		Rev string `json:"rev"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return "", err
	}
	return r.Rev, nil
}

func (c *Client) InstallNode(module, version string) error {
	u := c.baseURL + "/nodes"
	payload := map[string]string{"module": module}
	if version != "" {
		payload["version"] = version
	}
	b, _ := json.Marshal(payload)
	req, err := http.NewRequest(http.MethodPost, u, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		data, _ := io.ReadAll(resp.Body)
		return errors.New(string(data))
	}
	return nil
}
