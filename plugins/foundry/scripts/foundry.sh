#!/usr/bin/env bash
# foundry.sh — File management and state utility for The Foundry
# Manages the foundry-archive/{run}/ directory structure, defect ledger, verdicts,
# and forge log. All state lives in PROJECT_ROOT/foundry-archive/{run}/.
#
# Usage:
#   foundry.sh init [--spec <path>] [--url <url>] [--temper] [--no-ui] [--max-cycles N] [--ticket ID] [--desc TEXT]
#   foundry.sh init --resume <name>
#   foundry.sh add-casting <id> "<title>" [depends] [scope] [key_files]
#   foundry.sh add-defect <cycle> <source> <json>
#   foundry.sh add-verdict <requirement_id> <verdict> <evidence_json>
#   foundry.sh summary
#   foundry.sh status
#   foundry.sh forge-log <cycle> <message>
#   foundry.sh save-trace <cycle> <json_file>
#   foundry.sh save-proof <cycle> <json_file>
#   foundry.sh save-console-errors <cycle> <file|->
#   foundry.sh compare-console-errors <cycle>
#   foundry.sh register-team <name>
#   foundry.sh unregister-team <name>
#   foundry.sh check-teams
#   foundry.sh clean <name>
#   foundry.sh list                   # List all runs in foundry-archive/
#   foundry.sh gate <phase>           # Validate preconditions for phase
#   foundry.sh mark-cast-complete
#   foundry.sh mark-phase <phase>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# Project root is the parent of .claude/ (which contains scripts/)
PROJECT_ROOT="$CLAUDE_DIR"
if [[ "$(basename "$CLAUDE_DIR")" == ".claude" ]]; then
    PROJECT_ROOT="$(dirname "$CLAUDE_DIR")"
fi

ARCHIVE_DIR="${PROJECT_ROOT}/foundry-archive"

# FOUNDRY_DIR is set per-run (by init or resume)
FOUNDRY_DIR=""

if ! command -v jq &>/dev/null; then
    echo '{"error":"jq is required"}' >&2
    exit 1
fi

# --- helpers ---

# Generate run name: ticket-desc or random adj-noun
_generate_run_name() {
    local ticket="${1:-}"
    local desc="${2:-}"
    local parts=()
    [[ -n "$ticket" ]] && parts+=("$ticket")
    if [[ -n "$desc" ]]; then
        local slug
        slug=$(echo "$desc" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | sed 's/[^a-z0-9-]//g' | head -c 40)
        [[ -n "$slug" ]] && parts+=("$slug")
    fi
    if [[ ${#parts[@]} -gt 0 ]]; then
        echo "${parts[*]}" | tr ' ' '-'
        return
    fi
    local adjectives=(ambitious blazing bold brave calm clever cosmic daring deft eager fierce flying golden grand heroic humble iron jolly keen lively lucky mighty noble plucky quick rapid roaring sharp silver sleek soaring steady steel stout swift thunder titan valiant vivid wild witty zesty)
    local nouns=(anvil arrow badger beacon bison bolt canyon cedar comet condor crane falcon forge fox glacier granite hawk helm heron jaguar kestrel lance leopard lynx mammoth mantis maple marble mustang oak orca osprey otter panther phoenix pike puma quartz raven ridge salmon sequoia squid stallion summit talon tiger trout viper vulture walrus wolf wren)
    local adj=${adjectives[$RANDOM % ${#adjectives[@]}]}
    local noun=${nouns[$RANDOM % ${#nouns[@]}]}
    echo "${adj}-${noun}"
}

next_defect_id() {
    if [[ ! -f "${FOUNDRY_DIR}/defects.json" ]]; then
        echo "D-001"
        return
    fi
    local count
    count=$(jq '.defects | length' "${FOUNDRY_DIR}/defects.json")
    printf "D-%03d" $((count + 1))
}

ensure_foundry() {
    if [[ -z "$FOUNDRY_DIR" || ! -d "$FOUNDRY_DIR" ]]; then
        echo '{"error":"No active foundry run. Use init or --resume."}' >&2
        return 1
    fi
}

# --- commands ---

cmd_init() {
    local spec_path=""
    local temper=false
    local no_ui=false
    local url=""
    local max_cycles=0
    local resume=""
    local ticket=""
    local desc=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --spec)      spec_path="${2:-}"; shift 2 ;;
            --temper)    temper=true; shift ;;
            --no-ui)     no_ui=true; shift ;;
            --url)       url="${2:-}"; shift 2 ;;
            --max-cycles) max_cycles="${2:-0}"; shift 2 ;;
            --resume)    resume="${2:-}"; shift 2 ;;
            --ticket)    ticket="${2:-}"; shift 2 ;;
            --desc)      desc="${2:-}"; shift 2 ;;
            *)           shift ;;
        esac
    done

    mkdir -p "$ARCHIVE_DIR"

    # --- Resume mode ---
    if [[ -n "$resume" ]]; then
        FOUNDRY_DIR="${ARCHIVE_DIR}/${resume}"
        if [[ ! -f "${FOUNDRY_DIR}/state.json" ]]; then
            echo "{\"error\":\"Run '${resume}' not found in foundry-archive/\"}" >&2
            return 1
        fi
        local phase cycle
        phase=$(jq -r '.phase // "?"' "${FOUNDRY_DIR}/state.json")
        cycle=$(jq -r '.cycle // 0' "${FOUNDRY_DIR}/state.json")
        echo "{\"ok\":true, \"resumed\":\"${resume}\", \"phase\":\"${phase}\", \"cycle\":${cycle}}"
        return 0
    fi

    # --- New run ---
    local run_name
    run_name=$(_generate_run_name "$ticket" "$desc")

    # Ensure unique
    if [[ -d "${ARCHIVE_DIR}/${run_name}" ]]; then
        run_name="${run_name}-$((RANDOM % 900 + 100))"
    fi

    FOUNDRY_DIR="${ARCHIVE_DIR}/${run_name}"

    # Silently delete legacy .foundry-dir
    rm -f "${PROJECT_ROOT}/.foundry-dir"

    # Create directory structure
    mkdir -p "${FOUNDRY_DIR}/castings"
    mkdir -p "${FOUNDRY_DIR}/traces"
    mkdir -p "${FOUNDRY_DIR}/proofs"
    mkdir -p "${FOUNDRY_DIR}/proofs/screenshots"

    # Copy spec if provided
    if [[ -n "$spec_path" ]]; then
        if [[ ! -f "$spec_path" ]]; then
            echo "{\"error\":\"Spec file not found: ${spec_path}\"}" >&2
            return 1
        fi
        cp "$spec_path" "${FOUNDRY_DIR}/spec.md"
    fi

    # Initialize manifest.json
    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    jq -n \
        --arg created "$now" \
        --argjson max_cycles "$max_cycles" \
        --argjson temper "$temper" \
        --argjson no_ui "$no_ui" \
        --arg url "$url" \
        --arg spec "${spec_path:-}" \
        '{
            created_at: $created,
            updated_at: $created,
            spec_path: $spec,
            temper: $temper,
            no_ui: $no_ui,
            target_url: $url,
            max_cycles: $max_cycles,
            current_cycle: 0,
            status: "initialized",
            castings: [],
            waves: []
        }' > "${FOUNDRY_DIR}/castings/manifest.json"

    # Initialize state.json
    jq -n \
        --arg phase "F0" \
        --arg started "$now" \
        --arg spec "${spec_path:-}" \
        --argjson temper "$temper" \
        --argjson no_ui "$no_ui" \
        '{phase: $phase, cycle: 0, spec_path: $spec, temper: $temper, no_ui: $no_ui, started_at: $started, phase_times: {}}' \
        > "${FOUNDRY_DIR}/state.json"

    # Initialize defects.json
    jq -n '{ defects: [] }' > "${FOUNDRY_DIR}/defects.json"

    # Initialize verdicts.json
    jq -n '{ cycle: 0, requirements: [] }' > "${FOUNDRY_DIR}/verdicts.json"

    # Initialize forge-log.md
    cat > "${FOUNDRY_DIR}/forge-log.md" << 'HEADER'
