package account

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	accountdomain "github.com/chenyme/grok2api/backend/internal/domain/account"
	"github.com/chenyme/grok2api/backend/internal/infra/provider"
	"github.com/chenyme/grok2api/backend/internal/infra/provider/conversation"
)

// ProbeClassification is a non-sensitive probe outcome for pool maintenance.
type ProbeClassification string

const (
	ProbeAlive         ProbeClassification = "alive"
	ProbeQuotaLimited  ProbeClassification = "quota_limited"
	ProbeSoftAlive     ProbeClassification = "soft_alive"
	ProbeNetworkError  ProbeClassification = "network_error"
	ProbeSuspectDead   ProbeClassification = "suspect_dead"
	ProbeConfirmedDead ProbeClassification = "confirmed_dead"
	ProbeSkipped       ProbeClassification = "skipped"
	ProbeError         ProbeClassification = "error"
)

const (
	defaultBuildProbeModel       = "grok-4.5"
	defaultBuildProbeTimeout     = 20 * time.Second
	defaultBuildProbeConcurrency = 5
	maxBuildProbeAccounts        = 200
	maxBuildProbeConcurrency     = 16
	buildProbeDiagnosticLimit    = 400
)

// BuildProbeResult is returned to admin clients without any credential material.
type BuildProbeResult struct {
	AccountID      string              `json:"account_id"`
	Classification ProbeClassification `json:"classification"`
	StatusCode     int                 `json:"status_code"`
	Reason         string              `json:"reason"`
	LatencyMs      int64               `json:"latency_ms"`
}

// ProbeBuildAccounts runs in-process Build chat probes via the registered CLI provider.
// Tokens stay inside the process; only classifications are returned.
func (s *Service) ProbeBuildAccounts(ctx context.Context, ids []uint64, model string, timeout time.Duration, concurrency int) ([]BuildProbeResult, error) {
	ids, err := normalizeIDs(ids, maxBuildProbeAccounts)
	if err != nil {
		return nil, err
	}
	if len(ids) == 0 {
		return nil, invalidInput("account_ids 不能为空")
	}
	model = strings.TrimSpace(model)
	if model == "" {
		model = defaultBuildProbeModel
	}
	if timeout <= 0 {
		timeout = defaultBuildProbeTimeout
	}
	if timeout > 60*time.Second {
		timeout = 60 * time.Second
	}
	if concurrency <= 0 {
		concurrency = defaultBuildProbeConcurrency
	}
	if concurrency > maxBuildProbeConcurrency {
		concurrency = maxBuildProbeConcurrency
	}
	if s.providers == nil {
		return nil, fmt.Errorf("%w: Provider 未注册", ErrUnsupported)
	}
	adapter, ok := s.providers.Responses(accountdomain.ProviderBuild)
	if !ok {
		return nil, fmt.Errorf("%w: Grok Build Provider 未注册", ErrUnsupported)
	}

	results := make([]BuildProbeResult, len(ids))
	sem := make(chan struct{}, concurrency)
	var wg sync.WaitGroup
	for index, id := range ids {
		wg.Add(1)
		go func(i int, accountID uint64) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
			case <-ctx.Done():
				results[i] = BuildProbeResult{
					AccountID: strconv.FormatUint(accountID, 10), Classification: ProbeError,
					Reason: "context_canceled",
				}
				return
			}
			defer func() { <-sem }()
			results[i] = s.probeOneBuildAccount(ctx, adapter, accountID, model, timeout)
		}(index, id)
	}
	wg.Wait()
	return results, nil
}

func (s *Service) probeOneBuildAccount(ctx context.Context, adapter provider.ResponseAdapter, accountID uint64, model string, timeout time.Duration) BuildProbeResult {
	result := BuildProbeResult{AccountID: strconv.FormatUint(accountID, 10)}
	credential, err := s.accounts.Get(ctx, accountID)
	if err != nil {
		result.Classification = ProbeError
		result.Reason = "account_lookup_failed"
		return result
	}
	if credential.Provider != accountdomain.ProviderBuild {
		result.Classification = ProbeSkipped
		result.Reason = "not_build_provider"
		return result
	}
	if strings.TrimSpace(credential.EncryptedAccessToken) == "" {
		result.Classification = ProbeSuspectDead
		result.Reason = "missing_access_token"
		return result
	}

	body, err := json.Marshal(map[string]any{
		"model":       model,
		"messages":    []map[string]string{{"role": "user", "content": "1+1=?"}},
		"max_tokens":  10,
		"stream":      false,
		"temperature": 0,
	})
	if err != nil {
		result.Classification = ProbeError
		result.Reason = "encode_request_failed"
		return result
	}

	probeCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	started := time.Now()
	response, err := adapter.ForwardResponse(probeCtx, provider.ResponseResourceRequest{
		Credential:    credential,
		Method:        http.MethodPost,
		Path:          "/responses",
		Body:          body,
		Model:         model,
		Streaming:     false,
		NormalizeBody: true,
		Operation:     conversation.OperationChat,
	})
	result.LatencyMs = time.Since(started).Milliseconds()
	if err != nil {
		result.Classification = ProbeNetworkError
		result.Reason = classifyNetworkError(err)
		return result
	}
	defer response.Body.Close()

	snippet, _ := io.ReadAll(io.LimitReader(response.Body, buildProbeDiagnosticLimit))
	result.StatusCode = response.StatusCode
	result.Classification, result.Reason = classifyBuildProbeHTTP(response.StatusCode, string(snippet))
	return result
}

func classifyNetworkError(err error) string {
	msg := strings.ToLower(err.Error())
	switch {
	case strings.Contains(msg, "timeout") || strings.Contains(msg, "deadline"):
		return "build-timeout"
	case strings.Contains(msg, "tls") || strings.Contains(msg, "ssl") || strings.Contains(msg, "x509"):
		return "ssl_error"
	case strings.Contains(msg, "proxy"):
		return "proxy_error"
	case strings.Contains(msg, "connection") || strings.Contains(msg, "connect"):
		return "connection_error"
	default:
		return "network_error"
	}
}

func classifyBuildProbeHTTP(status int, body string) (ProbeClassification, string) {
	low := strings.ToLower(body)
	switch {
	case status == http.StatusOK:
		return ProbeAlive, "answered"
	case status == http.StatusTooManyRequests:
		return ProbeQuotaLimited, "quota-exhausted"
	case status == http.StatusForbidden:
		if strings.Contains(low, "cloudflare") || strings.Contains(low, "just a moment") || strings.Contains(low, "cf-ray") {
			return ProbeSoftAlive, "cf-blocked"
		}
		if strings.Contains(low, "permission-denied") || strings.Contains(low, "access denied") || strings.Contains(low, "forbidden") {
			return ProbeSuspectDead, "forbidden"
		}
		return ProbeSuspectDead, "forbidden"
	case status == http.StatusUnauthorized || status == http.StatusBadRequest:
		return ProbeSuspectDead, fmt.Sprintf("auth-fail:%d", status)
	case status >= 500:
		return ProbeSoftAlive, fmt.Sprintf("upstream-%d", status)
	case status == 0:
		return ProbeNetworkError, "empty-status"
	default:
		return ProbeError, fmt.Sprintf("http-%d", status)
	}
}
