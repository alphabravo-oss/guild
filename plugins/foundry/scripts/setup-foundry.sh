#!/bin/bash

# Foundry Setup Script
# Parses arguments and initializes the foundry run state

set -euo pipefail

# Parse arguments
SCOPE=""
SPEC_PATH=""
URL=""
TEMPER=false
MAX_CYCLES=0
NO_UI=false
OUTPUT_DIR=""
TICKET=""
DESCRIPTION=""
SKIP_START_BACKEND=false

# Handle subcommands first
case "${1:-}" in
  resume|status|stop)
    echo "FOUNDRY_SUBCOMMAND=$1"
    exit 0
    ;;
esac

while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      cat << 'HELP_EOF'
Foundry — Build-Verify-Fix Loop

Forge plans. Foundry builds.

USAGE:
  /foundry:start <SCOPE> [OPTIONS]
  /foundry:resume
  /foundry:status
  /foundry:stop

ARGUMENTS:
  SCOPE             Description of what to build (required)

OPTIONS:
  --spec <path>            Spec file for spec-aware decomposition
  --url <url>              Browser audit URL for SIGHT verification
  --output-dir <dir>       Output directory (default: auto-generated)
  --temper                 Enable micro-domain stress testing (F5)
  --max-cycles <n>         Cap verify-fix cycles (default: unlimited)
  --no-ui                  Skip browser audit (SIGHT)
  --ticket <id>            Ticket ID for commit messages
  --desc <text>            Run description
  --skip-start-backend     Don't auto-start dev servers

PHASES:
  F0: DECOMPOSE  — Break spec into castings with observable truths
  F1: CAST       — Build castings with parallel teams
  F2: INSPECT    — 4-stream verification (TRACE + PROVE + SIGHT + TEST)
  F3: GRIND      — Fix defects, loop back to INSPECT
  F4: ASSAY      — Final spec-before-code verification (4 parallel agents)
  F5: TEMPER     — Micro-domain stress testing (optional)
  F6: DONE       — Report and archive

EXAMPLES:
  /foundry:start "user authentication" --spec docs/specs/auth.md
  /foundry:start "dashboard redesign" --spec docs/specs/dashboard.md --url http://localhost:3000
  /foundry:start "api improvements" --spec docs/specs/api.md --temper
  /foundry:start "quick fix" --no-ui --max-cycles 2

WORKFLOW:
  1. Forge plans:    /forge:plan "my feature"
  2. Foundry builds: /foundry:start "my feature" --spec docs/specs/my-feature.md

  Forge plans. Foundry builds.
HELP_EOF
      exit 0
      ;;
    --spec)
      SPEC_PATH="$2"
      shift 2
      ;;
    --url)
      URL="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --temper)
      TEMPER=true
      shift
      ;;
    --max-cycles)
      MAX_CYCLES="$2"
      shift 2
      ;;
    --no-ui|--headless)
      NO_UI=true
      shift
      ;;
    --ticket)
      TICKET="$2"
      shift 2
      ;;
    --desc)
      DESCRIPTION="$2"
      shift 2
      ;;
    --skip-start-backend)
      SKIP_START_BACKEND=true
      shift
      ;;
    *)
      if [[ -z "$SCOPE" ]]; then
        SCOPE="$1"
      else
        SCOPE="$SCOPE $1"
      fi
      shift
      ;;
  esac
done

# Validate
if [[ -z "$SCOPE" ]]; then
  echo "Error: Scope description is required" >&2
  echo "" >&2
  echo "   Example: /foundry:start \"user authentication\" --spec docs/specs/auth.md" >&2
  exit 1
fi

# Output parsed state for the plan command to use
echo "Foundry — Build-Verify-Fix Loop"
echo ""
echo "Scope: $SCOPE"
if [[ -n "$SPEC_PATH" ]]; then echo "Spec: $SPEC_PATH"; fi
if [[ -n "$URL" ]]; then echo "URL: $URL"; fi
if [[ -n "$OUTPUT_DIR" ]]; then echo "Output: $OUTPUT_DIR"; fi
if [[ "$TEMPER" == "true" ]]; then echo "Temper: enabled"; fi
if [[ "$MAX_CYCLES" -gt 0 ]]; then echo "Max Cycles: $MAX_CYCLES"; fi
if [[ "$NO_UI" == "true" ]]; then echo "UI: disabled"; fi
if [[ -n "$TICKET" ]]; then echo "Ticket: $TICKET"; fi
if [[ -n "$DESCRIPTION" ]]; then echo "Description: $DESCRIPTION"; fi
echo ""
echo "FOUNDRY_SCOPE=$SCOPE"
echo "FOUNDRY_SPEC=$SPEC_PATH"
echo "FOUNDRY_URL=$URL"
echo "FOUNDRY_OUTPUT=$OUTPUT_DIR"
echo "FOUNDRY_TEMPER=$TEMPER"
echo "FOUNDRY_MAX_CYCLES=$MAX_CYCLES"
echo "FOUNDRY_NO_UI=$NO_UI"
echo "FOUNDRY_TICKET=$TICKET"
echo "FOUNDRY_DESC=$DESCRIPTION"
echo "FOUNDRY_SKIP_BACKEND=$SKIP_START_BACKEND"
echo ""
echo "Use MCP tool Foundry-Init to create the run, then follow the phase guide."
echo "Call Foundry-Next at every step to get specific instructions."
echo ""
echo "Forge plans. Foundry builds."
