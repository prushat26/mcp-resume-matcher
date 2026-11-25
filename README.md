# MCP Resume Matcher

Model Context Protocol (`mcp`) resume matcher using a `stdio` filesystem server, `pypdf` text extraction, and a LangGraph (`StateGraph`) ranking agent.

## Architecture & Components

* **`filesystem_mcp_server.py`**: `MCPServer` over `stdio` with Pydantic config, `resolve_safe_path`, `pypdf` extraction, resources (`resumes://catalog`, `resumes://file/{filename}`, `resumes://config/summary`), and tools (`watch_directory`, `batch_process`).
* **`matching_agent.py`**: Connects via `stdio`, maps MCP tools to LangChain `StructuredTool`, runs a LangGraph `StateGraph`, and outputs a ranked match report.
* **`test_mcp_workflow.py`**: Tests MCP server operations and LangGraph agent runs (stubbed offline and live with `OPENAI_API_KEY`).
* **`ARCHITECTURE.md`**: Architecture documentation.
* **`resumes/`**: Candidate resume files (`.pdf`, `.txt`, `.md`, `.docx`).

## Setup & Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install mcp langchain-core langchain-openai langgraph pydantic pypdf python-dotenv

```

Create `.env` in the project root (ignored by `.gitignore`):

```env
OPENAI_API_KEY=your-openai-api-key
RESUME_DIR=./resumes
PROCESSED_DIR=./processed_resumes
MAX_BATCH_FILES=25

```

## Usage

### 1. Run End-to-End Matching Agent

```powershell
.\.venv\Scripts\python.exe matching_agent.py

```

### 2. Run Integration & Workflow Tests

```powershell
.\.venv\Scripts\python.exe test_mcp_workflow.py

```

### 3. Inspect MCP Server (Optional)

```powershell
.\.venv\Scripts\mcp.exe dev filesystem_mcp_server.py

```

## Security & Path Handling

* `resolve_safe_path()` blocks path traversal (`../`), rejects path separators, and validates allowed extensions inside `RESUME_DIR`.
* Keep `.env` and `.venv/` out of Git via `.gitignore`.