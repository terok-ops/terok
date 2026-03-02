# terok

A tool for managing containerized AI coding agent projects using Podman. Provides both a CLI (`terokctl`) and a Textual TUI (`terok`).

## Features

- **Project Management**: Define and manage containerized AI agent projects
- **Task Lifecycle**: Create, run, and manage tasks with automatic workspace setup
- **Multiple Agents**: Support for Codex, Claude Code, Mistral Vibe, and Blablador
- **Security Modes**: Online and gatekeeping modes for different trust levels
- **Container Layers**: Efficient three-layer Docker image architecture (L0/L1/L2)
- **SSH Integration**: Automatic SSH key management for private Git repositories

## Quick Start

### Prerequisites

- Podman installed and configured
- Python 3.9+
- OpenSSH client (for private git repos)

### Installation

```bash
# Clone and install
git clone git@github.com:terok-ops/terok.git
cd terok
pip install .

# With TUI support
pip install '.[tui]'
```

### Basic Workflow

```bash
# 1. Create project directory
mkdir -p ~/.config/terok/projects/myproj

# 2. Create project.yml
cat > ~/.config/terok/projects/myproj/project.yml << 'EOF'
project:
  id: myproj
  security_class: online
git:
  upstream_url: https://github.com/yourorg/yourrepo.git
  default_branch: main
EOF

# 3. Generate and build images
terokctl generate myproj
terokctl build myproj

# 4. Create and run a task
terokctl task new myproj
terokctl task run-cli myproj 1
```

## Documentation

- [User Guide](USAGE.md) - Complete user documentation
- [Developer Guide](DEVELOPER.md) - Architecture and contributing
- [API Reference](reference/) - Auto-generated API documentation

## License

See [LICENSE](https://github.com/terok-ops/terok/blob/master/LICENSE) file.
