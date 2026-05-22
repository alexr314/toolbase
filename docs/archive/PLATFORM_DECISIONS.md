# Toolbase Platform Architecture Decisions

This document outlines key architectural decisions and open questions for the Toolbase platform.

## 1. User Onboarding & Profile Management

### Current State
- Basic Firebase authentication (email/password + Google OAuth)
- Minimal user information captured (email only)
- No terms/privacy policy
- No institutional affiliation tracking

### Proposed Improvements

#### Profile Fields
**Required on first login:**
- Name (auto-populated from Google if available, editable)
- Institution/Affiliation
- Accept Terms & Conditions checkbox
- Accept Privacy Policy checkbox

**Optional fields:**
- Personal website
- ORCID iD (common in scientific publishing)
- GitHub username
- Research interests/domains

#### Implementation Notes
- Create a "profile completion" flow that triggers after first successful authentication
- Store additional profile data in Firestore (already have Firebase setup)
- Display institution on published toolkits (builds credibility)
- Link to personal website from author profiles

#### Legal Considerations
**Privacy Policy & Terms:**
- Privacy policy should cover: data collection, usage, storage, third-party services (Firebase, any analytics)
- Terms should cover: acceptable use, content policies, liability limitations, intellectual property
- Standard boilerplate + specific clauses about:
  - User-uploaded code (toolkit authors retain ownership)
  - Platform's right to review and reject toolkits
  - No warranty on toolkit functionality
  - Users responsible for their own code

**Age requirement:**
- Not immediately necessary - most scientific software platforms don't require this
- Can revisit if we add payment features or other regulated functionality

---

## 2. Toolkit Publishing Workflows

### Current State
- CLI-only publishing via `toolbase publish`
- No web-based publishing option
- No toolkit management interface

### Proposed Features

#### A. Web-Based Publishing Form
**Pros:**
- More accessible to researchers less comfortable with CLI
- Easier for metadata-only updates
- Can provide better validation UI (dropdowns, file size indicators, etc.)
- Lower barrier to entry

**Cons:**
- More complex to implement (file uploads, form validation, etc.)
- Need to handle large files (toolkit packages could be substantial)
- Duplicate functionality with CLI

