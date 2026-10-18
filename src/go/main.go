package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const version = "0.2.0"

var (
	secretNames = map[string]bool{
		".env": true, ".git": true, "id_rsa": true, "id_ed25519": true,
	}
)

type Config struct {
	Root            string
	Port            int
	EnableWrites    bool
	EnableExecution bool
	RelayURL        string
	AgentToken      string
}

func computeSHA256(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func isPathAllowed(root, relPath string) (string, error) {
	if strings.Contains(relPath, "..") || strings.HasPrefix(relPath, "/") || strings.HasPrefix(relPath, "\\") {
		return "", fmt.Errorf("path traversal denied")
	}
	parts := strings.Split(filepath.ToSlash(relPath), "/")
	for _, p := range parts {
		lp := strings.ToLower(p)
		if secretNames[lp] || strings.HasPrefix(lp, ".env.") || strings.HasSuffix(lp, ".pem") || strings.HasSuffix(lp, ".key") {
			return "", fmt.Errorf("credential or git path denied: %s", p)
		}
	}
	full := filepath.Join(root, relPath)
	cleanRoot := filepath.Clean(root)
	cleanFull := filepath.Clean(full)
	if cleanFull != cleanRoot && !strings.HasPrefix(cleanFull, cleanRoot+string(filepath.Separator)) {
		return "", fmt.Errorf("path outside workspace root")
	}
	return cleanFull, nil
}

func handleGetCapabilities(cfg Config) (map[string]interface{}, error) {
	return map[string]interface{}{
		"server": map[string]string{
			"name":    "nano-airbridge",
			"version": version,
		},
		"grants": map[string]bool{
			"writes":    cfg.EnableWrites,
			"execution": cfg.EnableExecution,
		},
		"limits": map[string]int{
			"max_file_size": 10 * 1024 * 1024,
			"page_size":     32 * 1024,
		},
		"workspace_root": cfg.Root,
	}, nil
}

func handleInspectWorkspace(root string) (map[string]interface{}, error) {
	var entries []map[string]interface{}
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		rel, _ := filepath.Rel(root, path)
		if rel == "." || rel == "" {
			return nil
		}
		name := strings.ToLower(info.Name())
		if secretNames[name] || strings.HasPrefix(name, ".env") || strings.HasPrefix(name, ".") {
			if info.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if !info.IsDir() {
			entries = append(entries, map[string]interface{}{
				"path":       filepath.ToSlash(rel),
				"kind":       "file",
				"size_bytes": info.Size(),
			})
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return map[string]interface{}{
		"action":        "list",
		"entries":       entries,
		"total_scanned": len(entries),
	}, nil
}

func handleReadFile(root, relPath string) (map[string]interface{}, error) {
	full, err := isPathAllowed(root, relPath)
	if err != nil {
		return nil, err
	}
	info, err := os.Lstat(full)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]interface{}{"path": relPath, "exists": false}, nil
		}
		return nil, err
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return nil, fmt.Errorf("symlinks denied")
	}
	data, err := os.ReadFile(full)
	if err != nil {
		return nil, err
	}
	return map[string]interface{}{
		"path":     relPath,
		"exists":   true,
		"size":     len(data),
		"sha256":   computeSHA256(data),
		"mtime_ns": info.ModTime().UnixNano(),
		"content":  string(data),
	}, nil
}

func handleWriteFile(root, relPath, content, expectedSHA256 string, enableWrites bool) (map[string]interface{}, error) {
	if !enableWrites {
		return nil, fmt.Errorf("write access denied; start with --enable-writes")
	}
	full, err := isPathAllowed(root, relPath)
	if err != nil {
		return nil, err
	}
	info, err := os.Lstat(full)
	if err == nil {
		if info.Mode()&os.ModeSymlink != 0 {
			return nil, fmt.Errorf("symlinks denied")
		}
		data, err := os.ReadFile(full)
		if err != nil {
			return nil, err
		}
		currentSHA := computeSHA256(data)
		if expectedSHA256 != "" && currentSHA != expectedSHA256 {
			return nil, fmt.Errorf("source_changed: expected %s, found %s", expectedSHA256, currentSHA)
		}
	} else if os.IsNotExist(err) {
		if expectedSHA256 != "" {
			return nil, fmt.Errorf("source_changed: file does not exist, expected empty precondition")
		}
	} else {
		return nil, err
	}

	if err := os.MkdirAll(filepath.Dir(full), 0755); err != nil {
		return nil, err
	}
	newData := []byte(content)
	if err := os.WriteFile(full, newData, 0644); err != nil {
		return nil, err
	}
	return map[string]interface{}{
		"path":       relPath,
		"written":    true,
		"size_bytes": len(newData),
		"sha256":     computeSHA256(newData),
	}, nil
}

