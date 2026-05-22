# Toolbase Implementation Plan

## Vision

Toolbase is a community-driven platform for scientific agentic tools - making it as easy as possible for researchers to create, publish, share, search, download, and use AI tools for science.

## Project Information

- **Package Name**: toolbase (secured on PyPI)
- **Domain**: tool-base.org
- **GitHub Org**: https://github.com/toolbase
- **Email**: scitoolkit.dev@gmail.com
- **Author**: Alex Roman

### Repositories

1. **tb-website** (https://github.com/toolbase/tb-website.git)
   - Frontend website (Netlify deployment)
   - Browse/search UI, toolkit detail pages, documentation

2. **tb-backend** (planned)
   - API server for toolkit registry
   - Authentication, upload/download endpoints
   - Database management
   - **Hosted on Triton** (Alex's home server - Lenovo Mini PC, Windows 11)

3. **toolbase** (this repo - will move to org)
   - Python package/CLI tool
   - Core functionality for init, publish, install commands
   - Integration with Orchestral AI and MCP

### Infrastructure

- **Frontend**: Netlify (free tier)
- **Backend**: Triton home server (Lenovo Mini PC, Windows 11)
  - Running Python FastAPI application
  - SQLite or PostgreSQL database
  - Local file storage (or mount network storage)
  - Port forwarding for external access
  - Consider using ngrok or Cloudflare Tunnel for HTTPS
- **Domain**: tool-base.org (DNS pointing to backend)

## Architecture Overview

### Three Main Components

```
┌─────────────────────────────────────────────────────────────┐
│                     tool-base.org                           │
│  (Frontend - React/Next.js on Netlify)                      │
│  - Browse toolkits                                           │
│  - Search by domain/keyword                                  │
│  - User accounts & API key generation                        │
│  - Toolkit detail pages (README, metadata, downloads)        │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ REST API
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Backend API Server                        │
│  (Python FastAPI on Triton - Home Server)                   │
│  - /api/upload - Receive toolkit uploads                    │
│  - /api/download - Serve toolkit files                      │
│  - /api/registry - JSON registry index                      │
│  - /api/auth - API key management                           │
│  - PostgreSQL database (users, toolkits, downloads)         │
│  - S3/Cloud Storage for toolkit files                       │
└─────────────────────────────────────────────────────────────┘
                            ▲
                            │ HTTPS
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              toolbase CLI (Python Package)                 │
│  Commands:                                                   │
│  - toolbase init       # Create toolkit template          │
│  - toolbase login      # Authenticate with API key        │
│  - toolbase publish    # Upload toolkit to registry       │
│  - toolbase search     # Search for toolkits              │
│  - toolbase install    # Download & setup toolkit         │
│  - toolbase list       # Show installed toolkits          │
│  - toolbase serve      # Start MCP server for toolkits    │
└─────────────────────────────────────────────────────────────┘
```

### File Storage Structure

```
S3 Bucket (or equivalent):
toolkits/
  aster/
    aster-1.0.0.tar.gz
    aster-1.1.0.tar.gz
  heptapod/
    heptapod-2.0.0.tar.gz
  metadata/
    logos/
      aster.png
      heptapod.png

Local Installation (~/.toolbase/):
  config.json              # User settings, API key
  toolkits/
    aster/
      venv/                # Isolated Python environment
      tools/               # Orchestral tool definitions
      toolkit.yaml         # Metadata
      requirements.txt
    heptapod/
      venv/
      tools/
      toolkit.yaml
      requirements.txt
```

## Phase 1: Foundation (Weeks 1-4)

### 1.1 CLI Tool Core (`toolbase` package)

**Goal**: Enable basic toolkit creation and structure

- [ ] Create CLI entry point using Click or Typer
- [ ] Implement `toolbase init` command
  - Generate toolkit.yaml template
  - Create directory structure (tools/, README.md, requirements.txt)
  - Interactive prompts for metadata
- [ ] Implement `toolbase validate` command
  - Check toolkit.yaml schema
  - Validate tool definitions (Orchestral format)
  - Check for required files
- [ ] Create toolkit.yaml schema definition
- [ ] Write tests for CLI commands

**Deliverable**: Scientists can run `toolbase init` to scaffold a toolkit

### 1.2 Backend API (tb-backend repo)

**Goal**: Accept and serve toolkit uploads

- [ ] Set up FastAPI project structure
- [ ] Configure PostgreSQL database
  - Users table (id, email, api_key_hash, created_at)
  - Toolkits table (id, name, category, version, author_id, description, downloads, created_at)
  - Versions table (id, toolkit_id, version, file_path, published_at)
- [ ] Implement authentication middleware (API key verification)
- [ ] Create API endpoints:
  - POST /api/auth/register (create account)
  - POST /api/auth/apikey (generate API key)
  - POST /api/upload (upload toolkit, auth required)
  - GET /api/download/:name/:version (download toolkit)
  - GET /api/registry (list all toolkits with metadata)
  - GET /api/toolkit/:name (get toolkit details)
- [ ] Set up S3/cloud storage integration
- [ ] Add file upload validation (size limits, format checks)
- [ ] Deploy to Railway or Fly.io
- [ ] Set up CI/CD pipeline

**Deliverable**: API server accepting authenticated uploads

### 1.3 Frontend Website (tb-website repo)

**Goal**: Basic browsing and account management

- [ ] Set up Next.js or React project
- [ ] Design landing page
  - Hero section explaining Toolbase
  - Featured toolkits showcase
  - Quick start guide
- [ ] Implement toolkit browse/search page
  - Grid/list view of toolkits
  - Filter by category (astro, hep, quantum, etc.)
  - Search by keyword
- [ ] Create toolkit detail page
  - Display README (rendered markdown)
  - Show metadata (author, version, downloads, category)
  - Display logo/screenshots
  - Installation instructions
  - Dependencies list
- [ ] Build user account pages
  - Sign up / login
  - Profile page
  - API key generation and management
  - "My Toolkits" dashboard
- [ ] Deploy to Netlify
- [ ] Connect to backend API

**Deliverable**: Live website at tool-base.org

## Phase 2: Publishing Workflow (Weeks 5-8)

### 2.1 CLI Publishing

- [ ] Implement `toolbase login` command
  - Store API key securely in ~/.toolbase/config.json
  - Verify key with backend
- [ ] Implement `toolbase publish` command
  - Run validation checks
  - Package toolkit into tar.gz
  - Upload to backend API
  - Handle versioning (auto-increment or manual)
- [ ] Add progress bars for uploads
- [ ] Handle errors gracefully (network issues, auth failures, etc.)
- [ ] Write end-to-end publishing tests

### 2.2 Manual Review System

- [ ] Create admin panel on website
- [ ] Build toolkit review queue
  - Pending submissions list
  - Code viewer
  - Approve/reject actions
- [ ] Implement email notifications
  - Submission received
  - Approved/rejected status
- [ ] Document review guidelines
  - Security checks
  - Code quality standards
  - Orchestral tool format compliance

**Deliverable**: Complete publish workflow from CLI to website

## Phase 3: Installation & Usage (Weeks 9-14)

### 3A: Multi-Tier Execution (Venv + Conda) - CURRENT PHASE

#### 3A.1 Multi-Tier Execution Architecture

**Three execution modes with auto-detection:**

1. **Venv Mode** - Isolated virtualenv per toolkit (default, fast)
   - Pure Python toolkits
   - Each toolkit: ~/.toolbase/toolkits/{name}/.venv/
   - Subprocess execution

2. **Conda Mode** - Conda environment per toolkit (different Python versions)
   - Scientific computing with specific Python versions
   - Conda env: toolbase-{name}
   - Handles binary dependencies

3. **Docker Mode** - Containers (Phase 3B, system dependencies)
   - Legacy code, system libraries, CUDA, etc.
   - Planned for Phase 3B

**See [PLATFORM_DECISIONS.md](../PLATFORM_DECISIONS.md#8-execution-architecture---multi-tier-system-approved---phase-3) for full architectural details.**

#### 3A.2 CLI Installation Commands

- [ ] Implement `toolbase search <query>` command
  - Search registry by name/description/category
  - Display results in terminal (rich formatting)
- [ ] Implement `toolbase install <name>` command
  - Download toolkit from registry
  - Auto-detect execution mode (venv/conda/docker)
  - Create isolated environment (~/.toolbase/toolkits/<name>/)
  - Install dependencies from requirements.txt
  - Extract toolkit files
  - Register toolkit locally
  - Optional: --force-docker, --force-venv flags
- [ ] Implement `toolbase list` command
  - Show installed toolkits
  - Display execution mode, versions, paths
- [ ] Implement `toolbase uninstall <name>` command
  - Remove toolkit and cleanup environment
- [ ] Implement `toolbase update <name>` command
  - Check for newer versions
  - Update toolkit

#### 3A.3 MCP Server Implementation

- [ ] Implement `toolbase serve` command
  - Start STDIO MCP server
  - Discover all installed toolkits
  - Route tool calls to appropriate execution environment
  - Subprocess execution for venv/conda tools
  - Container execution for docker tools (Phase 3B)
- [ ] Textual TUI for toolkit management
  - Interactive list of installed toolkits
  - Toggle toolkit on/off during serve
  - View toolkit status, execution mode
- [ ] Create helper for Claude Code integration
  - Auto-configure Claude Code MCP settings
- [ ] Document MCP usage for other agent frameworks

**Deliverable**: End-to-end workflow: install → serve → use with MCP

### 3B: Docker Support (Planned - Following weeks)

- [ ] Create toolbase/python base Docker images
  - 3.11-minimal, 3.11-scipy, 3.11-astro, etc.
  - Push to Docker Hub
- [ ] Implement Docker mode in install/serve
  - Auto-generate Dockerfiles from templates
  - Build and manage Docker images
  - Container lifecycle management
- [ ] Advanced features
  - GPU support in containers
  - Data volume mounting
  - Health checks

### 3C Integration with Orchestral AI (Ongoing)

- [x] Create toolkit loader module
  ```python
  from toolbase import load_toolkit
  aster = load_toolkit('aster')
  tools = aster.get_tools()
  ```
- [x] Implement tool discovery from installed toolkits
- [x] Support Orchestral tool format (@define_tool decorator)
- [ ] Test with existing ASTER and HEPTAPOD toolkits
- [ ] Verify MCP server tool routing

## Phase 4: Polish & Scale (Weeks 13-16)

### 4.1 Developer Experience

- [ ] Create comprehensive documentation
  - Getting started guide
  - Toolkit creation tutorial
  - API reference
  - Best practices
- [ ] Build example toolkits
  - Simple "hello world" toolkit
  - More complex examples for each category
- [ ] Create toolkit template repository on GitHub
- [ ] Add CLI auto-update checker
- [ ] Improve error messages and help text

### 4.2 Community Features

- [ ] Add toolkit ratings/reviews on website
- [ ] Implement toolkit usage analytics
  - Download counts
  - Active users (opt-in)
- [ ] Create changelog/release notes system
- [ ] Add toolkit dependencies/recommendations
  - "Tools that work well together"
- [ ] Build discussion/comment system (or link to GitHub Discussions)

### 4.3 Advanced Features

- [ ] Support for private toolkits (teams/organizations)
- [ ] Toolkit collections/bundles
- [ ] Automated testing for submitted toolkits (CI/CD)
- [ ] Sandboxed execution environment (Docker/containers)
- [ ] Web-based toolkit editor (stretch goal)
- [ ] VSCode extension for toolkit management (future)

## Phase 5: Launch & Growth (Week 17+)

### 5.1 Seed Content

- [ ] Migrate ASTER to toolbase format
- [ ] Migrate HEPTAPOD to toolbase format
- [ ] Migrate quantum toolkit to toolbase format
- [ ] Migrate neutrino toolkit to toolbase format
- [ ] Publish all 4 toolkits to registry

### 5.2 Marketing & Outreach

- [ ] Write launch blog post
- [ ] Post on relevant communities:
  - arXiv (relevant categories)
  - Academic Twitter/Mastodon
  - Physics/astronomy forums
  - AI/ML communities (HuggingFace, etc.)
- [ ] Create demo videos
- [ ] Reach out to research groups directly
- [ ] Present at conferences (if applicable)

### 5.3 Monitoring & Iteration

- [ ] Set up monitoring (uptime, errors, performance)
- [ ] Collect user feedback
- [ ] Track key metrics:
  - Number of toolkits
  - Number of users
  - Download counts
  - Active usage
- [ ] Regular updates based on feedback
- [ ] Build roadmap for future features

## Success Metrics

**Short-term (3 months):**
- 5+ toolkits published (including seed content)
- 20+ registered users
- 100+ toolkit downloads
- Website live and functional
- Full publish/install workflow working

**Medium-term (6 months):**
- 15+ toolkits across multiple categories
- 100+ registered users
- 1000+ toolkit downloads
- Active community engagement (issues, discussions)
- VSCode extension beta

**Long-term (12 months):**
- 50+ toolkits
- 500+ registered users
- Established as go-to platform for scientific AI tools
- Self-sustaining community contributions
- Potential partnerships with research institutions

## Technical Decisions

### Why Not Just Use PyPI?

1. **Curation**: We need manual review for quality/security
2. **Metadata**: Scientific tools need domain-specific metadata (category, research area, etc.)
3. **Isolation**: Need to manage conflicting dependencies across toolkits
4. **Discovery**: Domain-specific search and categorization
5. **UX**: Scientists shouldn't need to learn Python packaging

### Technology Choices

**Frontend**: Next.js/React
- Modern, well-supported
- Great for static/hybrid sites
- Netlify deployment is free

**Backend**: Python FastAPI
- Fast, modern Python web framework
- Type hints & validation built-in
- Great docs, async support
- Native Python matches our audience

**Database**: SQLite or PostgreSQL
- SQLite: Simple, serverless, perfect for getting started
- PostgreSQL: Can upgrade later if needed
- Running on Triton home server

**Storage**: Local filesystem on Triton
- Simple directory structure for toolkit files
- Can add S3/cloud storage later if needed
- Direct file serving via FastAPI

**CLI**: Click (CLI framework) + Textual (TUI)
- Click: Standard Python CLI framework
  - Rich terminal output support
  - Easy to test
- Textual: Terminal User Interface for interactive features
  - Interactive toolkit management in serve command
  - Toggle toolkits on/off during serve
  - View status and logs

## Risks & Mitigations

**Risk**: Low adoption
- **Mitigation**: Seed with quality toolkits, active marketing, make it genuinely easier than alternatives

**Risk**: Security vulnerabilities in submitted code
- **Mitigation**: Manual review initially, sandboxing later, clear security guidelines

**Risk**: Infrastructure costs grow too fast
- **Mitigation**: Using home server (Triton) keeps costs near zero initially, can migrate to cloud later if needed

**Risk**: Maintenance burden
- **Mitigation**: Build automated systems (CI/CD, testing), keep scope focused initially

**Risk**: Another platform does this better
- **Mitigation**: Focus on scientific domain expertise, tight Orchestral integration, community

## Open Questions

1. **Versioning strategy**: Semantic versioning enforced? Allow version deletion?
2. **Toolkit dependencies**: Can toolkits depend on other toolkits?
3. **Pricing model**: Forever free, or freemium model eventually?
4. **Governance**: How to handle disputes, moderation, etc.?
5. **Legal**: Terms of service, code licenses, liability?

## Next Immediate Steps

1. Set up GitHub repositories structure
2. Initialize backend API project
3. Initialize frontend project
4. Create detailed architecture diagrams
5. Start Phase 1 implementation
