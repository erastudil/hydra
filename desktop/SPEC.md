# desktop specification

scope : formal specification for the hydra desktop application server and web desk contract.

version : 1.0.0.

dialect : progen instruct.


## http routes

get root : path `/`; returns text/html content for single-page web desk interface.

get status : path `/api/status`; returns json with version, uptime, gateway url, and computer use state.

get models : path `/api/models`; returns json list of catalog models and aliases.

post chat : path `/api/chat`; accepts json payload with model, prompt, and execution mode; returns completion or stream.

post terminal : path `/api/terminal/run`; executes shell command in zero-trust sandbox; returns stdout, stderr, and exit code.

post computer action : path `/api/computer/action`; executes mouse, keyboard, or window command via computer use engine.

get computer screen : path `/api/computer/screen`; returns image/png binary bytes of desktop screen or json with base64.

post browser action : path `/api/browser/action`; executes playwright browser action and returns structured result.


## websocket protocol

endpoint : path `/ws/desktop`.

client payload : json message declaring action `ping`, `chat`, `terminal`, or `screen`.

server events : emitted json objects declaring event `pong`, `token`, `tool_call`, `screen`, `done`, or `error`.


## lifecycle and isolation

thread safety : thread-safe execution of uvicorn server in background daemon thread.

clean shutdown : `server.stop()` sets `server.should_exit = True` and joins thread with bounded timeout.
