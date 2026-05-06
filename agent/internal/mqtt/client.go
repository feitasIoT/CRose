package mqtt

import (
	"crypto/tls"
	"encoding/json"
	"log"
	"time"

	"mygoagent/internal/config"

	paho "github.com/eclipse/paho.mqtt.golang"
)

type Client struct {
	c           paho.Client
	deviceID    string
	statusTopic string
}

func NewClient(cfg config.MQTTConfig, deviceID string) (*Client, error) {
	opts := paho.NewClientOptions().AddBroker(cfg.BrokerURL)
	if cfg.Username != "" {
		opts.SetUsername(cfg.Username)
		opts.SetPassword(cfg.Password)
	}
	if cfg.ClientID != "" {
		opts.SetClientID(cfg.ClientID)
	}
	if cfg.TLSInsecure {
		opts.SetTLSConfig(&tls.Config{InsecureSkipVerify: true})
	}
	opts.SetOrderMatters(false)
	opts.SetKeepAlive(30 * time.Second)
	opts.SetPingTimeout(10 * time.Second)
	onlinePayload, _ := json.Marshal(map[string]string{"id": deviceID, "status": "online"})
	offlinePayload, _ := json.Marshal(map[string]string{"id": deviceID, "status": "offline"})
	// 遗嘱消息
	opts.SetWill(cfg.TopicStatus, string(offlinePayload), 1, true)
	opts.OnConnect = func(c paho.Client) {
		log.Printf("MQTT connected broker=%s clientID=%s", cfg.BrokerURL, cfg.ClientID)
		// 初始消息
		c.Publish(cfg.TopicStatus, 1, true, string(onlinePayload))
	}
	c := paho.NewClient(opts)
	if token := c.Connect(); token.Wait() && token.Error() != nil {
		return nil, token.Error()
	}
	return &Client{c: c, deviceID: deviceID, statusTopic: cfg.TopicStatus}, nil
}

func (c *Client) Subscribe(topic string, fn func([]byte) (string, error)) error {
	h := func(_ paho.Client, m paho.Message) {
		log.Printf("MQTT received topic=%s payload=%s", topic, string(m.Payload()))
		resp, err := fn(m.Payload())
		if err != nil {
			c.publishStatus(`{"status":"error","error":"` + escape(err.Error()) + `"}`)
			log.Printf("MQTT status publish error=%s", err.Error())
			return
		}
		if resp != "" {
			c.publishStatus(resp)
		}
	}
	token := c.c.Subscribe(topic, 1, h)
	token.Wait()
	if token.Error() != nil {
		return token.Error()
	}
	log.Printf("MQTT subscribed topic=%s qos=%d", topic, 1)
	return nil
}

func (c *Client) publishStatus(payload string) {
	t := c.c.Publish(c.statusTopic, 1, false, payload)
	t.Wait()
	if t.Error() != nil {
		log.Printf("MQTT publish error topic=%s err=%v", c.statusTopic, t.Error())
	}
}

func (c *Client) PublishStatus(payload string) {
	c.publishStatus(payload)
}

func (c *Client) PublishPresence(status string, retained bool) {
	b, _ := json.Marshal(map[string]string{"id": c.deviceID, "status": status})
	t := c.c.Publish(c.statusTopic, 1, retained, b)
	t.Wait()
	if t.Error() != nil {
		log.Printf("MQTT presence publish error topic=%s err=%v", c.statusTopic, t.Error())
	}
}

func (c *Client) Disconnect() {
	if c.c.IsConnected() {
		c.c.Disconnect(250)
	}
}

func escape(s string) string {
	r := make([]rune, 0, len(s))
	for _, ch := range s {
		if ch == '"' {
			r = append(r, '\\', '"')
		} else if ch == '\\' {
			r = append(r, '\\', '\\')
		} else {
			r = append(r, ch)
		}
	}
	return string(r)
}
