<?php
/**
 * nano-airbridge relay — one-file MCP relay for free PHP hosting.
 *
 * Bridges a remote MCP client (ChatGPT custom connector) to ONE local agent
 * (nano_agent.py next to nano_airbridge.py) without any ngrok/tunnel install.
 * Architecture mirrors OpenAI's Secure MCP Tunnel (queue + outbound-only long
 * poll + response post), with this PHP file as the hosted control plane.
 *
 *   ChatGPT cloud ──▶ POST .../relay.php?op=mcp&s=CLIENT_SECRET   (MCP endpoint)
 *   relay_agent   ──▶ GET  .../relay.php?op=poll                  (long poll, outbound)
 *   relay_agent   ──▶ POST .../relay.php?op=response              (outbound)
 *   relay_agent   ──▶ POST .../relay.php?op=register              (tools/list cache)
 *
 * Endpoints (PATH_INFO style `relay.php/op/...` also works):
 *   ?op=mcp&s=SECRET       MCP JSON-RPC for the chat client (stateless, JSON).
 *                          initialize / ping / tools/list answered here from the
 *                          cached agent registration; everything else is queued.
 *   ?op=register           agent: publish tools/list snapshot        (agent token)
 *   ?op=poll               agent: claim queued requests             (agent token)
 *   ?op=response           agent: post JSON-RPC responses           (agent token)
 *   ?op=healthz            liveness + agent presence, no secrets
 *
 * Config: env NANO_RELAY_CLIENT_SECRET / NANO_RELAY_AGENT_TOKEN (and optional
 * NANO_RELAY_MCP_WAIT / NANO_RELAY_POLL_WAIT), else `nano_relay_config.php` beside this
 * file, else one is auto-generated with random secrets on first run (like OM
 * Core's first-run init) and the path is logged — read it and keep it private.
 *
 * Data: ./data/{queue,inflight,responses,meta} with web access denied.
 * Requires PHP 8.0+. No database, no composer, no extensions beyond json+hash.
 *
 * MIT — derived from the nano-airbridge / Open Machine (MIT © 2026 OpenMachineLabs)
 * local-first design. Security model: TLS + unguessable client secret + agent
 * token; the LOCAL grants on each user's machine remain the real safety boundary.
 */

declare(strict_types=1);

const RELAY_VERSION = "0.1.0";
const QUEUE_MAX = 100;          // pending requests before refusing new work
const STALE_SECONDS = 600;      // housekeeping: drop abandoned files

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

function relay_config(): array
{
    static $config = null;
    if ($config !== null) {
        return $config;
    }
    $env = [
        "client_secret" => getenv("NANO_RELAY_CLIENT_SECRET") ?: null,
        "agent_token"   => getenv("NANO_RELAY_AGENT_TOKEN") ?: null,
        "mcp_wait"      => (int) (getenv("NANO_RELAY_MCP_WAIT") ?: 24),
        "poll_wait"     => (int) (getenv("NANO_RELAY_POLL_WAIT") ?: 20),
    ];
    $file = __DIR__ . "/nano_relay_config.php";
    if (is_file($file)) {
        $fromFile = (array) require $file;
        $env = array_merge($fromFile, array_filter($env, static fn ($v) => $v !== null));
    } elseif ($env["client_secret"] === null || $env["agent_token"] === null) {
        $generated = [
            "client_secret" => bin2hex(random_bytes(24)),
            "agent_token"   => bin2hex(random_bytes(24)),
            "mcp_wait"      => 24,
            "poll_wait"     => 20,
            "created_at"    => date("c"),
        ];
        $export = "<?php\n// nano-airbridge relay secrets — keep private. Generated " . date("c") . "\nreturn "
            . var_export($generated, true) . ";\n";
        if (@file_put_contents($file, $export, LOCK_EX) !== false) {
            @chmod($file, 0600);
            @file_put_contents(__DIR__ . "/data/.htaccess", "Require all denied\nDeny from all\n");
        }
        error_log("nano-airbridge relay: wrote new credentials to " . $file . " — read and keep private.");
        $env = array_merge($generated, array_filter($env, static fn ($v) => $v !== null));
    }
    $env["mcp_wait"] = max(2, min(25, (int) $env["mcp_wait"]));
    $env["poll_wait"] = max(1, min(25, (int) $env["poll_wait"]));
    return $config = $env;
}

