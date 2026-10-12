#!/usr/bin/env python3
"""QA stub OpenAI-compatible provider. Scenario chosen by keyword in last user msg:
SLOW (60 ticks@150ms), HANG (never responds), MALFORMED (garbage SSE), CUT (stream then close w/o DONE), E500, BIG (4000-line markdown stream), WRITE (write_file tool call), default alpha beta gamma. Port arg."""
import json,sys,time,threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*a): pass
    def do_GET(self):
        b=json.dumps({"data":[{"id":"local"}]}).encode()
        self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
    def do_POST(self):
        n=int(self.headers.get("Content-Length",0));body=json.loads(self.rfile.read(n) or b"{}")
        msgs=body.get("messages",[]);p=next((m["content"] for m in reversed(msgs) if m["role"]=="user"),"") or ""
        if not isinstance(p,str):p=str(p)
        has_tool=any(m["role"]=="tool" for m in msgs)
        def sse(v):
            self.wfile.write(("data: %s\n\n"%json.dumps(v)).encode());self.wfile.flush()
        if "E500" in p:
            b=b'{"error":"boom"}';self.send_response(500);self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b);return
        if "HANG" in p:
            time.sleep(3600);return
        self.send_response(200);self.send_header("Content-Type","text/event-stream");self.send_header("Connection","close");self.end_headers()
        try:
            if "MALFORMED" in p:
                self.wfile.write(b"data: {not json\n\ndata: [1,2\n\n");self.wfile.write(b"data: {\"choices\":[{\"delta\":{\"content\":\"after-garbage\"}}]}\n\n");self.wfile.flush();return
            if "WRITE" in p and not has_tool:
                sse({"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"write_file","arguments":""}}]}}]})
                sse({"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":json.dumps({"path":"hello.txt","content":"hi\n"})}}]}}]});sse({"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":3}});self.wfile.write(b"data: [DONE]\n\n");return
            if "BIGDIFF" in p and not has_tool:
                c="".join("line %d\n"%i for i in range(5000))
                sse({"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"write_file","arguments":""}}]}}]})
                sse({"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":json.dumps({"path":"big.txt","content":c})}}]}}]});sse({"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":3}});self.wfile.write(b"data: [DONE]\n\n");return
            if "BIG" in p:
                for i in range(4000):
                    sse({"choices":[{"delta":{"content":"row %d lorem ipsum dolor sit amet\n"%i}}]});time.sleep(0.002)
            elif "SLOW" in p:
                for i in range(60): sse({"choices":[{"delta":{"content":"tick%d "%i}}]});time.sleep(.15)
            elif "CUT" in p:
                sse({"choices":[{"delta":{"content":"partial "}}]});time.sleep(.2);return
            elif has_tool: sse({"choices":[{"delta":{"content":"finished"}}]})
            else:
                for t in ["alpha ","beta ","gamma"]: sse({"choices":[{"delta":{"content":t}}]});time.sleep(.02)
            sse({"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":3}});self.wfile.write(b"data: [DONE]\n\n")
        except Exception: pass
ThreadingHTTPServer.daemon_threads=True
ThreadingHTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()
