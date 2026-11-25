# Architecture & Workflow

## Agent <-> MCP State Machine Diagram

```text
+-----------------------------------------------------------------------+
|                         matching_agent.py                             |
|                                                                       |
|  [START]                                                              |
|     |                                                                 |
|     v                                                                 |
|  (Open stdio_client + ClientSession)                                  |
|     |                                                                 |
|     v                                                                 |
|  (JSON-RPC: initialize & resources/list & resources/read catalog)     |
|     |                                                                 |
|     v                                                                 |
|  +--> [agent node: LLM evaluates state & selects MCP tool]            |
|  |       |                                                            |
|  |       +-- (No tool_calls) ----------------------------> [END]      |
|  |       |                                                            |
|  |       +-- (Has tool_calls: watch_directory / batch_process)        |
|  |               |                                                    |
|  |               v                                                    |
|  |       [tools node: ToolNode -> JSON-RPC tools/call]                |
|  |               |                                                    |
|  +---------------+                                                    |
+------------------|----------------------------------------------------+
                   | stdin / stdout (JSON-RPC 2.0)
                   v
+-----------------------------------------------------------------------+
|                     filesystem_mcp_server.py                          |
|                                                                       |
|  [MCP Server Loop]                                                    |
|     |-- Handles initialize & capability discovery                     |
|     |-- Serves resources: resumes://catalog, resumes://file/{name}    |
|     |-- Executes watch_directory() -> delta list of resume files      |
|     |-- Executes batch_process() -> per-file metrics & error objects  |
|     |-- Enforces resolve_safe_path(), extension & size limits         |
+-----------------------------------------------------------------------+