func handleEditFile(root, relPath, oldText, newText, expectedSHA256 string, enableWrites bool) (map[string]interface{}, error) {
	if !enableWrites {
		return nil, fmt.Errorf("write access denied; start with --enable-writes")
	}
	full, err := isPathAllowed(root, relPath)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(full)
	if err != nil {
		return nil, err
	}
	currentSHA := computeSHA256(data)
	if expectedSHA256 != "" && currentSHA != expectedSHA256 {
		return nil, fmt.Errorf("source_changed: expected %s, found %s", expectedSHA256, currentSHA)
	}
	content := string(data)
	count := strings.Count(content, oldText)
	if count == 0 {
		return nil, fmt.Errorf("target_not_found: old_text not found in %s", relPath)
	}
	if count > 1 {
		return nil, fmt.Errorf("multiple_occurrences: old_text appears %d times, must be unique", count)
	}
	updatedContent := strings.Replace(content, oldText, newText, 1)
	newData := []byte(updatedContent)
	if err := os.WriteFile(full, newData, 0644); err != nil {
		return nil, err
	}
	return map[string]interface{}{
		"path":       relPath,
		"edited":     true,
		"size_bytes": len(newData),
		"sha256":     computeSHA256(newData),
	}, nil
}

func isCommandBlocked(cmdStr string, argv []string) error {
	full := cmdStr
	if len(argv) > 0 {
		full = strings.Join(argv, " ")
	}
	clean := strings.ToLower(full)
	// Check for any invocation of rm, rmdir, unlink
	tokens := strings.Fields(clean)
	for _, t := range tokens {
		if t == "rm" || t == "rmdir" || t == "unlink" || strings.HasPrefix(t, "rm-") {
			return fmt.Errorf("destructive command denied: '%s' is prohibited. Only write/execute commands are allowed.", t)
		}
	}
	// Also check for sub-commands, pipes, redirects, or chained commands
	for _, sep := range []string{";", "&&", "||", "|", "`", "$("} {
		if strings.Contains(clean, sep) {
			for _, chunk := range strings.Split(clean, sep) {
				chunk = strings.TrimSpace(chunk)
				fields := strings.Fields(chunk)
				if len(fields) > 0 {
					bin := filepath.Base(fields[0])
					if bin == "rm" || bin == "rmdir" || bin == "unlink" {
						return fmt.Errorf("destructive command denied: '%s' is prohibited. Only write/execute commands are allowed.", bin)
					}
				}
			}
		}
	}
	return nil
}

func handleRunCommand(root string, argv []string, cmdStr string, enableExec bool) (map[string]interface{}, error) {
	if !enableExec {
		return nil, fmt.Errorf("command execution disabled; start with --enable-execution")
	}
	if err := isCommandBlocked(cmdStr, argv); err != nil {
		return nil, err
	}
	var cmd *exec.Cmd
	if len(argv) > 0 {
		cmd = exec.Command(argv[0], argv[1:]...)
	} else if cmdStr != "" {
		if os.Getenv("OS") == "Windows_NT" {
			cmd = exec.Command("cmd", "/c", cmdStr)
		} else {
			cmd = exec.Command("sh", "-c", cmdStr)
		}
	} else {
		return nil, fmt.Errorf("empty command")
	}
	cmd.Dir = root
	out, err := cmd.CombinedOutput()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = 1
		}
	}
	return map[string]interface{}{
		"status": "succeeded",
		"command": map[string]interface{}{
			"exit_code": exitCode,
			"exited":    true,
		},
		"output": map[string]interface{}{
			"text": string(out),
		},
	}, nil
}

