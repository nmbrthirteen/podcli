package provision

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAllowedDownloadHost(t *testing.T) {
	allow := []string{
		"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
		"huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.huggingface.co",
		"nodejs.org", "evermeet.cx", "johnvansickle.com", "www.johnvansickle.com",
	}
	for _, h := range allow {
		if !allowedDownloadHost(h) {
			t.Errorf("expected %q to be allowed", h)
		}
	}
	// Suffix spoofing must not pass: a trusted name as a left-label is not enough.
	deny := []string{
		"evil.com", "github.com.evil.com", "huggingface.co.attacker.net",
		"githubusercontent.com.evil.com", "127.0.0.1", "",
	}
	for _, h := range deny {
		if allowedDownloadHost(h) {
			t.Errorf("expected %q to be denied", h)
		}
	}
}

func TestDownloadClientRefusesUntrustedRedirect(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "https://attacker.example/payload", http.StatusFound)
	}))
	defer srv.Close()

	resp, err := downloadHTTPClient().Get(srv.URL)
	if err == nil {
		if resp != nil {
			resp.Body.Close()
		}
		t.Fatal("expected redirect to an untrusted host to be refused")
	}
}