**Recommendation:** Implement as Phase 2 feature. CLI-first is fine for early adopters (they're the technical users who will write tools anyway). Add web UI once we have ~10-20 active toolkit authors and can validate the use case.

#### B. "My Toolkits" Management Interface
**Essential features:**
- List all published toolkits by current user
- View toolkit details (downloads, versions, status)
- Edit metadata (description, keywords, homepage URL)
- Deprecate/unpublish toolkit
- View review status for pending toolkits

**Nice-to-have:**
- Analytics (downloads over time, geographic distribution)
- User feedback/issues from toolkit users
- Integration testing status

---

## 3. Toolkit Versioning & Organization

### Challenge
Toolkit developers often create multiple iterations:
- Different versions of the same tool (v1, v2, v3)
- Variations for testing (tool_fast, tool_accurate)
- Tool groups/suites (all-in-one vs modular)

### Proposed Structure

#### Option A: Version-First (Like npm/PyPI)
```
toolkit-name@1.0.0
toolkit-name@1.1.0
toolkit-name@2.0.0
```

**Toolkit structure:**
- One toolkit = one package name
- Semantic versioning (major.minor.patch)
- Users can specify version constraints
- Platform shows "latest" by default but allows browsing versions

**Tool variants within a toolkit:**
```yaml
# In toolkit.yaml
name: exoplanet-tools
version: 2.0.0
tools:
  - name: transit_model_fast
    description: "Fast but approximate transit model"
  - name: transit_model_accurate
    description: "Slower but high-precision transit model"
```

#### Option B: Namespace-First (Like Docker Hub)
```
author/toolkit-name:version
author/toolkit-name:variant
```

#### Option C: Tool Groups
```yaml
name: exoplanet-tools
version: 2.0.0
tool_groups:
  - name: transit_modeling
    tools:
      - transit_model_fast
      - transit_model_accurate
  - name: radial_velocity
    tools:
      - rv_fit
      - rv_periodogram
```

### Recommendation: **Option A + Option C Hybrid**

**Rationale:**
1. Toolkit-level versioning (like npm) is familiar and works well
2. Tool groups within toolkit allow logical organization
3. Individual tool variants are just different tools in the toolkit
4. Keeps namespace simple (no need for author prefix if names are unique)

**Example:**
```yaml
name: aster-exoplanet
version: 2.1.0
author: Jane Researcher
institution: MIT
description: "Comprehensive exoplanet analysis toolkit"

tool_groups:
  - name: transit
    description: "Transit modeling and fitting"
    tools:
      - transit_model_fast
      - transit_model_accurate
      - transit_fit

  - name: radial_velocity
    description: "RV analysis"
    tools:
      - rv_periodogram
      - rv_fit

tools_dir: ./tools
```

Users install: `toolbase install aster-exoplanet@2.1.0`
Or latest: `toolbase install aster-exoplanet`

---

## 4. Code Review & Security

### Automated Safety Checks

#### Option A: LLM-Based Code Review
**Tools:**
- LlamaGuard (Meta) - content moderation
- GPT-4 with security prompts
- Custom fine-tuned model

**What to check:**
- No obvious malicious code (system calls, network access without disclosure)
- Code matches toolkit description
- No hardcoded secrets/credentials
- Follows Python security best practices

**Pros:**
- Can catch sophisticated issues
- Natural language explanations of problems
- Scales with minimal human intervention

**Cons:**
- False positives (legitimate system access flagged)
- False negatives (novel attack vectors)
- Cost per review (if using commercial LLM)
- Need to handle edge cases (obfuscated code)

#### Option B: Static Analysis Tools
**Tools:**
- Bandit (Python security linter)
- Safety (checks dependencies for known vulnerabilities)
- Semgrep (pattern-based code scanning)

**Pros:**
- Deterministic, reproducible
- Well-tested for known vulnerabilities
- Fast and cheap
- Lower false positive rate

**Cons:**
- Only catches known patterns
- Can't understand semantic intent
- Requires keeping rule sets updated

### Recommendation: **Hybrid Approach**

**Pipeline:**
1. **Automated static analysis** (Bandit + Safety)
   - Must pass to proceed to next stage
   - Checks for known vulnerability patterns
   - Validates dependencies

2. **LLM code review** (GPT-4 or similar)
   - Reviews overall code structure
   - Checks if functionality matches description
   - Flags suspicious patterns for human review
   - Generates summary for reviewers

3. **Human review** (for verified badge)
   - Manual review by platform maintainers
   - Only for toolkits requesting "verified" status
   - Checks LLM flagged issues
   - Tests basic functionality

**Verification Tiers:**
- **Unverified**: Passed automated checks only
- **Verified**: Passed human review by platform team
- **Trusted Author**: From author with 3+ verified toolkits, new toolkits auto-verified

---

## 5. Dependency Isolation: The Critical Question

This is the hardest problem. You're right that this is fundamental to the platform's viability.

### The Problem
**Scenario:** User wants to use both:
- `toolkit-a` requires Python 3.9, numpy 1.20, astropy 4.0
- `toolkit-b` requires Python 3.11, numpy 1.24, astropy 5.0

Traditional Python: **Conflicts, one or both break**

### Solution Analysis

#### Option 1: Docker Containers (Full Isolation)

**How it works:**
- Each toolkit ships as/with a Docker container
- Container includes all dependencies, correct Python version, system libraries
- Toolbase CLI manages containers (starting, stopping, routing MCP calls)
- Tools run in isolated environments, can't conflict

**Pros:**
- **Guaranteed to work** - toolkit author controls entire environment
- Handles ANY dependency (system libs, specific Python versions, even GPU drivers)
- Familiar to many researchers (Docker is standard in computational science)
- Toolkits can include large data files in container volumes
- Security: natural isolation between toolkits

**Cons:**
- **Requires Docker** - users must install and run Docker Desktop
  - Docker Desktop is free for personal/small business use
  - Works on Mac, Windows, Linux
  - ~500MB download + ~2GB disk space
- **Container size** - each toolkit = Docker image (100MB - several GB)
  - Can use base images to share common layers
  - Users must download all images for toolkits they use
- **Startup overhead** - containers take ~1-5 seconds to start
  - Can keep containers running (uses memory)
  - Or start on-demand (adds latency to first call)
- **Complexity for authors** - must create Dockerfile
  - Can provide templates + helper tool
  - Many scientists already use Docker

**Author workflow:**
```bash
# Option A: Author provides Dockerfile
toolbase init --with-docker
# Creates template with Dockerfile

# Option B: Toolbase generates container
toolbase init
# Add tools and requirements.txt
toolbase containerize  # Auto-generates optimized Dockerfile
toolbase publish       # Builds and pushes container
```

**User workflow:**
```bash
pip install toolbase
# One-time: ensure Docker is running
toolbase install aster-exoplanet
# Downloads Docker image, ~500MB

toolbase serve
# Starts MCP server, manages containers
# Containers started on-demand or kept warm
```

#### Option 2: Conda/Mamba Environments

**How it works:**
- Each toolkit specifies conda environment (environment.yml)
- Toolbase manages separate conda envs per toolkit
- Tools run in appropriate env via subprocess

**Pros:**
- No Docker requirement
- Conda common in scientific Python
- Can handle different Python versions (unlike venv)
- Smaller download than Docker images

**Cons:**
- **Still have conflicts** - conda not as isolated as containers
  - System libraries can leak between envs
  - Some packages hard-code paths
- **Slower** - creating conda envs is slow (minutes)
- **Less reliable** - conda solver can fail on complex dependencies
- **No system-level isolation** - can't include non-Python dependencies easily

#### Option 3: Pure Python + Virtual Environments

**How it works:**
- Each toolkit gets a Python venv
- Use pip to install dependencies

**Pros:**
- Simplest for users (no extra tools)
- Fastest startup
- Smallest footprint

**Cons:**
- **Doesn't solve the problem** - all toolkits must use same Python version
- No isolation for system libraries
- Will have conflicts (numpy version wars, etc.)
- Essentially guaranteed to break as toolkit count grows

#### Option 4: WebAssembly/Pyodide (Future)

**How it works:**
- Run Python in WebAssembly sandbox
- Each toolkit in separate WASM instance

**Pros:**
- True isolation
- No Docker required
- Run in browser or native

**Cons:**
- **Not ready yet** - Pyodide is experimental, many packages don't work
- Performance overhead
- Can't access system resources (files, GPU)
- 2-3 years out from being viable

### Recommendation: **Docker Containers (Option 1)**

**Rationale:**

1. **It actually solves the problem** - Options 2 and 3 don't fully isolate dependencies

2. **Target audience is technical** - Scientists who write agentic tools can install Docker
   - Not asking undergrads to install it
   - Asking researchers who are already using Python, GitHub, etc.

3. **One-time setup cost** - Yes, Docker is ~30 minutes to install and test, but:
   - Only need to do it once
   - Pays off immediately with zero dependency conflicts
   - Alternative is hours of debugging version conflicts

4. **Provides other benefits:**
   - Security isolation (malicious toolkit can't access host)
   - Can include binary dependencies (many scientific tools need Fortran compilers, etc.)
   - Works with GPU toolkits (CUDA in container)
   - Enables reproducible science (container is exact environment)

5. **Can optimize the UX:**
   - Auto-detect if Docker is installed
   - Provide detailed setup guide with screenshots
   - Offer "Docker-free mode" (fallback to venv, with warnings about conflicts)
   - Pre-pull common base images to reduce download sizes

**Phased rollout:**

**Phase 1 (MVP):** Docker required, explicit in docs
- "Toolbase requires Docker Desktop to ensure toolkits work correctly"
- Provide installation guide
- Most early adopters won't mind

**Phase 2:** Optimize for Docker users
- Smart image layering (share common dependencies)
- Keep containers warm (reduce startup latency)
- Background downloads (pull images while user browses)

**Phase 3:** Optional Docker-free mode
- Detect if user has Docker
- If not, offer "experimental mode" with venv
- Clear warnings about potential conflicts
- Only works if toolkits declare compatible dependencies

### Container Strategy Details

**Base images:**
```dockerfile
# Toolbase provides blessed base images
FROM toolbase/python:3.11-minimal  # Python 3.11, pip, common sci libs
FROM toolbase/python:3.11-scipy    # + numpy, scipy, pandas
FROM toolbase/python:3.11-astro    # + astropy, astroquery
FROM toolbase/python:3.11-ml       # + torch, transformers
```

**Author specifies:**
```yaml
# toolkit.yaml
base_image: toolbase/python:3.11-astro
dependencies:
  - taurex==3.1.0
  - exotransmit==2.0.0
```

**Toolbase generates:**
```dockerfile
FROM toolbase/python:3.11-astro
WORKDIR /toolkit
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "-m", "toolbase.mcp_server"]
```

**Image size optimization:**
- Base images shared across toolkits (only download once)
- Typical toolkit-specific layer: 50-200MB
- Use multi-stage builds for tools with build dependencies

**Data files:**
- Option A: Include small data (<100MB) in container
- Option B: Download on first use, cache in named volume
- Option C: User provides data directory, mount into container

```yaml
# toolkit.yaml
data:
  - name: stellar_models
    size: 2.5GB
    url: https://example.com/data.tar.gz
    mount: /data/stellar_models
    strategy: download_on_install  # or: download_on_first_use, user_provided
```

---

## 6. MCP Integration Architecture

### How tools run through MCP:

**User workflow:**
```bash
# Terminal 1: Start Toolbase MCP server
toolbase serve
# Starts server on stdio (MCP standard)
# Discovers installed toolkits
# Manages Docker containers

# Terminal 2: Use with Claude Code
claude-code
# In Claude Code settings, add MCP server:
# "toolbase": {"command": "toolbase", "args": ["serve"]}
```

**What happens when Claude calls a tool:**
1. Claude sends MCP request: `{"method": "call_tool", "params": {"name": "aster-exoplanet::transit_model", "arguments": {...}}}`
2. Toolbase MCP server receives request
3. Parses toolkit name: `aster-exoplanet`
4. Checks if container running, starts if needed (~1-3 sec first time)
5. Forwards request into container
6. Container executes tool, returns result
7. Toolbase returns to Claude

**Container management:**
- Keep recently used containers running (configurable, default: 5 min idle timeout)
- User can configure "always-on" for frequently used toolkits
- Health checks to restart crashed containers

---

## 7. Implementation Priorities

### Phase 1 (MVP - Next 2-4 weeks)
1. **Profile completion flow**
   - Additional fields on first login
   - Privacy policy + terms (can be simple for MVP)
   - Store in Firestore

2. **"My Toolkits" dashboard section**
   - List published toolkits
   - Basic metadata display
   - Link to toolkit pages

3. **Toolkit versioning in backend**
   - Update API to handle versions
   - Toolkit detail page shows version history
   - Install specific version: `toolbase install name@version`

4. **Docker-based CLI (proof of concept)**
   - `toolbase init --with-docker` creates Dockerfile
   - `toolbase build` creates local image
   - `toolbase test` runs toolkit locally in container

### Phase 2 (Growth - 1-2 months)
1. **Automated security pipeline**
   - Bandit + Safety checks on publish
   - LLM-based code review
   - Review dashboard for maintainers

2. **Verified badge system**
   - Human review workflow
   - Trusted author auto-verification

3. **Tool groups & organization**
   - UI for browsing tools within toolkit
   - Filter/search by tool capabilities

4. **Container registry**
   - Push/pull toolkit containers
   - Optimize base images
   - Smart caching

### Phase 3 (Polish - 2-3 months)
1. **Web-based publishing**
   - Upload form for toolkit packages
   - Online metadata editor

2. **Advanced analytics**
   - Download trends
   - Usage patterns
   - User feedback

3. **VSCode extension**
   - Browse toolkits from VSCode
   - Install/configure from GUI
   - View toolkit documentation inline

---

## Open Questions for Discussion

1. **Docker requirement: Deal-breaker or acceptable?**
   - Are we okay requiring Docker Desktop for users?
   - Should we offer Docker-free mode even if it's less reliable?

2. **Container authorship:**
   - Should authors write Dockerfiles, or should we auto-generate?
   - How much Docker knowledge should we assume?

3. **Data files:**
   - How should we handle toolkits with large (>1GB) data dependencies?
   - User-provided? Downloaded on install? Separate data registry?

4. **Versioning semantics:**
   - Should toolkit versions be semantic (1.0.0) or date-based (2024.1.15)?
   - How to handle breaking changes to tool interfaces?

5. **Pricing/sustainability:**
   - Currently free. Container hosting costs money.
   - Future: paid hosting for private toolkits?
   - Grant-funded? University-sponsored?

6. **Testing:**
   - Should we run automated tests on published toolkits?
   - Who writes tests - authors or platform?
   - How to test tools that need expensive computation?

---

## Next Steps

1. **Get feedback on Docker decision** - This is the biggest architectural choice
2. **Draft privacy policy & terms** - Can use templates, customize for our use case
3. **Implement profile completion flow** - Relatively straightforward
4. **Prototype Docker-based toolkit** - Build one example end-to-end
5. **Design container registry** - Figure out hosting (Docker Hub? GitHub Registry? Self-hosted?)

---

*Last updated: 2024-01-15*
*Authors: Claude Code + Toolbase Team*