func executeTool(cfg Config, toolName string, args map[string]interface{}) (interface{}, error) {
	switch toolName {
	case "get_capabilities":
		return handleGetCapabilities(cfg)
	case "inspect_workspace":
		return handleInspectWorkspace(cfg.Root)
	case "read_file":
		p, _ := args["path"].(string)
		return handleReadFile(cfg.Root, p)
	case "write_file":
		p, _ := args["path"].(string)
		c, _ := args["content"].(string)
		sha, _ := args["expected_sha256"].(string)
		return handleWriteFile(cfg.Root, p, c, sha, cfg.EnableWrites)
	case "edit_file":
		p, _ := args["path"].(string)
		oldT, _ := args["old_text"].(string)
		newT, _ := args["new_text"].(string)
		sha, _ := args["expected_sha256"].(string)
		return handleEditFile(cfg.Root, p, oldT, newT, sha, cfg.EnableWrites)
	case "run_command":
		var argv []string
		if rawArgv, ok := args["argv"].([]interface{}); ok {
			for _, a := range rawArgv {
				if s, ok := a.(string); ok {
					argv = append(argv, s)
				}
			}
		}
		cmdStr, _ := args["command"].(string)
		return handleRunCommand(cfg.Root, argv, cmdStr, cfg.EnableExecution)
	default:
		return nil, fmt.Errorf("unknown tool: %s", toolName)
	}
}

var toolCatalog = []map[string]string{
	{"name": "get_capabilities", "description": "Inspect active grants, limits, and workspace identity"},
	{"name": "inspect_workspace", "description": "List directory contents or search text"},
	{"name": "read_file", "description": "Read file with SHA-256 CAS verification"},
	{"name": "write_file", "description": "Write or overwrite file with expected_sha256 precondition"},
	{"name": "edit_file", "description": "Edit contiguous text with expected_sha256 CAS precondition"},
	{"name": "run_command", "description": "Execute shell command inside root with execution receipt"},
}

