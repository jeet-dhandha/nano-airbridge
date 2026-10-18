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

const version = "0.1.0"

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

func handleRunCommand(root string, argv []string, enableExec bool) (map[string]interface{}, error) {
	if !enableExec {
		return nil, fmt.Errorf("command execution disabled; start with --enable-execution")
	}
	if len(argv) == 0 {
		return nil, fmt.Errorf("empty command")
	}
	cmd := exec.Command(argv[0], argv[1:]...)
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

func startHTTPServer(cfg Config) {
	http.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok", "server": "nano-airbridge-go", "version": version})
	})

	http.HandleFunc("/inspect", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Access-Control-Allow-Origin", "*")
		res, err := handleInspectWorkspace(cfg.Root)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		json.NewEncoder(w).Encode(res)
	})

	http.HandleFunc("/read/", func(w http.ResponseWriter, r *http.Request) {
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

	http.HandleFunc("/mcp", func(w http.ResponseWriter, r *http.Request) {
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
					"tools": []map[string]string{
						{"name": "inspect_workspace", "description": "List directory contents or search text"},
						{"name": "read_file", "description": "Read file with SHA-256 CAS verification"},
						{"name": "run_command", "description": "Execute shell command inside root"},
					},
				},
			})
		case "tools/call":
			params, _ := msg["params"].(map[string]interface{})
			toolName, _ := params["name"].(string)
			args, _ := params["arguments"].(map[string]interface{})

			var (
				res interface{}
				err error
			)
			switch toolName {
			case "read_file":
				p, _ := args["path"].(string)
				res, err = handleReadFile(cfg.Root, p)
			case "inspect_workspace":
				res, err = handleInspectWorkspace(cfg.Root)
			case "run_command":
				var argv []string
				if rawArgv, ok := args["argv"].([]interface{}); ok {
					for _, a := range rawArgv {
						if s, ok := a.(string); ok {
							argv = append(argv, s)
						}
					}
				}
				res, err = handleRunCommand(cfg.Root, argv, cfg.EnableExecution)
			default:
				err = fmt.Errorf("unknown tool: %s", toolName)
			}
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

	// 1. Register tools
	regPayload := map[string]interface{}{
		"tools": []map[string]string{
			{"name": "inspect_workspace", "description": "List directory contents or search text"},
			{"name": "read_file", "description": "Read file with SHA-256 CAS verification"},
			{"name": "run_command", "description": "Execute shell command inside root"},
		},
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

	// 2. Outbound long-polling loop
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

				var (
					res interface{}
					err error
				)
				switch toolName {
				case "read_file":
					p, _ := args["path"].(string)
					res, err = handleReadFile(cfg.Root, p)
				case "inspect_workspace":
					res, err = handleInspectWorkspace(cfg.Root)
				case "run_command":
					var argv []string
					if rawArgv, ok := args["argv"].([]interface{}); ok {
						for _, a := range rawArgv {
							if s, ok := a.(string); ok {
								argv = append(argv, s)
							}
						}
					}
					res, err = handleRunCommand(cfg.Root, argv, cfg.EnableExecution)
				default:
					err = fmt.Errorf("unknown tool: %s", toolName)
				}

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