# Forge Log

Cumulative log of all foundry operations, timestamped per cycle.

HEADER

    # Initialize cumulative findings document
    cat > "${FOUNDRY_DIR}/forge-findings.md" << 'FINDINGS'
# Forge Findings

Cumulative findings across all INSPECT/GRIND/ASSAY/TEMPER cycles.
Re-read this file at the start of every INSPECT cycle.

## Open Defects
<!-- Updated by sync-defects. Tracks all OPEN items with spec refs and fix history. -->

## Regressions
<!-- Items that were fixed but reappeared. Escalate these — they indicate fragile fixes. -->

## Console Errors
<!-- Persistent console errors from SIGHT audits. Tracked with "still present" flag. -->

## Domain Health
<!-- TEMPER domain verdicts. Tracks HARDENED/BRITTLE/HOLLOW/MISSING across probes. -->

## Patterns
<!-- Systemic patterns detected by ASSAY. Root causes, not symptoms. -->

FINDINGS

    # Initialize lessons file
    if [[ ! -f "${FOUNDRY_DIR}/lessons.md" ]]; then
        cat > "${FOUNDRY_DIR}/lessons.md" << 'LESSONS'
# Foundry Lessons

Accumulated knowledge from foundry runs. Never deleted — read at init, appended after
triage/ASSAY/TEMPER/completion. Records regression patterns, codebase pitfalls,
effective fix strategies, and domain health history.

LESSONS
    fi

    # Initialize temper directory if requested
    if [[ "$temper" == "true" ]]; then
        mkdir -p "${FOUNDRY_DIR}/temper/probe-results"
        jq -n '{ domains: [], status: "pending" }' > "${FOUNDRY_DIR}/temper/domains.json"
    fi

    echo "{\"ok\":true, \"run_name\":\"${run_name}\", \"foundry_dir\":\"${FOUNDRY_DIR}\", \"temper\":${temper}, \"no_ui\":${no_ui}}"
}

