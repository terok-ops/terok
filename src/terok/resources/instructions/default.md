# terok Container Environment

You are running inside an isolated Podman container managed by terok.

## Workspace

- `/workspace/` — Git repository checkout (your working directory)
- `/home/dev/` — Your home directory (uid 1000)
- The container IS the security boundary — you have full filesystem access within it.

## Privileges

- You can use `sudo` without a password to install packages, modify system config, etc.
- Use `sudo apt install <package>` for any tools you need.
- The environment is disposable — feel free to install, configure, and experiment.

## Pre-installed tools

git, gh (GitHub CLI), glab (GitLab CLI), rg (ripgrep), fd-find, jq, yq, ast-grep (structural code search/rewrite using AST patterns; `ast-grep run -p 'PATTERN' -l LANG` to search).

Python 3 and Node.js are available. Use `sudo apt install` or `pip`/`npm` for anything else.

## Git workflow

- Git identity is pre-configured:
  - Author (you): AI agent name and email (set automatically)
  - Committer: human operator's name and email
- The repo was cloned from a gated or upstream source.
- Always work on a branch; commit with clear, descriptive messages.

## Conventions

- Read existing project documentation (README, CLAUDE.md, AGENTS.md, etc.) first.
- Make focused, minimal changes; avoid unrelated modifications.
- Run the project's test suite after making changes when possible.

## Classifying internal files by project

Your configuration and data directories (e.g. memory files, session notes, scratch
data) may be shared across multiple projects. To keep things organized, derive a
short, recognizable project identifier from the repository or codebase you are
working on and use it as a prefix or subdirectory for any files you generate
outside the workspace. This provides soft separation — you can still access
cross-project data while keeping project-specific files easy to find.

## Work Status Reporting

Report your current work phase by writing to `/home/dev/.terok/work-status.yml`:

    status: coding
    message: "Brief description of current work"

Valid statuses: planning, coding, testing, debugging, reviewing, documenting, done, blocked, error.
Update this file when transitioning between work phases. Set `done` with a summary when finished.
