"""D-001 — guards for `plugins/foundry/scripts/serena-daemon.sh`.

WHAT THIS FILE IS, AND WHAT IT IS NOT
--------------------------------------
The behavioural change these tests guard was authored by the LEAD at commit
`218ccab` — the `serena-agent==1.6.1` -> `>=1.7.0,<2.0.0` unpin, the XML escape
the range made necessary, and `cmd_doctor`'s project-configs rung. This file
adds the guards that change shipped without. It changes no behaviour of its
own.

`218ccab` is 116 added-plus-deleted lines in the script that supervises the
daemon EVERY foundry run depends on, and at the time this file was written
nothing under `mcp-server/tests/` referenced `serena-daemon.sh`, the
`FOUNDRY_SERENA_HEALTH` tokens, or the rendered plist. So the whole of it could
regress in silence — which is the same shape as the incident it fixed, where
`doctor` printed `verdict: healthy` through an entire run whose TRACE stream
could not resolve one symbol.

HOW A BASH SCRIPT IS TESTED HERE, STATED PLAINLY
--------------------------------------------------
Every assertion below is driven against the REAL shipped script. Nothing here
asserts over its source text, because a text assertion cannot tell a correct
escape from the one that actually shipped broken: `${var//<//&lt;}` and the
`sed` spelling that replaced it both read as "an escape exists" to a regex, and
only the RENDERED BYTES distinguish them. Two mechanisms make the drive
possible:

  $HOME relocation. Every path the script derives — `PID_FILE`, `LOG_FILE`,
  `PLIST_FILE`, `SYSTEMD_FILE` and the first two entries of
  `SERVICE_PATH_DIRS` — hangs off `$HOME`, so a throwaway home relocates the
  script entirely and no test can touch the real daemon, the real log, or the
  real LaunchAgents directory.

  A stub bin directory ahead of `$PATH`. Six one-line stubs — `uname`, `lsof`,
  `ps`, `curl`, `launchctl`, `uvx` — put the script into a chosen machine
  state. Each is documented at its writer below with the one question it
  answers. They are deliberately minimal: a stub that grows behaviour of its
  own becomes the thing under test.

`uname` is stubbed to `Darwin` on every platform ON PURPOSE, so these tests
exercise the launchd arm — the priority platform, and the one whose plist is
XML — identically on a macOS laptop and a Linux CI box.

WHAT IS NOT COVERED, SAID OUT LOUD
------------------------------------
launchd is never invoked. `launchctl` is stubbed to a failing no-op, so nothing
here proves the rendered plist is one launchd will ACCEPT — only that it is
well-formed XML whose `ProgramArguments` decode back to the exact specifier,
which is precisely the property the raw `<` destroyed. Loading a real job from
a test is not something a test suite may do to a developer's machine.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from xml.parsers.expat import ExpatError

import pytest

# tests/test_serena_daemon.py -> [0]=tests, [1]=mcp-server, [2]=foundry,
# [3]=plugins, [4]=repo-root. Mirrors test_containment.py's precedent: the
# surface under test lives ABOVE mcp-server/, so the plugin root is not enough.
REPO_ROOT = Path(__file__).resolve().parents[4]
DAEMON_SH = REPO_ROOT / "plugins" / "foundry" / "scripts" / "serena-daemon.sh"
SESSION_START_HOOK = (
    REPO_ROOT / "plugins" / "foundry" / "hooks" / "session-start-serena.sh"
)

#: The specifier `218ccab` moved to. Written here as a literal because these
#: tests must FAIL when it moves silently — a test that re-reads the constant
#: out of the script and compares it to itself pins nothing. The floor is
#: asserted structurally in `test_the_floor_excludes_the_1_6_x_that_could_not
#: _read_a_1_7_x_config`, so this literal is not the only thing saying 1.7.0.
SERENA_SPECIFIER = "serena-agent>=1.7.0,<2.0.0"

#: Mirrors `SERVICE_LABEL` / `PORT` in the script. Restated rather than parsed
#: for the reason above.
SERVICE_LABEL = "com.guild.serena"
PORT = "9121"

#: The header the dispatch `case` sits under. `render_service_definition` has
#: no subcommand, so the only way to drive the REAL function (rather than a
#: reimplementation of it) is to run the script with its dispatch replaced by a
#: generic caller. `DaemonHarness.render_service_definition` asserts this
#: marker was found, so a rename of the header degrades into a failure rather
#: than into a harness that silently tests nothing.
DISPATCH_MARKER = "# ── Dispatch ──"

#: A PID the stub `lsof` reports as holding the port, and the stub `ps` then
#: confirms as a Serena server. Any number works; it is never signalled.
FAKE_SERENA_PID = "424242"

#: What a real Serena answers an MCP `initialize` with. `serena_mcp_healthy`
#: requires BOTH `protocolVersion` and a `serverInfo` object before it returns
#: 0, so a body missing either is the wedged case instead.
MCP_HANDSHAKE_BODY = (
    '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",'
    '"capabilities":{},"serverInfo":{"name":"serena","version":"1.7.0"}}}'
)

#: A startup marker, verbatim in the part the awk scoper matches
#: (`/Initializing Serena MCP server/`). The `buf = ""` reset on this line is
#: what scopes the rung to the most recent startup.
STARTUP_MARKER_LINE = (
    "INFO  2026-09-15 22:35:45,001 [MainThread] serena.mcp:start:301 - "
    "Initializing Serena MCP server with config ..."
)

#: A project-load failure, copied from a real `~/.serena-daemon.log` rather
#: than invented. The SHAPE is load-bearing twice: the awk scoper matches
#: `Failed to load project configuration for `, and the reporting `sed` strips
#: `.*configuration for \\(.*\\): .*` to recover the path — which only recovers
#: the right substring because the REASON this daemon emits (`'languages'.
#: This project will be skipped. ...`) carries no `": "` of its own. An
#: invented one-word reason would pass a test the real line could fail.
_REAL_FAILURE_LINE = (
    "ERROR 2026-09-15 22:35:46,990 [MainThread] "
    "serena.config.serena_config:from_config_file:1064 - Failed to load "
    "project configuration for {path}: 'languages'. This project will be "
    "skipped. Fix or delete its .serena/project.yml (or remove it from "
    "serena_config.yml) to re-enable it."
)

#: Two DIFFERENT paths, so the two failure lines differ. Identical lines would
#: also trip doctor's (g) repeating-error dimension and `detect_crash_loop`,
#: putting a second unrelated finding in the output these tests read.
FAILED_PROJECT_A = "/tmp/serena-test-project-a"
FAILED_PROJECT_B = "/tmp/serena-test-project-b"


def _failure_line(path: str) -> str:
    return _REAL_FAILURE_LINE.format(path=path)


def _stub(path: Path, body: str) -> None:
    """Write one executable `/bin/sh` stub and make it runnable."""
    path.write_text("#!/bin/sh\n" + body.strip() + "\n", encoding="utf-8")
    path.chmod(0o755)


class DaemonHarness:
    """A throwaway `$HOME` and stub `$PATH`, plus runners bound to both."""

    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.bin = root / "stubbin"
        self.home.mkdir()
        self.bin.mkdir()
        (self.home / ".local" / "bin").mkdir(parents=True)
        self.uvx_argv_file = root / "uvx-argv.txt"

        # `uname` -> Darwin everywhere. See the module docstring: this pins the
        # launchd arm, whose plist is the XML surface the escape exists for, so
        # these tests read the same on macOS and on Linux CI.
        _stub(self.bin / "uname", "printf 'Darwin\\n'")

        # `launchctl` -> a failing no-op. doctor's (f) dimension and
        # detect_crash_loop both shell out to it; letting the REAL launchctl
        # answer would read this machine's actual job state into a test, and
        # `launchctl list` against a relocated HOME is meaningless anyway.
        # Exit 1 with no output is what the script reads as "not loaded".
        _stub(self.bin / "launchctl", "exit 1")

        # `uvx` in TWO places, resolved by two different mechanisms.
        # `resolve_service_tool` walks SERVICE_PATH_DIRS, whose first entry is
        # "$HOME/.local/bin" — without a uvx there it would fall through to the
        # REAL uvx on this machine (/opt/homebrew/bin, a mise shim, ...) and the
        # rendered plist would then differ between machines, which is exactly
        # what a drift comparison must not do. `cmd_start` instead takes uvx off
        # $PATH, hence the second copy.
        _stub(self.home / ".local" / "bin" / "uvx", "exit 0")
        _stub(self.bin / "uvx", "exit 0")

        self.serena_on_port(True)
        self.handshake_ok(True)

    # ── machine-state selectors ──────────────────────────────────────────────

    def serena_on_port(self, present: bool) -> None:
        """Stub `lsof` (and, through it, `ps`) into "Serena holds $PORT" or not.

        `serena_port_holder_pids` and `serena_port_owners_known` both consult
        `lsof` FIRST and never reach ss/fuser once it exists, so these two
        stubs settle the whole port-resolver chain.
        """
        if present:
            # Real `lsof -i :PORT -t` prints one PID per holder.
            _stub(self.bin / "lsof", f"printf '%s\\n' '{FAKE_SERENA_PID}'")
            # `serena_port_pids` confirms each holder by cmdline before
            # believing it is Serena, so the PID above needs a matching
            # `start-mcp-server` command line or it is filtered right back out.
            _stub(
                self.bin / "ps",
                'case "$*" in\n'
                '  *"-o command="*)\n'
                "    printf '%s\\n' "
                f"'/fake/bin/serena start-mcp-server --context claude-code "
                f"--transport streamable-http --port {PORT}' ;;\n"
                "esac\n"
                "exit 0",
            )
        else:
            # Real lsof exits 1 with no output when nothing holds the port.
            _stub(self.bin / "lsof", "exit 1")
            _stub(self.bin / "ps", "exit 0")

    def handshake_ok(self, healthy: bool) -> None:
        """Stub `curl` into answering a valid MCP handshake, or not.

        This is the ONE stub that stands in for the thing doctor actually
        measures, and it is unavoidable: a real handshake needs a real Serena.
        It is honest scaffolding rather than a shortcut — the subject of the
        tests that use it is the project-configs rung and the VERDICT
        PRECEDENCE above it, both of which are only reachable once health has
        already been established.
        """
        body = MCP_HANDSHAKE_BODY if healthy else '{"error":"no handshake"}'
        _stub(self.bin / "curl", f"printf '%s' '{body}'")

    def record_uvx_argv(self) -> None:
        """Swap the inert `uvx` for one that records the argv it was handed."""
        _stub(
            self.bin / "uvx",
            f"printf '%s\\n' \"$@\" > '{self.uvx_argv_file}'\nexit 0",
        )

    def write_log(self, lines: list[str]) -> None:
        (self.home / ".serena-daemon.log").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def install_rendered_plist(self) -> str:
        """Install exactly what the script renders, so `drift` reads `current`.

        doctor compares `$(render_service_definition "$os")` against the
        installed file, and `$( )` strips trailing newlines before `printf
        '%s\\n'` restores exactly one. The same normalisation is applied here so
        the comparison is the byte-identity it is meant to be — an artifact that
        differed by a newline would report `drifted` and exit 4, masking every
        verdict below it.
        """
        rendered = self.render_service_definition()
        artifact = self.home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(rendered.rstrip("\n") + "\n", encoding="utf-8")
        return rendered

    # ── runners ──────────────────────────────────────────────────────────────

    def env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "TERM": "dumb",
        }

    def run(self, *args: str, script: Path | None = None) -> subprocess.CompletedProcess:
        """Run the shipped script (or the hook) under the harness environment.

        Invoked by PATH-resolved shebang rather than by naming an interpreter,
        so what runs is what a user, launchd, or the SessionStart hook runs.
        """
        return subprocess.run(
            [str(script or DAEMON_SH), *args],
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=90,
        )

    def render_service_definition(self, platform: str = "Darwin") -> str:
        """Call the script's REAL `render_service_definition` and return stdout.

        The function has no subcommand of its own, so the script is copied with
        everything from the dispatch header onward replaced by `"$@"` — turning
        it into a generic "call the named function" entry point. Only the tail
        is dropped; every constant and every function body above it is the
        shipped one. The copy keeps a real on-disk path so `SCRIPT_PATH`'s
        `BASH_SOURCE` resolution still works.
        """
        source = DAEMON_SH.read_text(encoding="utf-8")
        head, marker, _ = source.partition(DISPATCH_MARKER)
        assert marker, (
            f"{DAEMON_SH.relative_to(REPO_ROOT)} no longer carries the "
            f"{DISPATCH_MARKER!r} header this harness truncates at. Without it "
            f"the whole script would be sourced, the dispatch would run "
            f"`usage; exit 1`, and every render-based test below would be "
            f"measuring nothing. Re-point this marker at the new header."
        )

        harness = self.home / "_render_harness.sh"
        harness.write_text(head + '\n"$@"\n', encoding="utf-8")
        harness.chmod(0o755)

        result = self.run("render_service_definition", platform, script=harness)
        assert result.returncode == 0, (
            f"render_service_definition {platform} exited "
            f"{result.returncode}; stderr: {result.stderr!r}"
        )
        return result.stdout


@pytest.fixture
def daemon(tmp_path: Path) -> DaemonHarness:
    return DaemonHarness(tmp_path)


def _program_arguments(rendered_plist: str) -> list[str]:
    return plistlib.loads(rendered_plist.encode("utf-8"))["ProgramArguments"]


def _value_after(argv: list[str], flag: str) -> str:
    assert flag in argv, f"{flag!r} is absent from {argv}"
    return argv[argv.index(flag) + 1]


# ── the plist: well-formed, and carrying the specifier intact ────────────────


def test_rendered_plist_parses_and_its_from_argument_is_the_exact_specifier(
    daemon: DaemonHarness,
) -> None:
    """The escape `218ccab` added, measured on its output rather than its spelling.

    A range specifier contains `<`, which is illegal in XML character data.
    Interpolated raw it rendered `<string>serena-agent>=1.7.0,<2.0.0</string>`,
    where `<2.0.0` opens what the parser reads as a tag — the plist stops being
    well-formed and launchd cannot load the job, at login, unattended. So this
    asserts two things a regex over the source cannot: that the bytes PARSE,
    and that the parse hands back the specifier with its `<` restored.
    """
    rendered = daemon.render_service_definition()

    argv = _program_arguments(rendered)

    assert _value_after(argv, "--from") == SERENA_SPECIFIER, (
        f"the plist's `--from` argument decodes to "
        f"{_value_after(argv, '--from')!r}, expected {SERENA_SPECIFIER!r}. "
        f"launchd passes this straight to uvx, so a value that survived XML "
        f"escaping in a mangled form resolves the wrong package — or no "
        f"package — on every RunAtLoad start."
    )

    assert "<lt;" not in rendered, (
        "the rendered plist contains `<lt;`, which is what "
        "`${var//<//&lt;}` emits under bash 5.2: a bare `&` in a "
        "pattern-substitution REPLACEMENT is read as the matched text, so the "
        "obvious parameter-expansion spelling silently half-escapes. The "
        "escape must stay the `sed` form."
    )


def test_the_plist_escape_check_actually_bites(daemon: DaemonHarness) -> None:
    """The guard on the guard: prove an unescaped `<` really does fail the parse.

    The test above is an equality that passes when nothing is wrong, which is
    also what a broken parse would do — `plistlib.loads` silently accepting
    malformed input, or `_program_arguments` reading a key that is no longer
    there, would leave it green while measuring nothing. So the exact
    historical failure is reproduced here, in memory, and asserted to be
    REJECTED. Without this, the escape could be deleted from the script
    tomorrow and only this file would be able to say so.
    """
    rendered = daemon.render_service_definition()

    assert "&lt;" in rendered, (
        "the rendered plist carries no `&lt;` at all, so there is nothing for "
        "this check to un-escape — the specifier lost its `<`, or the render "
        "stopped interpolating SERENA_PKG"
    )

    # `<lt;` is exactly what the bash-5.2 parameter-expansion spelling emitted.
    half_escaped = rendered.replace("&lt;", "<lt;")
    with pytest.raises((ExpatError, plistlib.InvalidFileException, ValueError)):
        plistlib.loads(half_escaped.encode("utf-8"))

    # And the fully-raw form the escape was introduced to prevent.
    unescaped = rendered.replace("&lt;", "<")
    with pytest.raises((ExpatError, plistlib.InvalidFileException, ValueError)):
        plistlib.loads(unescaped.encode("utf-8"))


def test_the_floor_excludes_the_1_6_x_that_could_not_read_a_1_7_x_config(
    daemon: DaemonHarness,
) -> None:
    """What the range MEANS, not which characters it is spelled with.

    Pinning the literal string alone is brittle — a legitimate ceiling bump to
    `<3.0.0` would fail it for no reason. What actually matters is the FLOOR:
    1.6.1 declares `FIELDS_WITHOUT_DEFAULTS = {"project_name", "languages"}`
    and reads `data["languages"]` unguarded, so it raises `KeyError:
    'languages'` on every project.yml a 1.7.x serena wrote, skips the project,
    and serves a daemon with `known projects: []`. 1.7.0 carries the rename in
    `RENAMED_FIELDS` and reads both shapes. So the floor must be at least 1.7.0
    and must exclude 1.6.x.

    The specifier is read back out of the DECODED plist rather than out of the
    script's source, so this is a statement about what launchd is handed.
    """
    specifier = _value_after(_program_arguments(daemon.render_service_definition()), "--from")

    package, _, constraints = specifier.partition(">=")
    assert package == "serena-agent", (
        f"the `--from` argument names package {package!r}; expected "
        f"`serena-agent` introduced by a `>=` floor"
    )

    floor_text, _, ceiling_text = constraints.partition(",<")
    assert ceiling_text, (
        f"{specifier!r} carries no `,<` ceiling. The ceiling is not decoration: "
        f"a 2.x may rename the config fields again, and an unattended login "
        f"start is the worst place to discover it."
    )

    floor = tuple(int(part) for part in floor_text.split("."))
    ceiling = tuple(int(part) for part in ceiling_text.split("."))

    assert floor >= (1, 7, 0), (
        f"the floor is {floor_text}, below 1.7.0. Anything in 1.6.x raises "
        f"KeyError: 'languages' on a project.yml written by a 1.7.x serena, "
        f"skips the project, and leaves a daemon that answers the handshake "
        f"and resolves no symbols — the exact state D-001 was filed for."
    )
    assert floor < ceiling, (
        f"the range {specifier!r} is empty: floor {floor_text} is not below "
        f"ceiling {ceiling_text}, so uvx can resolve nothing and every daemon "
        f"start fails"
    )


# ── the two launch sites, compared as argv rather than as text ───────────────


def test_both_launch_sites_hand_uvx_an_identical_argument_vector(
    daemon: DaemonHarness,
) -> None:
    """`cmd_start` and the plist must not diverge — that divergence IS the incident.

    The script says so in both places: "Launch site 1 of 2 ... the two carry an
    IDENTICAL flag set — a divergence between them is what produced the
    original incident." Nothing enforced it. A source-text check would only
    show both sites mention `$SERENA_PKG`, which is true even when one of them
    TRANSFORMS it — and transforming it at one site is precisely what the XML
    escape does.

    So both vectors are observed rather than read: the plist's from its decoded
    `ProgramArguments`, and `cmd_start`'s from a `uvx` stub that records the
    argv it is handed. `cmd_start`'s exit status is deliberately not asserted —
    the stub exits immediately, so the liveness re-check two seconds later
    correctly reports a dead launcher. The argv was already written by then,
    and the argv is the subject.
    """
    plist_argv = _program_arguments(daemon.render_service_definition())

    # Nothing on the port, so cmd_start launches instead of adopting.
    daemon.serena_on_port(False)
    daemon.record_uvx_argv()
    daemon.run("start")

    assert daemon.uvx_argv_file.is_file(), (
        "cmd_start never invoked uvx, so there is no launch vector to compare. "
        "It adopted a daemon it believed was already on the port, refused the "
        "port as contended, or failed before step 4."
    )
    start_argv = daemon.uvx_argv_file.read_text(encoding="utf-8").splitlines()

    # plist_argv[0] is the resolved uvx path — launchd is handed the binary as
    # its own element, where cmd_start reaches it through $PATH. Everything
    # after it is the flag set the two sites must agree on.
    assert plist_argv[1:] == start_argv, (
        f"the two launch sites disagree.\n"
        f"  plist ProgramArguments (after the uvx path): {plist_argv[1:]}\n"
        f"  cmd_start's actual uvx argv:                 {start_argv}\n"
        f"A flag added at one site and not the other means a daemon started at "
        f"login behaves differently from one started by hand — which is the "
        f"original incident this pairing is documented to prevent."
    )
    assert _value_after(start_argv, "--from") == SERENA_SPECIFIER, (
        f"cmd_start handed uvx --from "
        f"{_value_after(start_argv, '--from')!r}, expected {SERENA_SPECIFIER!r}"
    )


def test_usage_reports_the_same_specifier_it_launches_with(
    daemon: DaemonHarness,
) -> None:
    """The third, human-facing copy — the one a reader trusts when diagnosing.

    `usage()` prints a DISPLAY copy of the daemon command line, and the script
    notes that this block "is the one block in this script whose staleness a
    user sees directly". A stale one sends whoever is debugging a failed start
    after the wrong package.
    """
    result = daemon.run("no-such-subcommand")

    assert result.returncode == 1, (
        f"an unrecognised subcommand exited {result.returncode}; the dispatch's "
        f"default arm is `usage; exit 1`"
    )
    daemon_lines = [
        line for line in result.stdout.splitlines() if line.startswith("Daemon command:")
    ]
    assert len(daemon_lines) == 1, (
        f"usage printed {len(daemon_lines)} `Daemon command:` lines; expected "
        f"exactly one. stdout: {result.stdout!r}"
    )

    shown = daemon_lines[0].split(" --from ", 1)[1].split(" serena ", 1)[0]
    assert shown == SERENA_SPECIFIER, (
        f"usage advertises `uvx --from {shown}` while the launch sites use "
        f"{SERENA_SPECIFIER!r}. The display copy has gone stale."
    )


# ── doctor's project-configs rung, on both arms ──────────────────────────────


def _healthy_installed(daemon: DaemonHarness) -> None:
    """Put the harness in the state every rung BELOW health has to pass through.

    doctor's precedence is 1 > 2 > 3 > 4 > 0, and the project-configs rung sits
    under the two health checks, so it is only ever reached on a daemon that is
    installed and answering. Anything less and these tests would be asserting
    exit 3 for the wrong reason entirely — a stopped daemon exits 2, and a
    drifted definition exits 4 before the green arm can reach 0.
    """
    daemon.serena_on_port(True)
    daemon.handshake_ok(True)
    daemon.install_rendered_plist()


def test_doctor_exits_3_when_a_project_failed_to_load_on_the_last_startup(
    daemon: DaemonHarness,
) -> None:
    """The rung `218ccab` added, on the arm that fires.

    Before it, a Serena that loaded ZERO projects passed every dimension doctor
    measured — it binds the port and answers a valid MCP handshake — and then
    failed every symbol request with `known projects: []`. This machine ran an
    entire foundry run in that state with `verdict: healthy` on screen.
    """
    _healthy_installed(daemon)
    daemon.write_log(
        [
            STARTUP_MARKER_LINE,
            _failure_line(FAILED_PROJECT_A),
            _failure_line(FAILED_PROJECT_B),
        ]
    )

    result = daemon.run("doctor")

    assert result.returncode == 3, (
        f"doctor exited {result.returncode} on a daemon that answered the "
        f"handshake and loaded no project; expected 3 "
        f"(running-but-unhealthy).\nstdout:\n{result.stdout}"
    )
    assert "2 FAILED to load" in result.stdout, (
        f"the project-configs line does not report both failures.\n"
        f"stdout:\n{result.stdout}"
    )
    # The reporting `sed` must recover the PATH and not drag the reason along
    # with it. The reason this daemon emits carries no `": "`, which is the only
    # thing that makes the greedy capture land correctly — so this asserts the
    # extracted value is the path EXACTLY, never merely that it contains it.
    configs_line = next(
        line for line in result.stdout.splitlines() if "project configs" in line
    )
    reported = configs_line.split("FAILED to load: ", 1)[1].strip()
    assert reported == f"{FAILED_PROJECT_A} {FAILED_PROJECT_B}", (
        f"the project-configs line names {reported!r}; expected the two paths "
        f"alone. The `s/.*configuration for \\(.*\\): .*/\\1/` capture is "
        f"greedy, so a reason containing `\": \"` would drag part of it into "
        f"the project name."
    )


def test_doctor_exits_3_for_a_wedged_daemon_too_so_the_rung_added_no_new_code(
    daemon: DaemonHarness,
) -> None:
    """The rung reuses exit 3; it does not introduce a sixth code.

    That matters outside this script. The code is machine-consumed in two
    places — `hooks/session-start-serena.sh` branches on it, and
    `scripts/setup-foundry.sh` maps it to the `FOUNDRY_SERENA_HEALTH` token the
    foundry preflight records — and both have a default arm for anything
    outside 0-4. A sixth code would silently degrade to "unrecognised status"
    in the hook and to `UNKNOWN` in the preflight.
    """
    daemon.serena_on_port(True)
    daemon.handshake_ok(False)  # something holds the port, nothing answers
    daemon.install_rendered_plist()
    daemon.write_log([STARTUP_MARKER_LINE])

    result = daemon.run("doctor")

    assert result.returncode == 3, (
        f"the wedged-daemon case exited {result.returncode}; expected the same "
        f"3 the project-configs rung reuses.\nstdout:\n{result.stdout}"
    )


def test_doctor_exits_0_when_the_failures_predate_the_last_startup(
    daemon: DaemonHarness,
) -> None:
    """The green arm — and the whole reason for the `buf = ""` reset.

    A check that can only ever go red is noise a reader learns to skip, which
    is the fate of any rung that keeps firing on a failure fixed three restarts
    ago. So the same failure lines are present in the log here, ABOVE the most
    recent startup marker, and doctor must report clean. Testing only the
    failing arm would leave a rung that never goes green looking correct.
    """
    _healthy_installed(daemon)
    daemon.write_log(
        [
            STARTUP_MARKER_LINE,
            _failure_line(FAILED_PROJECT_A),
            _failure_line(FAILED_PROJECT_B),
            # A later restart, after the configs were repaired.
            STARTUP_MARKER_LINE,
            "INFO  2026-09-15 23:01:02,003 [MainThread] serena.agent:_activate:88 "
            "- Activating project guild",
        ]
    )

    result = daemon.run("doctor")

    assert result.returncode == 0, (
        f"doctor exited {result.returncode} on a log whose only project "
        f"failures predate the most recent startup; expected 0.\n"
        f"stdout:\n{result.stdout}"
    )
    assert "all loaded on the last startup" in result.stdout, (
        f"the project-configs line did not report clean.\nstdout:\n{result.stdout}"
    )
    assert FAILED_PROJECT_A not in result.stdout, (
        f"doctor named {FAILED_PROJECT_A}, a project whose failure predates "
        f"the last startup marker — the awk `buf = \"\"` reset is not scoping "
        f"to the most recent startup.\nstdout:\n{result.stdout}"
    )


def test_doctor_does_not_scan_project_configs_when_there_is_no_log(
    daemon: DaemonHarness,
) -> None:
    """A missing log is "not measured", never "measured and clean" — nor a failure.

    The count is initialised to 0 and the gather block is guarded on the log
    existing, so a machine with no log at all must fall through to the healthy
    verdict while SAYING it could not look. Reporting clean would be a lie; a
    verdict of 3 would be worse.
    """
    _healthy_installed(daemon)
    assert not (daemon.home / ".serena-daemon.log").exists()

    result = daemon.run("doctor")

    assert result.returncode == 0, (
        f"doctor exited {result.returncode} with no log file present; expected "
        f"0.\nstdout:\n{result.stdout}"
    )
    assert "not scanned" in result.stdout, (
        f"the project-configs line claims an answer it could not have "
        f"reached.\nstdout:\n{result.stdout}"
    )


# ── the adjacent path: the SessionStart hook consuming that exit code ────────


def test_the_session_start_hook_warns_the_model_on_an_unloadable_project(
    daemon: DaemonHarness,
) -> None:
    """ADJACENT PATH — a different consumer, reached by a different transition.

    The tests above drive `doctor` directly. This one drives
    `hooks/session-start-serena.sh`, which is the first automatic caller on
    every session start and branches on the very exit code the project-configs
    rung now feeds. It is the path that decides whether the MODEL is told
    Serena cannot serve — and during the D-001 incident it was told nothing,
    because doctor returned 0.

    It is also the reason the rung had to reuse exit 3 rather than add a code:
    the hook's default arm treats anything outside 0-4 as "unrecognised
    status", so a sixth code would have produced a vaguer warning, not a better
    one.

    The hook never fails a session, so its exit status is 0 on every path; what
    is asserted is the `additionalContext` it emits.
    """
    _healthy_installed(daemon)
    daemon.write_log([STARTUP_MARKER_LINE, _failure_line(FAILED_PROJECT_A)])

    result = daemon.run(script=SESSION_START_HOOK)

    assert result.returncode == 0, (
        f"the SessionStart hook exited {result.returncode}; it must never fail "
        f"a session.\nstderr:\n{result.stderr}"
    )
    assert "additionalContext" in result.stdout, (
        f"the hook emitted no context envelope for a daemon that cannot serve "
        f"symbols, so the model would proceed believing Serena works.\n"
        f"stdout:\n{result.stdout!r}"
    )
    assert "do not report any symbol as LSP-verified" in result.stdout, (
        f"the hook's warning does not tell the model to stop trusting symbol "
        f"results. That instruction is the entire remedy for this state: the "
        f"daemon answers, so every Serena call fails silently rather than "
        f"loudly.\nstdout:\n{result.stdout!r}"
    )


def test_the_session_start_hook_stays_silent_when_every_project_loaded(
    daemon: DaemonHarness,
) -> None:
    """The same adjacent path on its green arm.

    The hook fires on EVERY session start on every machine, so a healthy one
    must inject nothing at all — an unconditional line here would be noise in
    every session forever. Asserting only the warning arm would leave a hook
    that always warns looking correct.
    """
    _healthy_installed(daemon)
    daemon.write_log(
        [
            _failure_line(FAILED_PROJECT_A),
            STARTUP_MARKER_LINE,
            "INFO  2026-09-15 23:01:02,003 [MainThread] serena.agent:_activate:88 "
            "- Activating project guild",
        ]
    )

    result = daemon.run(script=SESSION_START_HOOK)

    assert result.returncode == 0, (
        f"the SessionStart hook exited {result.returncode} on a healthy "
        f"machine.\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip() == "", (
        f"the hook injected context on a healthy session start: "
        f"{result.stdout!r}"
    )
