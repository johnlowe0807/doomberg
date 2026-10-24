# Agent instructions (Codex / any coding agent)
1. The product spec is `docs/CLAUDE.md`. Follow its "Execution rules" and "Build phases".
2. Current state, architecture contracts, and the next-step checklist are in `docs/HANDOFF.md`. Read it before writing code.
3. After each phase: the app must still start (`.venv/bin/python -m freeterm`) and the headless smoke test in `docs/HANDOFF.md` must pass. Then tick the phase in `docs/HANDOFF.md`.
