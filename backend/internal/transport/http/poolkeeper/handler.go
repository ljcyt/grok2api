package poolkeeper

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/chenyme/grok2api/backend/internal/shared/response"
	"github.com/gin-gonic/gin"
)

// Handler proxies admin UI calls to the local poolkeeper service without embedding an iframe.
type Handler struct {
	baseURL    string
	token      string
	httpClient *http.Client
}

func NewHandler() *Handler {
	base := strings.TrimSpace(os.Getenv("POOLKEEPER_BASE_URL"))
	if base == "" {
		base = "http://127.0.0.1:9109"
	}
	return &Handler{
		baseURL: strings.TrimRight(base, "/"),
		token:   strings.TrimSpace(os.Getenv("POOLKEEPER_UI_TOKEN")),
		httpClient: &http.Client{
			Timeout: 60 * time.Second,
			Transport: &http.Transport{
				Proxy: http.ProxyFromEnvironment,
			},
		},
	}
}

func (h *Handler) Register(router *gin.RouterGroup) {
	router.GET("/poolkeeper/status", h.status)
	router.GET("/poolkeeper/config", h.getConfig)
	router.PUT("/poolkeeper/config", h.putConfig)
	router.POST("/poolkeeper/run", h.run)
}

func (h *Handler) status(c *gin.Context) {
	h.proxyJSON(c, http.MethodGet, "/api/status", nil)
}

func (h *Handler) getConfig(c *gin.Context) {
	h.proxyJSON(c, http.MethodGet, "/api/config", nil)
}

func (h *Handler) putConfig(c *gin.Context) {
	body, err := io.ReadAll(io.LimitReader(c.Request.Body, 1<<20))
	if err != nil {
		response.Error(c, http.StatusBadRequest, "invalidRequest", "读取请求失败")
		return
	}
	h.proxyJSON(c, http.MethodPut, "/api/config", body)
}

func (h *Handler) run(c *gin.Context) {
	h.proxyJSON(c, http.MethodPost, "/api/run", []byte("{}"))
}

func (h *Handler) proxyJSON(c *gin.Context, method, path string, body []byte) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 55*time.Second)
	defer cancel()

	var reader io.Reader
	if len(body) > 0 {
		reader = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, h.baseURL+path, reader)
	if err != nil {
		response.Error(c, http.StatusBadGateway, "poolkeeperUnavailable", "无法连接补号服务")
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if h.token != "" {
		req.Header.Set("X-Poolkeeper-Token", h.token)
	}

	resp, err := h.httpClient.Do(req)
	if err != nil {
		response.Error(c, http.StatusBadGateway, "poolkeeperUnavailable", fmt.Sprintf("补号服务不可达: %v", err))
		return
	}
	defer resp.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		response.Error(c, http.StatusBadGateway, "poolkeeperUnavailable", "读取补号服务响应失败")
		return
	}

	// Normalize to admin envelope {data: ...} when upstream already wraps data.
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		var parsed any
		if err := json.Unmarshal(payload, &parsed); err != nil {
			response.Error(c, http.StatusBadGateway, "poolkeeperInvalidResponse", "补号服务返回了无效 JSON")
			return
		}
		if obj, ok := parsed.(map[string]any); ok {
			if data, hasData := obj["data"]; hasData {
				response.Success(c, http.StatusOK, data)
				return
			}
		}
		response.Success(c, http.StatusOK, parsed)
		return
	}

	// surface upstream error
	var parsed map[string]any
	if json.Unmarshal(payload, &parsed) == nil {
		if errObj, ok := parsed["error"].(string); ok && errObj != "" {
			response.Error(c, resp.StatusCode, "poolkeeperError", errObj)
			return
		}
	}
	response.Error(c, resp.StatusCode, "poolkeeperError", strings.TrimSpace(string(payload)))
}