cmd_list() {
    if [[ ! -d "$ARCHIVE_DIR" ]]; then
        echo '{"runs":[]}'
        return 0
    fi
    local runs=()
    for dir in "${ARCHIVE_DIR}"/*/; do
        [[ ! -d "$dir" ]] && continue
        local name
        name=$(basename "$dir")
        local phase="?"
        if [[ -f "${dir}state.json" ]]; then
            phase=$(jq -r '.phase // "?"' "${dir}state.json" 2>/dev/null || echo "?")
        fi
        runs+=("{\"name\":\"${name}\",\"phase\":\"${phase}\"}")
    done
    if [[ ${#runs[@]} -eq 0 ]]; then
        echo '{"runs":[]}'
    else
        local json_runs
        json_runs=$(printf '%s\n' "${runs[@]}" | jq -s '.')
        jq -n --argjson runs "$json_runs" '{runs: $runs}'
    fi
}

cmd_add_defect() {
    ensure_foundry || return 1

    local cycle="${1:?cycle required}"
    local source="${2:?source required}"
    local defect_json="${3:?json required}"

    case "$source" in
        trace|prove|sight|test|assay|temper) ;;
        *)
            echo "{\"error\":\"Invalid source: ${source}. Must be one of: trace, prove, sight, test, assay, temper\"}" >&2
            return 1
            ;;
    esac

    local defect_id
    defect_id=$(next_defect_id)

    local new_defect
    new_defect=$(echo "$defect_json" | jq \
        --arg id "$defect_id" \
        --argjson cycle "$cycle" \
        --arg source "$source" \
        'del(.severity, .deferred, .priority, .status) + {id: $id, cycle: $cycle, source: $source, status: "open", fixed_in_cycle: null}')

    local tmp="${FOUNDRY_DIR}/defects.json.tmp"
    jq --argjson defect "$new_defect" '.defects += [$defect]' \
        "${FOUNDRY_DIR}/defects.json" > "$tmp"
    mv "$tmp" "${FOUNDRY_DIR}/defects.json"

    echo "{\"ok\":true, \"defect_id\":\"${defect_id}\"}"
}

cmd_mark_defect_fixed() {
    ensure_foundry || return 1

    local defect_id="${1:?defect_id required}"
    local cycle="${2:?cycle required}"

    local exists
    exists=$(jq --arg id "$defect_id" '[.defects[] | select(.id == $id)] | length' \
        "${FOUNDRY_DIR}/defects.json")
    if [[ "$exists" -eq 0 ]]; then
        echo "{\"error\":\"Defect ${defect_id} not found\"}" >&2
        return 1
    fi

    local tmp="${FOUNDRY_DIR}/defects.json.tmp"
    jq --arg id "$defect_id" --argjson cycle "$cycle" \
        '.defects = [.defects[] | if .id == $id then .status = "fixed" | .fixed_in_cycle = $cycle else . end]' \
        "${FOUNDRY_DIR}/defects.json" > "$tmp"
    mv "$tmp" "${FOUNDRY_DIR}/defects.json"

    echo "{\"ok\":true, \"defect_id\":\"${defect_id}\", \"fixed_in_cycle\":${cycle}}"
}

cmd_sync_defects() {
    ensure_foundry || return 1

    local cycle="${1:?cycle required}"
    local new_findings_json="${2:?new_findings_json required (file path or -)}"

    local new_findings
    if [[ "$new_findings_json" == "-" ]]; then
        new_findings=$(cat)
    elif [[ -f "$new_findings_json" ]]; then
        new_findings=$(cat "$new_findings_json")
    else
        echo "{\"error\":\"File not found: ${new_findings_json}\"}" >&2
        return 1
    fi

    local reopened=0
    local added=0

    local fixed_defects
    fixed_defects=$(jq -c '[.defects[] | select(.status == "fixed")]' "${FOUNDRY_DIR}/defects.json")

    local finding_count
    finding_count=$(echo "$new_findings" | jq 'length')

    local i=0
    while [[ "$i" -lt "$finding_count" ]]; do
        local finding
        finding=$(echo "$new_findings" | jq -c ".[$i]")
        local finding_symbol
        finding_symbol=$(echo "$finding" | jq -r '.symbol // .description // ""')

        local match_id=""
        if [[ -n "$finding_symbol" ]]; then
            match_id=$(echo "$fixed_defects" | jq -r \
                --arg sym "$finding_symbol" \
                '[.[] | select((.symbol // .description // "") == $sym)] | .[0].id // ""')
        fi

        if [[ -n "$match_id" && "$match_id" != "null" && "$match_id" != "" ]]; then
            local tmp="${FOUNDRY_DIR}/defects.json.tmp"
            jq --arg id "$match_id" --argjson cycle "$cycle" \
                '.defects = [.defects[] | if .id == $id then .status = "open" | .regression = true | .reopened_in_cycle = $cycle | .fixed_in_cycle = null else . end]' \
                "${FOUNDRY_DIR}/defects.json" > "$tmp"
            mv "$tmp" "${FOUNDRY_DIR}/defects.json"
            reopened=$((reopened + 1))
        else
            local source
            source=$(echo "$finding" | jq -r '.source // "inspect"')
            case "$source" in
                trace|prove|sight|test|assay|temper) ;;
                *) source="trace" ;;
            esac
            cmd_add_defect "$cycle" "$source" "$(echo "$finding" | jq -c '.')" >/dev/null
            added=$((added + 1))
        fi

        i=$((i + 1))
    done

    echo "{\"ok\":true, \"cycle\":${cycle}, \"added\":${added}, \"reopened\":${reopened}}"
}

cmd_defects_to_tasks() {
    ensure_foundry || return 1

    local open_defects
    open_defects=$(jq -c '[.defects[] | select(.status == "open")]' "${FOUNDRY_DIR}/defects.json")
    local count
    count=$(echo "$open_defects" | jq 'length')

    if [[ "$count" -eq 0 ]]; then
        echo '{"ok":true, "tasks":[], "count":0}'
        return 0
    fi

    local tasks
    tasks=$(echo "$open_defects" | jq '[
        group_by(.file // .symbol // .id) |
        .[] |
        {
            defect_ids: [.[].id],
            description: ([.[].description] | join("; ")),
            files: ([.[].file // empty] | unique),
            symbols: ([.[].symbol // empty] | unique),
            regression: (any(.[]; .regression == true)),
            source: .[0].source
        }
    ]')

    local task_count
    task_count=$(echo "$tasks" | jq 'length')

    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "${now} count=${task_count}" > "${FOUNDRY_DIR}/.tasks-generated"

    echo "$tasks" | jq --argjson count "$task_count" '{ok: true, tasks: ., count: $count}'
}

cmd_add_verdict() {
    ensure_foundry || return 1

    local req_id="${1:?requirement_id required}"
    local verdict="${2:?verdict required}"
    local evidence_json="${3:?evidence_json required}"

    local entry
    entry=$(jq -n \
        --arg id "$req_id" \
        --arg verdict "$verdict" \
        --arg evidence "$evidence_json" \
        '{id: $id, verdict: $verdict, evidence: $evidence}')

    local tmp="${FOUNDRY_DIR}/verdicts.json.tmp"
    local exists
    exists=$(jq --arg id "$req_id" '[.requirements[] | select(.id == $id)] | length' \
        "${FOUNDRY_DIR}/verdicts.json")

    if [[ "$exists" -gt 0 ]]; then
        jq --argjson entry "$entry" --arg id "$req_id" \
            '.requirements = [.requirements[] | if .id == $id then $entry else . end]' \
            "${FOUNDRY_DIR}/verdicts.json" > "$tmp"
    else
        jq --argjson entry "$entry" \
            '.requirements += [$entry]' \
            "${FOUNDRY_DIR}/verdicts.json" > "$tmp"
    fi
    mv "$tmp" "${FOUNDRY_DIR}/verdicts.json"

    echo "{\"ok\":true, \"requirement\":\"${req_id}\", \"verdict\":\"${verdict}\"}"
}

cmd_summary() {
    ensure_foundry || return 1

    local total_defects open_defects fixed_defects
    total_defects=$(jq '.defects | length' "${FOUNDRY_DIR}/defects.json")
    open_defects=$(jq '[.defects[] | select(.status == "open")] | length' "${FOUNDRY_DIR}/defects.json")
    fixed_defects=$(jq '[.defects[] | select(.status == "fixed")] | length' "${FOUNDRY_DIR}/defects.json")

    local total_verdicts verified non_verified
    total_verdicts=$(jq '.requirements | length' "${FOUNDRY_DIR}/verdicts.json")
    verified=$(jq '[.requirements[] | select(.verdict == "VERIFIED")] | length' "${FOUNDRY_DIR}/verdicts.json")
    non_verified=$((total_verdicts - verified))

    local cycle
    cycle=$(jq '.cycle' "${FOUNDRY_DIR}/verdicts.json")

    jq -n \
        --argjson total_defects "$total_defects" \
        --argjson open "$open_defects" \
        --argjson fixed "$fixed_defects" \
        --argjson total_verdicts "$total_verdicts" \
        --argjson verified "$verified" \
        --argjson non_verified "$non_verified" \
        --argjson cycle "$cycle" \
        '{
            defects: { total: $total_defects, open: $open, fixed: $fixed },
            verdicts: { total: $total_verdicts, verified: $verified, non_verified: $non_verified },
            cycle: $cycle
        }'
}

cmd_status() {
    ensure_foundry || return 1

    local manifest="${FOUNDRY_DIR}/castings/manifest.json"
    local defects="${FOUNDRY_DIR}/defects.json"

    local has_spec=false
    if [[ -f "${FOUNDRY_DIR}/spec.md" ]]; then
        has_spec=true
    fi

    local has_temper=false
    if [[ -d "${FOUNDRY_DIR}/temper" ]]; then
        has_temper=true
    fi

    local trace_count proof_count
    trace_count=$(find "${FOUNDRY_DIR}/traces" -type f 2>/dev/null | wc -l | tr -d ' ')
    proof_count=$(find "${FOUNDRY_DIR}/proofs" -type f 2>/dev/null | wc -l | tr -d ' ')

    local manifest_json defects_json
    manifest_json=$(cat "$manifest")
    defects_json=$(cmd_summary 2>/dev/null)

    jq -n \
        --arg fdir "$FOUNDRY_DIR" \
        --argjson manifest "$manifest_json" \
        --argjson defects "$defects_json" \
        --argjson has_spec "$has_spec" \
        --argjson has_temper "$has_temper" \
        --argjson trace_count "$trace_count" \
        --argjson proof_count "$proof_count" \
        '{
            foundry_dir: $fdir,
            has_spec: $has_spec,
            has_temper: $has_temper,
            trace_count: $trace_count,
            proof_count: $proof_count,
            manifest: $manifest,
            defects: $defects
        }'
}

cmd_forge_log() {
    ensure_foundry || return 1

    local cycle="${1:?cycle required}"
    local message="${2:?message required}"

    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    printf "\n## Cycle %s — %s\n\n%s\n" "$cycle" "$now" "$message" \
        >> "${FOUNDRY_DIR}/forge-log.md"

    echo "{\"ok\":true}"
}

cmd_save_trace() {
    ensure_foundry || return 1

    local cycle="${1:?cycle required}"
    local json_file="${2:?json_file required}"

    if [[ ! -f "$json_file" ]]; then
        echo "{\"error\":\"File not found: ${json_file}\"}" >&2
        return 1
    fi

    local dest="${FOUNDRY_DIR}/traces/cycle-${cycle}.json"
    cp "$json_file" "$dest"

    echo "{\"ok\":true, \"path\":\"${dest}\"}"
}

cmd_save_proof() {
    ensure_foundry || return 1

    local cycle="${1:?cycle required}"
    local json_file="${2:?json_file required}"

    if [[ ! -f "$json_file" ]]; then
        echo "{\"error\":\"File not found: ${json_file}\"}" >&2
        return 1
    fi

    local dest="${FOUNDRY_DIR}/proofs/cycle-${cycle}.json"
    cp "$json_file" "$dest"

    echo "{\"ok\":true, \"path\":\"${dest}\"}"
}

cmd_clean() {
    local name="${1:?run name required}"
    local run_dir="${ARCHIVE_DIR}/${name}"
    if [[ ! -d "$run_dir" ]]; then
        echo "{\"ok\":true, \"message\":\"Run '${name}' does not exist.\"}"
        return 0
    fi

    rm -rf "$run_dir"
    echo "{\"ok\":true, \"message\":\"Run '${name}' removed from foundry-archive/.\"}"
}

cmd_add_casting() {
    ensure_foundry || return 1

    local id="${1:?casting id required}"
    local title="${2:?title required}"
    local depends="${3:-[]}"
    local scope="${4:-}"
    local key_files="${5:-[]}"

    if [[ "$depends" != "["* ]]; then
        if [[ -z "$depends" || "$depends" == "none" ]]; then
            depends="[]"
        else
            depends=$(echo "$depends" | jq -R 'split(",") | map(tonumber)')
        fi
    fi

    if [[ "$key_files" != "["* ]]; then
        if [[ -z "$key_files" || "$key_files" == "none" ]]; then
            key_files="[]"
        else
            key_files=$(echo "$key_files" | jq -R 'split(",")')
        fi
    fi

    local manifest="${FOUNDRY_DIR}/castings/manifest.json"
    if [[ ! -f "$manifest" ]]; then
        echo '{"error":"manifest.json not found. Run init first."}' >&2
        return 1
    fi

    local existing
    existing=$(jq --argjson id "$id" '[.castings[] | select(.id == $id)] | length' "$manifest")
    if [[ "$existing" -gt 0 ]]; then
        echo "{\"error\":\"Casting with id ${id} already exists\"}" >&2
        return 1
    fi

    local tmp="${manifest}.tmp"
    jq --argjson id "$id" \
       --arg title "$title" \
       --argjson depends "$depends" \
       --arg scope "$scope" \
       --argjson key_files "$key_files" \
       '.castings += [{id: $id, title: $title, depends: $depends, scope: $scope, key_files: $key_files, status: "pending"}] | .updated_at = (now | todate)' \
       "$manifest" > "$tmp"
    mv "$tmp" "$manifest"

    echo "{\"ok\":true, \"casting_id\":${id}, \"title\":\"${title}\"}"
}

# --- team lifecycle ---

cmd_register_team() {
    ensure_foundry || return 1
    local team_name="${1:?team name required}"
    local state_file="${FOUNDRY_DIR}/state.json"

    local teams_dir="${HOME}/.claude/teams"
    if [[ -f "$state_file" ]]; then
        local existing
        existing=$(jq -r '.active_teams // [] | .[]' "$state_file" 2>/dev/null)
        local still_active=""
        while IFS= read -r t; do
            [[ -z "$t" ]] && continue
            [[ "$t" == "$team_name" ]] && continue
            if [[ -d "${teams_dir}/${t}" ]]; then
                still_active="${still_active} ${t}"
            fi
        done <<< "$existing"
        still_active=$(echo "$still_active" | xargs)
        if [[ -n "$still_active" ]]; then
            echo "{\"error\":\"Cannot register '${team_name}' — active teams exist: ${still_active}\", \"hint\":\"Shut down existing teammates + TeamDelete + unregister-team before creating a new team.\"}" >&2
            return 1
        fi
    fi

    if [[ ! -f "$state_file" ]]; then
        jq -n --arg t "$team_name" '{active_teams: [$t]}' > "$state_file"
    else
        local tmp="${state_file}.tmp"
        jq --arg t "$team_name" '
            .active_teams = ((.active_teams // []) + [$t] | unique)
        ' "$state_file" > "$tmp"
        mv "$tmp" "$state_file"
    fi
    echo "{\"ok\":true, \"registered\":\"${team_name}\"}"
}

cmd_unregister_team() {
    ensure_foundry || return 1
    local team_name="${1:?team name required}"
    local state_file="${FOUNDRY_DIR}/state.json"

    if [[ ! -f "$state_file" ]]; then
        echo '{"ok":true, "message":"No state file, nothing to unregister"}'
        return 0
    fi

    local tmp="${state_file}.tmp"
    jq --arg t "$team_name" '
        .active_teams = [(.active_teams // [])[] | select(. != $t)]
    ' "$state_file" > "$tmp"
    mv "$tmp" "$state_file"
    echo "{\"ok\":true, \"unregistered\":\"${team_name}\"}"
}

check_active_teams() {
    ACTIVE_TEAM_NAMES=""
    local state_file="${FOUNDRY_DIR}/state.json"

    if [[ ! -f "$state_file" ]]; then
        return 0
    fi

    local teams
    teams=$(jq -r '.active_teams // [] | .[]' "$state_file" 2>/dev/null)
    if [[ -z "$teams" ]]; then
        return 0
    fi

    local still_active=""
    local team_dir="${HOME}/.claude/teams"
    while IFS= read -r team_name; do
        if [[ -d "${team_dir}/${team_name}" ]]; then
            still_active="${still_active} ${team_name}"
        fi
    done <<< "$teams"

    still_active=$(echo "$still_active" | xargs)
    if [[ -n "$still_active" ]]; then
        ACTIVE_TEAM_NAMES="$still_active"
        return 1
    fi
    return 0
}

cmd_check_teams() {
    ensure_foundry || return 1
    if check_active_teams; then
        echo '{"active":false, "teams":[]}'
    else
        # shellcheck disable=SC2086
        local json_teams
        json_teams=$(printf '%s\n' $ACTIVE_TEAM_NAMES | jq -R . | jq -s .)
        jq -n --argjson teams "$json_teams" '{active: true, teams: $teams}'
    fi
}

# --- findings management ---

cmd_update_findings() {
    ensure_foundry || return 1

    local section="${1:?section required (defects|regressions|console|domains|patterns)}"
    local content="${2:?content required}"

    local findings_file="${FOUNDRY_DIR}/forge-findings.md"
    if [[ ! -f "$findings_file" ]]; then
        echo '{"error":"forge-findings.md not found. Run init first."}' >&2
        return 1
    fi

    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    local header=""
    case "$section" in
        defects)     header="## Open Defects" ;;
        regressions) header="## Regressions" ;;
        console)     header="## Console Errors" ;;
        domains)     header="## Domain Health" ;;
        patterns)    header="## Patterns" ;;
        *)
            echo "{\"error\":\"Invalid section: ${section}. Must be: defects, regressions, console, domains, patterns\"}" >&2
            return 1
            ;;
    esac

    local content_file="${findings_file}.content.tmp"
    printf "\n### Update %s\n%s\n" "$now" "$content" > "$content_file"

    local tmp="${findings_file}.tmp"
    awk -v header="$header" -v cfile="$content_file" '
        BEGIN { found=0; printed=0 }
        $0 == header { found=1; print; next }
        found && /^## / {
            while ((getline line < cfile) > 0) print line
            close(cfile)
            print ""
            printed=1
            found=0
        }
        { print }
        END { if (found && !printed) { while ((getline line < cfile) > 0) print line; close(cfile) } }
    ' "$findings_file" > "$tmp"
    mv "$tmp" "$findings_file"
    rm -f "$content_file"

    echo "{\"ok\":true, \"section\":\"${section}\"}"
}

cmd_append_lesson() {
    ensure_foundry || return 1

    local lesson="${1:?lesson text required}"
    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    echo "" >> "${FOUNDRY_DIR}/lessons.md"
    echo "## ${now}" >> "${FOUNDRY_DIR}/lessons.md"
    echo "" >> "${FOUNDRY_DIR}/lessons.md"
    echo "$lesson" >> "${FOUNDRY_DIR}/lessons.md"

    echo '{"ok":true}'
}

# --- console error tracking ---

cmd_save_console_errors() {
    ensure_foundry || return 1

    local cycle="${1:?cycle required}"
    local errors_file="${2:?errors file or - for stdin}"

    mkdir -p "${FOUNDRY_DIR}/proofs"

    local dest="${FOUNDRY_DIR}/proofs/console-errors-cycle-${cycle}.md"

    if [[ "$errors_file" == "-" ]]; then
        cat > "$dest"
    elif [[ -f "$errors_file" ]]; then
        cp "$errors_file" "$dest"
    else
        echo "{\"error\":\"File not found: ${errors_file}\"}" >&2
        return 1
    fi

    echo "{\"ok\":true, \"path\":\"${dest}\"}"
}

cmd_compare_console_errors() {
    ensure_foundry || return 1

    local cycle="${1:?cycle required}"

    local current="${FOUNDRY_DIR}/proofs/console-errors-cycle-${cycle}.md"
    local prev_cycle=$((cycle - 1))
    local previous="${FOUNDRY_DIR}/proofs/console-errors-cycle-${prev_cycle}.md"

    if [[ ! -f "$current" ]]; then
        echo '{"error":"No console errors file for current cycle"}' >&2
        return 1
    fi

    if [[ ! -f "$previous" ]]; then
        local total
        total=$(wc -l < "$current" | tr -d ' ')
        echo "{\"ok\":true, \"cycle\":${cycle}, \"new\":${total}, \"still_present\":0, \"resolved\":0, \"first_cycle\":true}"
        return 0
    fi

    local still_present=0 new_errors=0 resolved=0
    local persistent_list=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        local match
        match=$(grep -cFx -- "$line" "$previous" 2>/dev/null || true)
        if [[ "$match" -gt 0 ]]; then
            still_present=$((still_present + 1))
            persistent_list="${persistent_list}- ${line}
"
        else
            new_errors=$((new_errors + 1))
        fi
    done < "$current" || true

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]] && continue
        local match
        match=$(grep -cFx -- "$line" "$current" 2>/dev/null || true)
        if [[ "$match" -eq 0 ]]; then
            resolved=$((resolved + 1))
        fi
    done < "$previous" || true

    if [[ "$still_present" -gt 0 ]]; then
        cmd_update_findings "console" "STILL PRESENT (cycle ${prev_cycle} -> ${cycle}): ${still_present} errors persist.
${persistent_list}" >/dev/null 2>&1
    fi

    echo "{\"ok\":true, \"cycle\":${cycle}, \"new\":${new_errors}, \"still_present\":${still_present}, \"resolved\":${resolved}}"
}

# --- sight enforcement ---

cmd_check_sight_required() {
    ensure_foundry || return 1

    local manifest="${FOUNDRY_DIR}/castings/manifest.json"
    if [[ ! -f "$manifest" ]]; then
        echo '{"required":false, "reason":"No manifest"}'
        return 0
    fi

    local ui_patterns='\.tsx$|\.jsx$|\.vue$|\.svelte$|\.css$|\.scss$|\.html$|\.astro$'
    local ui_files
    ui_files=$(jq -r '.castings[].key_files[]?' "$manifest" 2>/dev/null | grep -E "$ui_patterns" || true)

    if [[ -z "$ui_files" ]]; then
        echo '{"required":false, "reason":"No frontend files in castings"}'
        return 0
    fi

    local url
    url=$(jq -r '.target_url // ""' "$manifest")
    local no_ui
    no_ui=$(jq -r '.no_ui // false' "$manifest")

    local file_count
    file_count=$(echo "$ui_files" | wc -l | tr -d ' ')

    if [[ "$no_ui" == "true" ]]; then
        echo "{\"required\":true, \"has_url\":false, \"blocked\":true, \"ui_files\":${file_count}, \"reason\":\"--no-ui set but ${file_count} frontend files in scope.\"}"
        return 1
    fi

    if [[ -z "$url" ]]; then
        echo "{\"required\":true, \"has_url\":false, \"blocked\":true, \"ui_files\":${file_count}, \"reason\":\"No --url provided but ${file_count} frontend files in scope.\"}"
        return 1
    fi

    echo "{\"required\":true, \"has_url\":true, \"blocked\":false, \"url\":\"${url}\", \"ui_files\":${file_count}}"
}

# --- gates ---

cmd_gate() {
    local phase="${1:?phase required}"

    case "$phase" in
        cast)       gate_cast ;;
        inspect)    gate_inspect ;;
        grind)      gate_grind ;;
        assay)      gate_assay ;;
        temper)     gate_temper ;;
        done)       gate_done ;;
        *)
            echo "{\"gate\":\"${phase}\", \"passed\":false, \"reason\":\"Unknown gate: ${phase}\"}" >&2
            return 1
            ;;
    esac
}

gate_cast() {
    if [[ ! -d "$FOUNDRY_DIR" ]]; then
        echo '{"gate":"cast", "passed":false, "reason":"Foundry directory does not exist"}'
        return 1
    fi
    if [[ ! -f "${FOUNDRY_DIR}/castings/manifest.json" ]]; then
        echo '{"gate":"cast", "passed":false, "reason":"manifest.json not found"}'
        return 1
    fi
    local count
    count=$(jq '.castings | length' "${FOUNDRY_DIR}/castings/manifest.json")
    if [[ "$count" -lt 1 ]]; then
        echo '{"gate":"cast", "passed":false, "reason":"No castings in manifest"}'
        return 1
    fi
    echo '{"gate":"cast", "passed":true}'
}

gate_inspect() {
    if [[ ! -f "${FOUNDRY_DIR}/castings/manifest.json" ]]; then
        echo '{"gate":"inspect", "passed":false, "reason":"manifest.json not found"}'
        return 1
    fi
    if [[ ! -f "${FOUNDRY_DIR}/.cast-complete" ]]; then
        echo '{"gate":"inspect", "passed":false, "reason":"CAST not complete"}'
        return 1
    fi
    if ! check_active_teams; then
        echo "{\"gate\":\"inspect\", \"passed\":false, \"reason\":\"Active teams: ${ACTIVE_TEAM_NAMES}\"}"
        return 1
    fi
    echo '{"gate":"inspect", "passed":true}'
}

gate_grind() {
    if [[ ! -f "${FOUNDRY_DIR}/defects.json" ]]; then
        echo '{"gate":"grind", "passed":false, "reason":"defects.json not found"}'
        return 1
    fi
    local open
    open=$(jq '[.defects[] | select(.status == "open")] | length' "${FOUNDRY_DIR}/defects.json")
    if [[ "$open" -lt 1 ]]; then
        echo '{"gate":"grind", "passed":false, "reason":"No open defects to grind"}'
        return 1
    fi
    if ! check_active_teams; then
        echo "{\"gate\":\"grind\", \"passed\":false, \"reason\":\"Active teams: ${ACTIVE_TEAM_NAMES}\"}"
        return 1
    fi
    if [[ ! -f "${FOUNDRY_DIR}/.tasks-generated" ]]; then
        echo '{"gate":"grind", "passed":false, "reason":"defects-to-tasks has not been run"}'
        return 1
    fi
    echo '{"gate":"grind", "passed":true}'
}

gate_assay() {
    if [[ ! -f "${FOUNDRY_DIR}/defects.json" ]]; then
        echo '{"gate":"assay", "passed":false, "reason":"defects.json not found"}'
        return 1
    fi
    local open
    open=$(jq '[.defects[] | select(.status == "open")] | length' "${FOUNDRY_DIR}/defects.json")
    if [[ "$open" -gt 0 ]]; then
        echo "{\"gate\":\"assay\", \"passed\":false, \"reason\":\"${open} open defect(s) remain\"}"
        return 1
    fi
    if ! check_active_teams; then
        echo "{\"gate\":\"assay\", \"passed\":false, \"reason\":\"Active teams: ${ACTIVE_TEAM_NAMES}\"}"
        return 1
    fi
    echo '{"gate":"assay", "passed":true}'
}

gate_temper() {
    if [[ ! -f "${FOUNDRY_DIR}/verdicts.json" ]]; then
        echo '{"gate":"temper", "passed":false, "reason":"verdicts.json not found"}'
        return 1
    fi
    local non_verified
    non_verified=$(jq '[.requirements[] | select(.verdict != "VERIFIED")] | length' "${FOUNDRY_DIR}/verdicts.json")
    if [[ "$non_verified" -gt 0 ]]; then
        echo "{\"gate\":\"temper\", \"passed\":false, \"reason\":\"${non_verified} requirement(s) not verified\"}"
        return 1
    fi
    echo '{"gate":"temper", "passed":true}'
}

gate_done() {
    if [[ ! -f "${FOUNDRY_DIR}/verdicts.json" || ! -f "${FOUNDRY_DIR}/defects.json" ]]; then
        echo '{"gate":"done", "passed":false, "reason":"Missing state files"}'
        return 1
    fi
    local non_verified
    non_verified=$(jq '[.requirements[] | select(.verdict != "VERIFIED")] | length' "${FOUNDRY_DIR}/verdicts.json")
    if [[ "$non_verified" -gt 0 ]]; then
        echo "{\"gate\":\"done\", \"passed\":false, \"reason\":\"${non_verified} requirement(s) not verified\"}"
        return 1
    fi
    local open
    open=$(jq '[.defects[] | select(.status == "open")] | length' "${FOUNDRY_DIR}/defects.json")
    if [[ "$open" -gt 0 ]]; then
        echo "{\"gate\":\"done\", \"passed\":false, \"reason\":\"${open} open defect(s) remain\"}"
        return 1
    fi
    if ! check_active_teams; then
        echo "{\"gate\":\"done\", \"passed\":false, \"reason\":\"Active teams: ${ACTIVE_TEAM_NAMES}\"}"
        return 1
    fi
    echo '{"gate":"done", "passed":true}'
}

# --- markers ---

cmd_mark_stream_complete() {
    ensure_foundry || return 1
    local stream="${1:?stream required (trace|prove|sight|test|probe)}"
    local cycle="${2:?cycle required}"

    case "$stream" in
        trace|prove|sight|test|probe) ;;
        *)
            echo "{\"error\":\"Invalid stream: ${stream}\"}" >&2
            return 1
            ;;
    esac

    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "${now} cycle=${cycle}" > "${FOUNDRY_DIR}/.${stream}-complete"
    echo "{\"ok\":true, \"stream\":\"${stream}\", \"cycle\":${cycle}}"
}

cmd_check_streams_complete() {
    ensure_foundry || return 1

    local manifest="${FOUNDRY_DIR}/castings/manifest.json"
    local no_ui="false"
    if [[ -f "$manifest" ]]; then
        no_ui=$(jq -r '.no_ui // false' "$manifest" 2>/dev/null || echo "false")
    fi

    local missing=""
    for stream in trace prove test; do
        if [[ ! -f "${FOUNDRY_DIR}/.${stream}-complete" ]]; then
            missing="${missing} ${stream}"
        fi
    done

    if [[ "$no_ui" != "true" ]]; then
        if [[ ! -f "${FOUNDRY_DIR}/.sight-complete" ]]; then
            missing="${missing} sight"
        fi
    fi

    missing=$(echo "$missing" | xargs)
    if [[ -n "$missing" ]]; then
        echo "{\"complete\":false, \"missing\":\"${missing}\"}"
        return 1
    fi
    echo '{"complete":true}'
}

cmd_clear_stream_markers() {
    ensure_foundry || return 1
    rm -f "${FOUNDRY_DIR}/.trace-complete" \
          "${FOUNDRY_DIR}/.prove-complete" \
          "${FOUNDRY_DIR}/.sight-complete" \
          "${FOUNDRY_DIR}/.test-complete" \
          "${FOUNDRY_DIR}/.probe-complete" \
          "${FOUNDRY_DIR}/.inspect-clean" \
          "${FOUNDRY_DIR}/.tasks-generated"
    echo '{"ok":true, "message":"All stream markers cleared"}'
}

cmd_mark_inspect_clean() {
    ensure_foundry || return 1

    if ! cmd_check_streams_complete >/dev/null 2>&1; then
        echo '{"error":"Verification streams incomplete"}' >&2
        return 1
    fi

    local open
    open=$(jq '[.defects[] | select(.status == "open")] | length' "${FOUNDRY_DIR}/defects.json" 2>/dev/null || echo "0")
    if [[ "$open" -gt 0 ]]; then
        echo "{\"error\":\"${open} open defect(s) remain\"}" >&2
        return 1
    fi
    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "$now" > "${FOUNDRY_DIR}/.inspect-clean"
    echo '{"ok":true}'
}

cmd_mark_grind_start() {
    ensure_foundry || return 1
    cmd_clear_stream_markers >/dev/null 2>&1
    echo '{"ok":true, "message":"GRIND started — all markers cleared"}'
}

cmd_mark_cast_complete() {
    ensure_foundry || return 1
    if ! check_active_teams; then
        echo "{\"error\":\"Active teams: ${ACTIVE_TEAM_NAMES}\"}" >&2
        return 1
    fi
    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "$now" > "${FOUNDRY_DIR}/.cast-complete"
    echo '{"ok":true}'
}

cmd_mark_phase() {
    ensure_foundry || return 1
    local phase="${1:?phase required}"
    local now
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    local state_file="${FOUNDRY_DIR}/state.json"
    if [[ -f "$state_file" ]]; then
        local tmp="${state_file}.tmp"
        jq --arg phase "$phase" --arg ts "$now" \
            '.phase = $phase | .updated_at = $ts | .phase_history = ((.phase_history // []) + [{phase: $phase, entered_at: $ts}])' \
            "$state_file" > "$tmp"
        mv "$tmp" "$state_file"
    else
        jq -n --arg phase "$phase" --arg ts "$now" \
            '{phase: $phase, updated_at: $ts, phase_history: [{phase: $phase, entered_at: $ts}]}' \
            > "$state_file"
    fi

    echo "{\"ok\":true, \"phase\":\"${phase}\"}"
}

# --- dispatch ---

cmd="${1:-help}"
shift || true

# For commands that need a run dir, check --resume or use first positional arg
# Init handles its own parsing
case "$cmd" in
    init)
        cmd_init "$@"
        ;;
    list)
        cmd_list
        ;;
    clean)
        cmd_clean "$@"
        ;;
    help|--help|-h)
        echo "Usage: foundry.sh <command> [args]"
        echo ""
        echo "Commands:"
        echo "  init [--spec <path>] [--url <url>] [--temper] [--no-ui] [--ticket ID] [--desc TEXT]"
        echo "  init --resume <name>                Resume an existing run"
        echo "  list                                 List all runs in foundry-archive/"
        echo "  add-casting <id> <title> [depends] [scope] [key_files]"
        echo "  add-defect <cycle> <source> <json>"
        echo "  mark-defect-fixed <id> <cycle>"
        echo "  sync-defects <cycle> <file|->"
        echo "  defects-to-tasks"
        echo "  add-verdict <requirement_id> <verdict> <evidence_json>"
        echo "  summary"
        echo "  status"
        echo "  forge-log <cycle> <message>"
        echo "  save-trace <cycle> <json_file>"
        echo "  save-proof <cycle> <json_file>"
        echo "  register-team <name>"
        echo "  unregister-team <name>"
        echo "  check-teams"
        echo "  save-console-errors <cycle> <file|->"
        echo "  compare-console-errors <cycle>"
        echo "  check-sight-required"
        echo "  update-findings <section> <content>"
        echo "  append-lesson <text>"
        echo "  clean <name>                         Remove a specific run"
        echo "  gate <phase>"
        echo "  mark-stream-complete <stream> <cycle>"
        echo "  check-streams-complete"
        echo "  clear-stream-markers"
        echo "  mark-cast-complete"
        echo "  mark-inspect-clean"
        echo "  mark-grind-start"
        echo "  mark-phase <phase>"
        ;;
    *)
        # All other commands require FOUNDRY_DIR to be set
        # Check if FOUNDRY_DIR was set by a prior init in this script invocation
        # Otherwise, error — must use init --resume first
        if [[ -z "$FOUNDRY_DIR" ]]; then
            echo '{"error":"No active run. Use: foundry.sh init --resume <name>"}' >&2
            exit 1
        fi

        case "$cmd" in
            add-casting)    cmd_add_casting "$@" ;;
            add-defect)        cmd_add_defect "$@" ;;
            mark-defect-fixed) cmd_mark_defect_fixed "$@" ;;
            sync-defects)      cmd_sync_defects "$@" ;;
            defects-to-tasks)  cmd_defects_to_tasks ;;
            add-verdict)    cmd_add_verdict "$@" ;;
            summary)        cmd_summary ;;
            status)         cmd_status ;;
            forge-log)      cmd_forge_log "$@" ;;
            save-trace)     cmd_save_trace "$@" ;;
            save-proof)     cmd_save_proof "$@" ;;
            register-team)      cmd_register_team "$@" ;;
            unregister-team)    cmd_unregister_team "$@" ;;
            check-teams)        cmd_check_teams ;;
            save-console-errors)    cmd_save_console_errors "$@" ;;
            compare-console-errors) cmd_compare_console_errors "$@" ;;
            check-sight-required)   cmd_check_sight_required ;;
            update-findings)   cmd_update_findings "$@" ;;
            append-lesson)     cmd_append_lesson "$@" ;;
            gate)               cmd_gate "$@" ;;
            mark-stream-complete)  cmd_mark_stream_complete "$@" ;;
            check-streams-complete) cmd_check_streams_complete ;;
            clear-stream-markers)  cmd_clear_stream_markers ;;
            mark-cast-complete)    cmd_mark_cast_complete ;;
            mark-inspect-clean)    cmd_mark_inspect_clean ;;
            mark-grind-start)      cmd_mark_grind_start ;;
            mark-phase)            cmd_mark_phase "$@" ;;
            *)
                echo "{\"error\":\"Unknown command: ${cmd}\"}" >&2
                exit 1
                ;;
        esac
        ;;
esac
