# hydra desktop

scope : sovereign desktop application and local web desk server for hydra.

subsystem : fastapi application, websocket duplex streaming, spa interface, and computer use controller.

dialect : progen syntax.


## architecture

server engine : asynchronous fastapi application running on uvicorn.

default port : 7778 for desktop interface; preserves port 7777 for openai compatible gateway.

web desk ui : self-contained single-page html5 application with zero external cdn dependencies.

multimodal workspace : real-time streaming chat, autonomous agent react loops, three-headed swarm consensus, and terminal sandbox.

computer use visualizer : live screen preview, coordinate tracking, click dispatch, and active window monitoring.


## launch interface

cli command : `hydra desktop` or `python -m hydra_cli.desktop`.

programmatic runner : `from desktop import run_desktop_app; server = run_desktop_app(port=7778)`.

browser launch : opens designated sovereign web desk window automatically upon startup unless --no-browser supplied.
