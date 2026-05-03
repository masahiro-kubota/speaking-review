# AGENTS.md

## Project Conventions

- Manage Python tooling in this repository with `uv`.
- Prefer `uv run ...` for script execution.
- Prefer `uv add ...` for dependency management instead of editing dependency lists manually.
- Keep proof-of-concept scripts under `poc/`.
- Treat `.env` at the repository root as the default place for local secrets such as `OPENAI_API_KEY`.
- If `uv` cache permissions are restricted in a sandboxed environment, use `UV_CACHE_DIR=/tmp/uv-cache`.
