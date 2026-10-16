"""Checks for the AI model connection, against a fake Ollama server on this computer (no real model needed).
Run from the project root: python3 tests/verify_llm.py"""
import json, logging, socket, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, "src")
import explain, llm, match
import requests

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []
    def emit(self, record):
        self.messages.append(record.getMessage())

capture = Capture()
logging.getLogger("llm").addHandler(capture)

class FakeOllama:
    """A tiny local web server that speaks just enough of Ollama's protocol to test against."""
    def __init__(self):
        self.posts, self.mode = [], "ok"
        self.reply = json.dumps({"verdict": "unsure", "explanation": "Not enough information."})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def _send(self, status, body, content_type="application/json"):
                data = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                if self.path == "/":
                    self._send(200, "Ollama is running", "text/plain")
                elif self.path == "/api/tags":
                    self._send(200, {"models": [{"name": "qwen2.5:3b", "size": 1929912432}]})
                else:
                    self._send(404, {"error": "not found"})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.posts.append({"path": self.path, "body": body})
                if fake.mode == "not_found":
                    self._send(404, {"error": f"model '{body['model']}' not found, try pulling it first"})
                elif fake.mode == "no_message":
                    self._send(200, {"done": True})
                elif fake.mode == "garbage":
                    self._send(200, "<html>not json</html>", "text/html")
                else:
                    if fake.mode == "slow":
                        time.sleep(2)
                    self._send(200, {"message": {"role": "assistant", "content": fake.reply}, "done": True,
                                     "load_duration": 2_000_000_000, "prompt_eval_count": 1200,
                                     "eval_count": 100, "eval_duration": 5_000_000_000})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
    def stop(self):
        self.server.shutdown()
        self.server.server_close()

def closed_port_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"

fake = FakeOllama()
SCHEMA = explain.ModelAnswer.model_json_schema()

# ---- configuration -------------------------------------------------------------
with tempfile.TemporaryDirectory() as d:
    good = Path(d) / "llm.json"
    good.write_text(json.dumps({"model": "qwen2.5:3b", "base_url": fake.url}))
    bad = Path(d) / "bad.json"
    bad.write_text("{not json")
    check("a valid settings file loads", llm.load_llm_config(str(good))["model"] == "qwen2.5:3b")
    capture.messages.clear()
    check("a missing settings file gives None and an error", llm.load_llm_config(str(Path(d) / "nope.json")) is None and any("not found" in m for m in capture.messages))
    capture.messages.clear()
    check("broken JSON in the settings gives None and an error", llm.load_llm_config(str(bad)) is None and any("Invalid JSON" in m for m in capture.messages))
check("the real settings file in the project is valid", (llm.load_llm_config() or {}).get("model") is not None, str(llm.load_llm_config()))

# ---- talking to the server ---------------------------------------------------------
check("a running server is detected", llm.is_server_up(fake.url))
check("a missing server is detected", not llm.is_server_up(closed_port_url(), timeout=1))
check("downloaded models are listed", llm.list_models(fake.url) == ["qwen2.5:3b"])
check("a missing server lists no models", llm.list_models(closed_port_url(), timeout=1) == [])

model = llm.OllamaModel("qwen2.5:3b", fake.url)
reply = model("SYSTEM TEXT", "USER TEXT", SCHEMA)
sent = fake.posts[-1]
check("the reply text is returned", reply == fake.reply)
check("the request goes to /api/chat with the model name", sent["path"] == "/api/chat" and sent["body"]["model"] == "qwen2.5:3b")
check("the system message comes first, then the user message",
      [(m["role"], m["content"]) for m in sent["body"]["messages"]] == [("system", "SYSTEM TEXT"), ("user", "USER TEXT")])
check("our JSON schema is sent as the required reply format", sent["body"]["format"] == SCHEMA)
check("no streaming; temperature 0, fixed seed, 4096 context",
      sent["body"]["stream"] is False and sent["body"]["options"] == {"temperature": 0, "num_ctx": 4096, "seed": 0})