func startHTTPServer(cfg Config) {
	getBaseURL := func(r *http.Request) string {
		scheme := "http"
		if r.TLS != nil || r.Header.Get("X-Forwarded-Proto") == "https" {
			scheme = "https"
		}
		host := r.Host
		if h := r.Header.Get("X-Forwarded-Host"); h != "" {
			host = h
		}
		return fmt.Sprintf("%s://%s", scheme, host)
	}

	helpHandler := func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		baseURL := getBaseURL(r)

		helpDoc := map[string]interface{}{
			"server": map[string]string{
				"name":        "nano-airbridge",
				"version":     version,
				"description": "Model Context Protocol & HTTP REST Workspace Bridge for AI Agents",
				"base_url":    baseURL,
			},
			"instructions_for_ai": map[string]string{
				"step_1_inspect_workspace": fmt.Sprintf("Perform HTTP GET to %s/inspect or run: curl -sS -H 'bypass-tunnel-reminder: 1' -H 'ngrok-skip-browser-warning: 1' '%s/inspect'", baseURL, baseURL),
				"step_2_read_file":         fmt.Sprintf("Perform HTTP GET to %s/read/<path> or run: curl -sS -H 'bypass-tunnel-reminder: 1' -H 'ngrok-skip-browser-warning: 1' '%s/read/customers_orders.csv'", baseURL, baseURL),
				"step_3_run_command":       fmt.Sprintf("Perform HTTP POST to %s/run or run: curl -sS -X POST '%s/run' -H 'Content-Type: application/json' -H 'bypass-tunnel-reminder: 1' -H 'ngrok-skip-browser-warning: 1' -d '{\"command\": \"which python3 node bash\"}'. Note: 'rm' commands are strictly prohibited.", baseURL, baseURL),
			},
			"tunnel_headers": map[string]string{
				"localtunnel": "bypass-tunnel-reminder: 1",
				"ngrok":       "ngrok-skip-browser-warning: 1",
			},
			"endpoints": map[string]map[string]interface{}{
				"inspect": {
					"method":       "GET",
					"url":          fmt.Sprintf("%s/inspect", baseURL),
					"description":  "Inspect all workspace files, relative paths, and sizes in bytes.",
					"example_curl": fmt.Sprintf("curl -sS -H 'bypass-tunnel-reminder: 1' -H 'ngrok-skip-browser-warning: 1' '%s/inspect'", baseURL),
				},
				"read_file": {
					"method":       "GET",
					"url_pattern":  fmt.Sprintf("%s/read/{path}", baseURL),
					"example":      fmt.Sprintf("%s/read/customers_orders.csv", baseURL),
					"description":  "Read file contents and obtain whole-file SHA-256 CAS digest.",
					"example_curl": fmt.Sprintf("curl -sS -H 'bypass-tunnel-reminder: 1' -H 'ngrok-skip-browser-warning: 1' '%s/read/customers_orders.csv'", baseURL),
				},
				"run_command": {
					"method":      "POST",
					"url":         fmt.Sprintf("%s/run", baseURL),
					"description": "Execute shell command on the host computer inside workspace root. 'rm' commands are strictly forbidden. Only write and compute commands are permitted.",
					"headers": map[string]string{
						"Content-Type":            "application/json",
						"bypass-tunnel-reminder":  "1",
						"ngrok-skip-browser-warning": "1",
					},
					"body_schema":  map[string]string{"command": "Shell command string (e.g. 'which python3 node bash', 'python3 -c \"...\"')"},
					"example_curl": fmt.Sprintf("curl -sS -X POST '%s/run' -H 'Content-Type: application/json' -H 'bypass-tunnel-reminder: 1' -H 'ngrok-skip-browser-warning: 1' -d '{\"command\": \"which python3 node bash\"}'", baseURL),
				},
				"help": {
					"method":       "GET",
					"url":          fmt.Sprintf("%s/help", baseURL),
					"description":  "Self-documenting path discovery directory.",
					"example_curl": fmt.Sprintf("curl -sS -H 'bypass-tunnel-reminder: 1' -H 'ngrok-skip-browser-warning: 1' '%s/help'", baseURL),
				},
				"healthz": {
					"method":       "GET",
					"url":          fmt.Sprintf("%s/healthz", baseURL),
					"description":  "Liveness probe.",
					"example_curl": fmt.Sprintf("curl -sS -H 'bypass-tunnel-reminder: 1' -H 'ngrok-skip-browser-warning: 1' '%s/healthz'", baseURL),
				},
			},
			"grants": map[string]bool{
				"writes":    cfg.EnableWrites,
				"execution": cfg.EnableExecution,
			},
			"tools": []map[string]interface{}{
				{
					"name":        "inspect_workspace",
					"description": "List all non-hidden files in the workspace.",
					"path":        fmt.Sprintf("GET %s/inspect or POST %s/mcp (tools/call)", baseURL, baseURL),
					"parameters":  map[string]string{},
				},
				{
					"name":        "read_file",
					"description": "Read file contents and obtain SHA-256 CAS digest for verification.",
					"path":        fmt.Sprintf("GET %s/read/<path> or POST %s/mcp (tools/call)", baseURL, baseURL),
					"parameters":  map[string]string{"path": "Relative path to target file within workspace"},
				},
				{
					"name":        "write_file",
					"description": "Write or overwrite file under CAS precondition.",
					"path":        fmt.Sprintf("POST %s/mcp (tools/call)", baseURL),
					"parameters":  map[string]string{"path": "Relative path", "content": "File content", "expected_sha256": "Previous SHA-256 or empty for new file"},
				},
				{
					"name":        "edit_file",
					"description": "Perform precise contiguous text replacement with CAS precondition.",
					"path":        fmt.Sprintf("POST %s/mcp (tools/call)", baseURL),
					"parameters":  map[string]string{"path": "Relative path", "old_text": "Exact text to replace", "new_text": "Replacement text", "expected_sha256": "Current file SHA-256"},
				},
				{
					"name":        "run_command",
					"description": "Execute shell command on the host computer. 'rm' is strictly prohibited. POST only.",
					"path":        fmt.Sprintf("POST %s/run with {\"command\": \"...\"} or POST %s/mcp (tools/call)", baseURL, baseURL),
					"parameters":  map[string]string{"command": "Shell command string in JSON body: {\"command\": \"...\"}"},
				},
			},
			"workspace_root": cfg.Root,
		}
		json.NewEncoder(w).Encode(helpDoc)
	}

	runHandler := func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		if r.Method == "OPTIONS" {
			w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(http.StatusMethodNotAllowed)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status": "error",
				"error":  "method not allowed: /run strictly requires HTTP POST with JSON body {\"command\": \"...\"}",
			})
			return
		}
		var body struct {
			Command string `json:"command"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.Command) == "" {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status": "error",
				"error":  "invalid request: provide JSON body {\"command\": \"...\"}",
			})
			return
		}
		res, err := handleRunCommand(cfg.Root, nil, body.Command, cfg.EnableExecution)
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status": "error",
				"error":  err.Error(),
			})
			return
		}
		json.NewEncoder(w).Encode(res)
	}

	route := func(pattern string, handler http.HandlerFunc) {
		http.HandleFunc(pattern, func(w http.ResponseWriter, r *http.Request) {
			fmt.Printf("[nano-airbridge] %s %s from %s (ua: %s)\n", r.Method, r.URL.Path, r.RemoteAddr, r.UserAgent())
			handler(w, r)
		})
	}

	route("/run", runHandler)
	route("/exec", runHandler)
	route("/help", helpHandler)
	route("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/" {
			helpHandler(w, r)
			return
		}
		http.NotFound(w, r)
	})
	route("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok", "server": "nano-airbridge-go", "version": version})
	})
	route("/inspect", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		res, err := handleInspectWorkspace(cfg.Root)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		json.NewEncoder(w).Encode(res)
	})
	route("/read/", func(w http.ResponseWriter, r *http.Request) {
		relPath := strings.TrimPrefix(r.URL.Path, "/read/")
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		res, err := handleReadFile(cfg.Root, relPath)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		json.NewEncoder(w).Encode(res)
	})
	route("/mcp", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		if r.Method == "OPTIONS" {
			w.Header().Set("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
			return
		}
		var msg map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
			http.Error(w, `{"error":"invalid_json"}`, http.StatusBadRequest)
			return
		}
		method, _ := msg["method"].(string)
		msgID := msg["id"]

		switch method {
		case "initialize":
			json.NewEncoder(w).Encode(map[string]interface{}{
				"jsonrpc": "2.0", "id": msgID,
				"result": map[string]interface{}{
					"protocolVersion": "2024-11-05",
					"serverInfo":      map[string]string{"name": "nano-airbridge", "version": version},
				},
			})
		case "tools/list":
			json.NewEncoder(w).Encode(map[string]interface{}{
				"jsonrpc": "2.0", "id": msgID,
				"result": map[string]interface{}{
					"tools": toolCatalog,
				},
			})
		case "tools/call":
			params, _ := msg["params"].(map[string]interface{})
			toolName, _ := params["name"].(string)
			args, _ := params["arguments"].(map[string]interface{})

			res, err := executeTool(cfg, toolName, args)
			if err != nil {
				json.NewEncoder(w).Encode(map[string]interface{}{
					"jsonrpc": "2.0", "id": msgID,
					"error": map[string]interface{}{"code": -32603, "message": err.Error()},
				})
				return
			}
			outBytes, _ := json.Marshal(res)
			json.NewEncoder(w).Encode(map[string]interface{}{
				"jsonrpc": "2.0", "id": msgID,
				"result": map[string]interface{}{
					"content": []map[string]string{{"type": "text", "text": string(outBytes)}},
				},
			})
		default:
			// Direct RPC method dispatch (e.g. method: "read_file", params: {...})
			var args map[string]interface{}
			if p, ok := msg["params"].(map[string]interface{}); ok {
				args = p
			}
			res, err := executeTool(cfg, method, args)
			if err != nil {
				json.NewEncoder(w).Encode(map[string]interface{}{
					"jsonrpc": "2.0", "id": msgID,
					"error": map[string]interface{}{"code": -32601, "message": err.Error()},
				})
				return
			}
			json.NewEncoder(w).Encode(map[string]interface{}{
				"jsonrpc": "2.0", "id": msgID,
				"result": res,
			})
		}
	})

	addr := fmt.Sprintf("0.0.0.0:%d", cfg.Port)
	fmt.Printf("[nano-airbridge] Native Standalone Binary v%s running on %s\n", version, addr)
	fmt.Printf("[nano-airbridge] Workspace: %s (writes=%v, exec=%v)\n", cfg.Root, cfg.EnableWrites, cfg.EnableExecution)
	if err := http.ListenAndServe(addr, nil); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
	}
}

func startRelayAgent(cfg Config) {
	token := cfg.AgentToken
	if token == "" {
		token = os.Getenv("NANO_RELAY_AGENT_TOKEN")
		if token == "" {
			token = "airbridge_agent_token_112233"
		}
	}
	relayBase := strings.TrimRight(strings.Split(cfg.RelayURL, "?")[0], "/")

	fmt.Printf("[nano-airbridge] Native Standalone Binary v%s connecting to Cloud Relay\n", version)
	fmt.Printf("[nano-airbridge] Relay: %s\n", relayBase)
	fmt.Printf("[nano-airbridge] Workspace: %s (writes=%v, exec=%v)\n", cfg.Root, cfg.EnableWrites, cfg.EnableExecution)

	regPayload := map[string]interface{}{
		"tools":  toolCatalog,
		"server": map[string]string{"name": "nano-airbridge-go", "version": version},
	}
	regBytes, _ := json.Marshal(regPayload)

	regReq, _ := http.NewRequest("POST", fmt.Sprintf("%s?op=register&t=%s", relayBase, token), bytes.NewReader(regBytes))
	regReq.Header.Set("Content-Type", "application/json")
	regReq.Header.Set("X-Agent-Token", token)
	client := &http.Client{Timeout: 35 * time.Second}
	resp, err := client.Do(regReq)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[nano-airbridge] Registration warning: %v\n", err)
	} else {
		resp.Body.Close()
		fmt.Printf("[nano-airbridge] Registered with cloud relay successfully.\n")
	}

	for {
		pollReq, _ := http.NewRequest("GET", fmt.Sprintf("%s?op=poll&wait=20&t=%s", relayBase, token), nil)
		pollReq.Header.Set("X-Agent-Token", token)
		pollResp, err := client.Do(pollReq)
		if err != nil {
			time.Sleep(2 * time.Second)
			continue
		}

		var pollData struct {
			Ok       bool                     `json:"ok"`
			Requests []map[string]interface{} `json:"requests"`
		}
		json.NewDecoder(pollResp.Body).Decode(&pollData)
		pollResp.Body.Close()

		for _, req := range pollData.Requests {
			internalID, _ := req["internal_id"].(string)
			method, _ := req["method"].(string)
			msgID := req["id"]

			var reply map[string]interface{}
			if method == "tools/call" {
				params, _ := req["params"].(map[string]interface{})
				toolName, _ := params["name"].(string)
				args, _ := params["arguments"].(map[string]interface{})

				res, err := executeTool(cfg, toolName, args)
				if err != nil {
					reply = map[string]interface{}{
						"jsonrpc": "2.0",
						"id":      msgID,
						"result": map[string]interface{}{
							"isError": true,
							"content": []map[string]string{{"type": "text", "text": fmt.Sprintf("{\"error\":\"%s\"}", err.Error())}},
						},
					}
				} else {
					outBytes, _ := json.Marshal(res)
					reply = map[string]interface{}{
						"jsonrpc": "2.0",
						"id":      msgID,
						"result": map[string]interface{}{
							"content": []map[string]string{{"type": "text", "text": string(outBytes)}},
						},
					}
				}
			} else {
				reply = map[string]interface{}{
					"jsonrpc": "2.0",
					"id":      msgID,
					"error":   map[string]interface{}{"code": -32601, "message": "Method not found"},
				}
			}

			postPayload := map[string]interface{}{
				"internal_id": internalID,
				"response":    reply,
			}
			postBytes, _ := json.Marshal(postPayload)
			pReq, _ := http.NewRequest("POST", fmt.Sprintf("%s?op=response&t=%s", relayBase, token), bytes.NewReader(postBytes))
			pReq.Header.Set("Content-Type", "application/json")
			pReq.Header.Set("X-Agent-Token", token)
			if pResp, pErr := client.Do(pReq); pErr == nil {
				pResp.Body.Close()
			}
		}
	}
}

func main() {
	rootFlag := flag.String("root", ".", "Workspace root directory")
	portFlag := flag.Int("port", 8899, "HTTP port (default 8899)")
	writesFlag := flag.Bool("enable-writes", false, "Enable workspace writes")
	execFlag := flag.Bool("enable-execution", false, "Enable CLI execution")
	relayFlag := flag.String("relay", "", "Cloud relay URL for outbound polling")
	tokenFlag := flag.String("token", "", "Agent token for cloud relay")
	flag.Parse()

	absRoot, err := filepath.Abs(*rootFlag)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Invalid root: %v\n", err)
		os.Exit(1)
	}

	cfg := Config{
		Root:            absRoot,
		Port:            *portFlag,
		EnableWrites:    *writesFlag,
		EnableExecution: *execFlag,
		RelayURL:        *relayFlag,
		AgentToken:      *tokenFlag,
	}

	if cfg.RelayURL != "" {
		startRelayAgent(cfg)
	} else {
		startHTTPServer(cfg)
	}
}