function clean_session(string $s): string
{
    $out = "";
    $len = strlen($s);
    for ($i = 0; $i < $len; $i++) {
        $c = $s[$i];
        if (ctype_alnum($c) || $c === "-" || $c === "_") {
            $out .= $c;
        }
    }
    return substr($out, 0, 64);
}

function request_session(): string
{
    $raw = (string) ($_GET["session"] ?? ($_SERVER["HTTP_X_SESSION_ID"] ?? ""));
    return clean_session($raw);
}

function data_dir(string $sub, string $session = ""): string
{
    $clean = clean_session($session);
    $base = $clean !== "" ? __DIR__ . "/data/sessions/" . $clean : __DIR__ . "/data";
    $dir = $base . "/" . $sub;
    if (!is_dir($dir)) {
        @mkdir($dir, 0700, true);
        @file_put_contents(__DIR__ . "/data/.htaccess", "Require all denied\nDeny from all\n");
    }
    return $dir;
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

function respond_json(int $status, array $body): void
{
    http_response_code($status);
    header("Content-Type: application/json");
    echo json_encode($body, JSON_UNESCAPED_SLASHES);
    exit;
}

function read_body(): array
{
    $raw = file_get_contents("php://input") ?: "";
    try {
        $body = json_decode($raw, true, 256, JSON_THROW_ON_ERROR);
    } catch (\JsonException $e) {
        respond_json(400, ["error" => "invalid_json"]);
    }
    return is_array($body) ? $body : [];
}

function request_op(): string
{
    $path = $_SERVER["PATH_INFO"] ?? "";
    if ($path !== "") {
        $part = trim(explode("/", $path)[1] ?? "");
        if ($part !== "") {
            return $part;
        }
    }
    return (string) ($_GET["op"] ?? "");
}

function request_secret(): string
{
    $path = $_SERVER["PATH_INFO"] ?? "";
    $parts = array_values(array_filter(explode("/", $path), static fn ($p) => $p !== ""));
    if (($parts[0] ?? "") === "mcp" && isset($parts[1])) {
        return $parts[1];
    }
    return (string) ($_GET["s"] ?? ($_SERVER["HTTP_X_MCP_SECRET"] ?? ""));
}

function require_agent_token(): void
{
    $token = (string) ($_SERVER["HTTP_AUTHORIZATION"] ?? "");
    if (str_starts_with($token, "Bearer ")) {
        $token = substr($token, 7);
    }
    $token = (string) ($_GET["t"] ?? ($_SERVER["HTTP_X_AGENT_TOKEN"] ?? $token));
    $expected = (string) relay_config()["agent_token"];
    if ($token === "" || !hash_equals($expected, $token)) {
        respond_json(401, ["error" => "bad_agent_token"]);
    }
}

function rpc_error($id, int $code, string $message): array
{
    return ["jsonrpc" => "2.0", "id" => $id, "error" => ["code" => $code, "message" => $message]];
}

function rpc_tool_failure($id, string $code, string $message, string $effect): array
{
    // Bounded tool-level failure (OM style): isError result, never a raw crash.
    $payload = ["error" => ["code" => $code, "message" => $message], "effect" => $effect];
    return ["jsonrpc" => "2.0", "id" => $id, "result" => [
        "content" => [["type" => "text", "text" => json_encode($payload)]],
        "structuredContent" => $payload,
        "isError" => true,
    ]];
}

// ---------------------------------------------------------------------------
// Queue mechanics
// ---------------------------------------------------------------------------

function housekeep(string $session = ""): void
{
    $cutoff = time() - STALE_SECONDS;
    foreach (["queue", "inflight", "responses"] as $sub) {
        foreach ((array) glob(data_dir($sub, $session) . "/*.json") as $file) {
            if (@filemtime($file) < $cutoff) {
                @unlink($file);
            }
        }
    }
}

function enqueue(string $method, $params, $originalId, string $session = ""): array
{
    $dir = data_dir("queue", $session);
    if (count((array) glob($dir . "/*.json")) >= QUEUE_MAX) {
        return rpc_tool_failure($originalId, "relay_busy",
            "The relay queue is full; the local agent has not drained pending work.",
            "known_no_effect");
    }
    $internal = "r" . bin2hex(random_bytes(11));
    $record = [
        "internal_id" => $internal,
        "original_id" => $originalId,
        "method" => $method,
        "params" => $params,
        "enqueued_at" => time(),
    ];
    if (@file_put_contents($dir . "/" . $internal . ".json", json_encode($record), LOCK_EX) === false) {
        return rpc_tool_failure($originalId, "relay_unavailable",
            "The relay could not queue this request.", "known_no_effect");
    }
    return $record;
}

function claim_pending(int $limit, string $session = ""): array
{
    $claimed = [];
    $files = (array) glob(data_dir("queue", $session) . "/*.json");
    sort($files);
    foreach ($files as $file) {
        if (count($claimed) >= $limit) {
            break;
        }
        $name = basename($file);
        if (@rename($file, data_dir("inflight", $session) . "/" . $name)) {
            $record = json_decode((string) @file_get_contents(data_dir("inflight", $session) . "/" . $name), true);
            if (is_array($record)) {
                $claimed[] = $record;
            }
        }
    }
    return $claimed;
}

// ---------------------------------------------------------------------------
// MCP endpoint (chat-client side)
// ---------------------------------------------------------------------------

function handle_mcp(string $secret): void
{
    $config = relay_config();
    $session = "";
    $cleanSecret = clean_session($secret);
    if (hash_equals((string) $config["client_secret"], $secret)) {
        $session = request_session();
        if ($session === "") {
            $sessionDirs = (array) glob(__DIR__ . "/data/sessions/*");
            $newestTime = 0;
            $newestSession = "";
            foreach ($sessionDirs as $sDir) {
                $sAlive = (int) (@file_get_contents($sDir . "/meta/alive") ?: 0);
                if ($sAlive > $newestTime) {
                    $newestTime = $sAlive;
                    $newestSession = basename($sDir);
                }
            }
            if ($newestSession !== "") {
                $session = $newestSession;
            }
        }
    } elseif ($cleanSecret !== "" && (is_dir(__DIR__ . "/data/sessions/" . $cleanSecret) || is_file(__DIR__ . "/data/sessions/" . $cleanSecret . "/meta/agent.json"))) {
        $session = $cleanSecret;
    } else {
        respond_json(401, ["error" => "bad_client_secret"]);
    }
    $message = read_body();
    $method = (string) ($message["method"] ?? "");
    $hasId = array_key_exists("id", $message) && $message["id"] !== null;

    if (!$hasId) { // notifications/initialized etc.
        http_response_code(202);
        exit;
    }
    $id = $message["id"];
    $params = $message["params"] ?? [];

    if ($method === "initialize") {
        $requested = (string) ($params["protocolVersion"] ?? "");
        $supported = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"];
        respond_json(200, ["jsonrpc" => "2.0", "id" => $id, "result" => [
            "protocolVersion" => in_array($requested, $supported, true) ? $requested : "2025-06-18",
            "capabilities" => ["tools" => ["listChanged" => false]],
            "serverInfo" => ["name" => "nano-airbridge-relay", "version" => RELAY_VERSION],
            "instructions" => "Relay to a local nano-airbridge runtime. Tools appear after the local "
                . "agent connects. Writes and commands are governed by the local operator's grants; "
                . "preserve execution_id/change_id values and read the original record after any "
                . "timeout — identical retries observe, never replays.",
        ]]);
    }
    if ($method === "ping") {
        respond_json(200, ["jsonrpc" => "2.0", "id" => $id, "result" => []]);
    }
    if ($method === "tools/list") {
        $meta = json_decode((string) @file_get_contents(data_dir("meta", $session) . "/agent.json"), true);
        respond_json(200, ["jsonrpc" => "2.0", "id" => $id, "result" => [
            "tools" => is_array($meta) ? ($meta["tools"] ?? []) : [],
        ]]);
    }

    // Everything else goes to the local agent through the queue.
    housekeep($session);
    $queued = enqueue($method, $params, $id, $session);
    if (!isset($queued["internal_id"])) { // pre-queued bounded failure envelope
        respond_json(200, $queued);
    }

    $responseFile = data_dir("responses", $session) . "/" . $queued["internal_id"] . ".json";
    $inflightFile = data_dir("inflight", $session) . "/" . $queued["internal_id"] . ".json";
    $deadline = time() + (int) $config["mcp_wait"];
    while (time() < $deadline) {
        clearstatcache();
        if (is_file($responseFile)) {
            $response = json_decode((string) @file_get_contents($responseFile), true);
            @unlink($responseFile);
            @unlink($inflightFile);
            if (is_array($response)) {
                $response["id"] = $id; // restore the caller's original JSON-RPC id
                respond_json(200, $response);
            }
            break;
        }
        usleep(250_000);
    }
    clearstatcache();
    $effect = is_file($inflightFile) ? "effect_unknown" : "known_no_effect";
    $message = is_file($inflightFile)
        ? "The local agent accepted this request but has not answered within the relay window. "
          . "Read the original task/operation record before any retry; execution may have occurred."
        : "No local agent is connected to this relay. Nothing was dispatched to the machine.";
    respond_json(200, rpc_tool_failure($id, is_file($inflightFile) ? "agent_timeout" : "agent_offline",
        $message, $effect));
}

// ---------------------------------------------------------------------------
// Agent endpoints (local side, outbound-only)
// ---------------------------------------------------------------------------

function handle_register(): void
{
    require_agent_token();
    $body = read_body();
    $tools = $body["tools"] ?? [];
    if (!is_array($tools)) {
        respond_json(400, ["error" => "invalid_tools"]);
    }
    $session = request_session();
    if ($session === "" && isset($body["session"])) {
        $session = clean_session((string) $body["session"]);
    }
    $meta = [
        "tools" => $tools,
        "server" => $body["server"] ?? null,
        "registered_at" => time(),
        "agent_version" => (string) ($body["agent_version"] ?? ""),
        "session" => $session,
    ];
    @file_put_contents(data_dir("meta", $session) . "/agent.json", json_encode($meta), LOCK_EX);
    respond_json(200, ["ok" => true, "tool_count" => count($tools), "session" => $session]);
}

function handle_poll(): void
{
    require_agent_token();
    $session = request_session();
    $wait = (int) ($_GET["wait"] ?? relay_config()["poll_wait"]);
    $wait = max(1, min(25, $wait));
    $deadline = time() + $wait;
    do {
        housekeep($session);
        $claimed = claim_pending(4, $session);
        if ($claimed !== []) {
            @file_put_contents(data_dir("meta", $session) . "/alive", (string) time());
            respond_json(200, ["requests" => $claimed]);
        }
        @file_put_contents(data_dir("meta", $session) . "/alive", (string) time()); // presence beacon
        usleep(500_000);
    } while (time() < $deadline);
    respond_json(200, ["requests" => []]);
}

function handle_response(): void
{
    require_agent_token();
    $session = request_session();
    $body = read_body();
    $id = (string) ($body["id"] ?? "");
    if ($id === "" || !preg_match('/^r[0-9a-f]{22}$/', $id)) {
        respond_json(400, ["error" => "invalid_internal_id"]);
    }
    if (!isset($body["result"]) && !isset($body["error"])) {
        respond_json(400, ["error" => "invalid_response"]);
    }
    $stored = ["jsonrpc" => "2.0", "id" => $id];
    if (array_key_exists("result", $body)) {
        $stored["result"] = $body["result"];
    } else {
        $stored["error"] = $body["error"];
    }
    @file_put_contents(data_dir("responses", $session) . "/" . $id . ".json", json_encode($stored), LOCK_EX);
    @unlink(data_dir("inflight", $session) . "/" . $id . ".json");
    respond_json(200, ["ok" => true]);
}

function handle_healthz(): void
{
    $session = request_session();
    if ($session !== "") {
        $alive = @file_get_contents(data_dir("meta", $session) . "/alive");
        $meta = json_decode((string) @file_get_contents(data_dir("meta", $session) . "/agent.json"), true);
        respond_json(200, [
            "relay" => "nano-airbridge-relay", "version" => RELAY_VERSION,
            "session" => $session,
            "agent_registered" => is_array($meta),
            "agent_last_seen" => $alive ? (int) $alive : null,
            "agent_online" => $alive !== false && (time() - (int) $alive) < 60,
        ]);
    }

    $alive = @file_get_contents(data_dir("meta") . "/alive");
    $meta = json_decode((string) @file_get_contents(data_dir("meta") . "/agent.json"), true);
    $isOnline = $alive !== false && (time() - (int) $alive) < 60;
    $isRegistered = is_array($meta);
    $lastSeen = $alive ? (int) $alive : null;

    $sessionDirs = (array) glob(__DIR__ . "/data/sessions/*");
    foreach ($sessionDirs as $sDir) {
        $sAlive = @file_get_contents($sDir . "/meta/alive");
        $sMeta = @file_get_contents($sDir . "/meta/agent.json");
        if ($sAlive && (time() - (int) $sAlive) < 60) {
            $isOnline = true;
            if ($lastSeen === null || (int) $sAlive > $lastSeen) {
                $lastSeen = (int) $sAlive;
            }
        }
        if ($sMeta) {
            $isRegistered = true;
        }
    }

    respond_json(200, [
        "relay" => "nano-airbridge-relay", "version" => RELAY_VERSION,
        "session" => "any",
        "agent_registered" => $isRegistered,
        "agent_last_seen" => $lastSeen,
        "agent_online" => $isOnline,
    ]);
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

if (($_SERVER["REQUEST_METHOD"] ?? "") === "GET" && request_op() === "") {
    respond_json(200, ["service" => "nano-airbridge-relay", "version" => RELAY_VERSION,
        "endpoints" => ["mcp", "register", "poll", "response", "healthz"]]);
}

switch (request_op()) {
    case "mcp":
        if (($_SERVER["REQUEST_METHOD"] ?? "") !== "POST") {
            respond_json(405, ["error" => "post_only"]);
        }
        handle_mcp(request_secret());
    case "register":
        if (($_SERVER["REQUEST_METHOD"] ?? "") !== "POST") {
            respond_json(405, ["error" => "post_only"]);
        }
        handle_register();
    case "poll":
        if (($_SERVER["REQUEST_METHOD"] ?? "") !== "GET") {
            respond_json(405, ["error" => "get_only"]);
        }
        handle_poll();
    case "response":
        if (($_SERVER["REQUEST_METHOD"] ?? "") !== "POST") {
            respond_json(405, ["error" => "post_only"]);
        }
        handle_response();
    case "healthz":
        handle_healthz();
    default:
        respond_json(404, ["error" => "unknown_op"]);
}