stats = model.stats[-1]
check("timings are recorded: load 2 s, 1200 prompt tokens, 100 output tokens, 20 tokens/s",
      stats["load_seconds"] == 2.0 and stats["prompt_tokens"] == 1200 and stats["output_tokens"] == 100 and stats["tokens_per_second"] == 20.0)
check("settings from the file are used (model, URL, context size)",
      llm.OllamaModel.from_config({"model": "m", "base_url": fake.url + "/", "num_ctx": 2048}).num_ctx == 2048
      and llm.OllamaModel.from_config({"model": "m", "base_url": fake.url + "/"}).base_url == fake.url)

# ---- failures must raise, never return nonsense ----------------------------------------------
def raises(callable_, *fragments):
    try:
        callable_()
    except Exception as error:
        return all(fragment in str(error) for fragment in fragments)
    return False

fake.mode = "not_found"
check("a model that is not downloaded raises with the server's message", raises(lambda: model("s", "u", SCHEMA), "404", "not found", "pulling"))
fake.mode = "no_message"
check("a reply without a message raises", raises(lambda: model("s", "u", SCHEMA), "no message"))
fake.mode = "garbage"
check("a reply that is not JSON raises", raises(lambda: model("s", "u", SCHEMA)))
fake.mode = "slow"
started = time.perf_counter()
check("a call that takes too long times out", raises(lambda: llm.OllamaModel("m", fake.url, timeout=0.5)("s", "u", SCHEMA)) and time.perf_counter() - started < 1.8)
check("a server that is down raises", raises(lambda: llm.OllamaModel("m", closed_port_url(), timeout=2)("s", "u", SCHEMA)))
fake.mode = "ok"

# ---- the whole chain: real note -> prompt -> fake server -> checked finding ------------------------
chunks = json.load(open("data/chunks/pandas_chunks.json"))
note = next(c for c in chunks if c["text"].startswith("Removed deprecated Series.append()"))
usage = {"file": "loader.py", "line": 9, "snippet": "df = df.append(extra)", "function": "append", "keywords": [],
         "resolved": True, "resolution": "tracked_variable", "kind": "call"}
result = match.match_usage(usage, [{"rank": 1, "score": 0.03, "semantic_rank": 1, "keyword_rank": 1, "chunk": note}])
honest = json.dumps({"verdict": "affected", "explanation": "DataFrame.append() was removed.", "suggested_fix": "Use concat() instead.",
                     "citations": [{"note": 1, "quote": "Removed deprecated Series.append() , DataFrame.append()"}]})
liar = json.dumps({"verdict": "affected", "explanation": "It is gone.", "suggested_fix": None,
                   "citations": [{"note": 1, "quote": "append will be removed in version 9"}]})

fake.reply, fake.posts = honest, []
finding = explain.explain_usage(result, llm.OllamaModel("qwen2.5:3b", fake.url), "pandas")
check("through the server, an honest answer becomes an 'affected' finding with a verified citation",
      finding["verdict"] == "affected" and finding["flags"] == [] and len(fake.posts) == 1
      and "Note 1" in fake.posts[0]["body"]["messages"][1]["content"] and note["text"] in fake.posts[0]["body"]["messages"][1]["content"])
fake.reply, fake.posts = liar, []
finding = explain.explain_usage(result, llm.OllamaModel("qwen2.5:3b", fake.url), "pandas")
check("through the server, a lying answer is caught, retried once, then downgraded to 'unsure'",
      finding["verdict"] == "unsure" and finding["flags"] == ["citation_check_failed"] and len(fake.posts) == 2)
fake.mode, fake.posts = "not_found", []
finding = explain.explain_usage(result, llm.OllamaModel("qwen2.5:3b", fake.url), "pandas")
check("through the server, a missing model gives an 'unsure' finding flagged model_error, not a crash",
      finding["verdict"] == "unsure" and finding["flags"] == ["model_error"] and len(fake.posts) == 2)

fake.stop()
print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
