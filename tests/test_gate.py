#!/usr/bin/env python3
"""Self-contained regression tests for install/token_optimizer_gate.py.

Runs the gate script as a real subprocess with crafted stdin JSON and checks
exit codes, exactly mirroring how Claude Code's PreToolUse hook invokes it.
No external dependencies beyond the stdlib. Run with:

    python3 tests/test_gate.py

The suite also covers install/_merge_settings.py and
install/_unmerge_settings.py's dual-install-path coexistence detection,
rebuilt around the REAL signals Claude Code actually exposes (settings.json's
"enabledPlugins" map, and a plugins-cache directory for the plugin) rather
than scanning hooks.PreToolUse for a plugin-cache-shaped path - Claude Code
never merges a plugin's own hooks into settings.json, so that old scan could
never find anything (that was Finding 15; the plugin-coexistence warning had
never fired on any real install):

- tests 12/13 check that _merge_settings.py warns on stderr (but still
  installs, rc=0) when enabledPlugins or a plugins-cache directory signals
  the plugin is present.
- test 14 is the critical regression guard: with NEITHER signal present (a
  genuinely plugin-free settings.json), no warning fires. The old code also
  passed this case, but for the wrong reason - it never looked anywhere
  real - which is exactly how Finding 15 shipped with passing tests.
- tests 15/16/17 are the equivalent coverage on the uninstall side
  (_unmerge_settings.py's matching note, printed to stdout not stderr).
- test 18 covers Finding 16: uninstall.sh must not abort when stdin isn't a
  terminal (e.g. piped from /dev/null) - it should skip the interactive
  prompt, say so, and still finish with exit code 0.
- test 19 covers Finding 17: uninstalling must restore settings.json to its
  exact pre-install shape (no "hooks" key) when there was no "hooks" key
  before install, rather than leaving behind an empty
  {"hooks": {"PreToolUse": []}}.

Exits 0 if all tests pass, non-zero if any fail (CI-friendly).
"""
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)

GATES = {
    "standalone": (os.path.join(REPO_ROOT, "install", "token_optimizer_gate.py"), False),
    "plugin": (os.path.join(REPO_ROOT, "hooks", "gate.py"), True),
}

results = []


def run_gate(payload, env_overrides=None, variant="standalone", state_dir=None):
    """Run the given gate script variant with the given payload dict as
    stdin JSON. Returns (returncode, stdout, stderr)."""
    script, takes_state_dir = GATES[variant]
    argv = [sys.executable, script]
    if takes_state_dir and state_dir:
        argv += ["--state-dir", state_dir]
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        argv,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout, proc.stderr


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((name, status, detail))
    print("[{0}] {1}{2}".format(status, name, (" - " + detail) if (detail and not condition) else ""))


def fresh_home():
    """Create a fresh temp HOME with ~/.claude already present, isolated from
    the real developer machine's ~/.claude."""
    tmp = tempfile.TemporaryDirectory()
    claude_dir = os.path.join(tmp.name, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    return tmp, claude_dir


def fresh_plugin_state_dir():
    """Create a fresh temp dir to pass as --state-dir to the plugin variant."""
    tmp = tempfile.TemporaryDirectory()
    return tmp, tmp.name


def test_1_top_level_bash_denied():
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            state_tmp, state_dir = fresh_plugin_state_dir()
            with state_tmp:
                rc, out, err = run_gate(
                    {"tool_name": "Bash", "session_id": "s1", "tool_input": {"command": "ls"}},
                    env_overrides={"HOME": tmp.name},
                    variant=variant,
                    state_dir=state_dir,
                )
                check(
                    "1. [{0}] top-level Bash (no agent_type) -> exit 2".format(variant),
                    rc == 2,
                    "got rc={0}".format(rc),
                )


def test_2_subagent_bash_allowed():
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            state_tmp, state_dir = fresh_plugin_state_dir()
            with state_tmp:
                rc, out, err = run_gate(
                    {
                        "tool_name": "Bash",
                        "agent_type": "runner",
                        "session_id": "s2",
                        "tool_input": {"command": "ls"},
                    },
                    env_overrides={"HOME": tmp.name},
                    variant=variant,
                    state_dir=state_dir,
                )
                check(
                    "2. [{0}] subagent Bash (agent_type=runner) -> exit 0".format(variant),
                    rc == 0,
                    "got rc={0}".format(rc),
                )


def test_3_free_budget_reads_allowed():
    """Up to 3 free direct Reads per session must all be allowed."""
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            state_tmp, state_dir = fresh_plugin_state_dir()
            with state_tmp:
                env = {"HOME": tmp.name}
                session_id = "s3-fresh"
                rcs = []
                for i in range(3):
                    rc, out, err = run_gate(
                        {
                            "tool_name": "Read",
                            "session_id": session_id,
                            "tool_input": {"file_path": "/tmp/x{0}".format(i)},
                        },
                        env_overrides=env,
                        variant=variant,
                        state_dir=state_dir,
                    )
                    rcs.append(rc)
                check(
                    "3. [{0}] first 3 top-level Reads in a fresh session -> all exit 0".format(variant),
                    all(rc == 0 for rc in rcs),
                    "got rcs={0}".format(rcs),
                )


def test_4_read_beyond_budget_denied():
    """The 4th direct Read in the same session, after the free budget of 3
    is used up, must be denied."""
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            state_tmp, state_dir = fresh_plugin_state_dir()
            with state_tmp:
                env = {"HOME": tmp.name}
                session_id = "s4-repeat"
                rcs = []
                for i in range(3):
                    rc, _, _ = run_gate(
                        {
                            "tool_name": "Read",
                            "session_id": session_id,
                            "tool_input": {"file_path": "/tmp/x{0}".format(i)},
                        },
                        env_overrides=env,
                        variant=variant,
                        state_dir=state_dir,
                    )
                    rcs.append(rc)
                rc4, out4, err4 = run_gate(
                    {"tool_name": "Read", "session_id": session_id, "tool_input": {"file_path": "/tmp/y"}},
                    env_overrides=env,
                    variant=variant,
                    state_dir=state_dir,
                )
                check(
                    "4. [{0}] 4th top-level Read, same session, beyond budget -> exit 2".format(variant),
                    all(rc == 0 for rc in rcs) and rc4 == 2,
                    "got rcs={0} rc4={1}".format(rcs, rc4),
                )


def test_5_write_real_settings_allowed():
    tmp, claude_dir = fresh_home()
    with tmp:
        real_settings = os.path.join(claude_dir, "settings.json")
        rc, out, err = run_gate(
            {
                "tool_name": "Write",
                "session_id": "s5",
                "tool_input": {"file_path": real_settings},
            },
            env_overrides={"HOME": tmp.name},
        )
        check(
            "5. Write to the real ~/.claude/settings.json -> exit 0 (self-repair)",
            rc == 0,
            "got rc={0}, path={1}".format(rc, real_settings),
        )


def test_6_malformed_json_fails_open():
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            state_tmp, state_dir = fresh_plugin_state_dir()
            with state_tmp:
                env = os.environ.copy()
                env["HOME"] = tmp.name
                script, takes_state_dir = GATES[variant]
                argv = [sys.executable, script]
                if takes_state_dir:
                    argv += ["--state-dir", state_dir]
                proc = subprocess.run(
                    argv,
                    input="{not valid json!!",
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=10,
                )
                check(
                    "6. [{0}] malformed JSON input -> exit 0 (fail open)".format(variant),
                    proc.returncode == 0,
                    "got rc={0}".format(proc.returncode),
                )


def test_7_session_id_path_traversal():
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            env = {"HOME": tmp.name}
            state_tmp, plugin_state_dir = fresh_plugin_state_dir()
            with state_tmp:
                state_dir = (
                    os.path.join(claude_dir, ".token-optimizer-state")
                    if variant == "standalone"
                    else plugin_state_dir
                )
                malicious_ids = ["../../evil", "/etc/passwd", "../../../../tmp/evil"]
                all_ok = True
                detail = ""
                for sid in malicious_ids:
                    rc, out, err = run_gate(
                        {"tool_name": "Read", "session_id": sid, "tool_input": {"file_path": "/tmp/x"}},
                        env_overrides=env,
                        variant=variant,
                        state_dir=plugin_state_dir,
                    )
                    # The gate must hash the session_id into a fixed-width
                    # filename inside state_dir - not leak it into the path
                    # some other way. Assert the EXACT expected filename
                    # exists, and that nothing else stray is in state_dir.
                    expected = hashlib.sha256(sid.encode("utf-8", "surrogatepass")).hexdigest()[:24] + ".count"
                    path = os.path.join(state_dir, expected)
                    if not os.path.isfile(path):
                        all_ok = False
                        detail = "[{0}] expected hashed file missing for {1!r}".format(variant, sid)
                    if os.path.isdir(state_dir):
                        stray = [f for f in os.listdir(state_dir) if not f.endswith((".count", ".tmp"))]
                        if stray:
                            all_ok = False
                            detail = "[{0}] unexpected entries: {1}".format(variant, stray)
                    # Also confirm nothing was written directly outside the
                    # state dir entirely (e.g. a literal "/etc/passwd.count"
                    # at filesystem root).
                    suspicious = "/etc/passwd.count"
                    if os.path.exists(suspicious):
                        all_ok = False
                        detail = "[{0}] created {1} outside state dir".format(variant, suspicious)
                check(
                    "7. [{0}] session_id path traversal -> hashed filename only, no stray/escaped files".format(variant),
                    all_ok,
                    detail,
                )


def test_8_fake_settings_path_denied():
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            state_tmp, state_dir = fresh_plugin_state_dir()
            with state_tmp:
                fake_settings = "/tmp/fake_settings.json"
                rc, out, err = run_gate(
                    {
                        "tool_name": "Write",
                        "session_id": "s8",
                        "tool_input": {"file_path": fake_settings},
                    },
                    env_overrides={"HOME": tmp.name},
                    variant=variant,
                    state_dir=state_dir,
                )
                check(
                    "8. [{0}] Write to a path merely ending in settings.json (not real) -> exit 2".format(variant),
                    rc == 2,
                    "got rc={0}".format(rc),
                )


def test_9_plugin_denies_write_to_real_settings():
    """Locks in the deliberate design difference: unlike the standalone
    variant, the plugin variant has no self-repair carve-out at all, so a
    Write to what would be the real ~/.claude/settings.json path must still
    be denied."""
    tmp, claude_dir = fresh_home()
    with tmp:
        state_tmp, state_dir = fresh_plugin_state_dir()
        with state_tmp:
            real_settings = os.path.join(claude_dir, "settings.json")
            rc, out, err = run_gate(
                {
                    "tool_name": "Write",
                    "session_id": "s9",
                    "tool_input": {"file_path": real_settings},
                },
                env_overrides={"HOME": tmp.name},
                variant="plugin",
                state_dir=state_dir,
            )
            check(
                "9. [plugin] Write to the real ~/.claude/settings.json path -> exit 2 (no self-repair carve-out)",
                rc == 2,
                "got rc={0}, path={1}".format(rc, real_settings),
            )


def test_10_corrupt_counter_self_heals():
    """Regression test for Findings 2 & 3: a corrupted (non-numeric) counter
    state file must not permanently disable gating for that session. The
    first post-corruption call should read the garbage back as 0, allow the
    call, and rewrite the state file as a valid "1". With the free budget
    now at 3, two more calls for the same session must still be allowed
    (count becomes 2, then 3), and the call after that must deny, proving
    the gate re-engages at the budget boundary instead of staying stuck
    open."""
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            env = {"HOME": tmp.name}
            state_tmp, plugin_state_dir = fresh_plugin_state_dir()
            with state_tmp:
                state_dir = (
                    os.path.join(claude_dir, ".token-optimizer-state")
                    if variant == "standalone"
                    else plugin_state_dir
                )
                session_id = "s10-corrupt"

                # Pre-create the state dir and write literal garbage into the
                # same hashed state filename the gate itself would use.
                os.makedirs(state_dir, exist_ok=True)
                expected = hashlib.sha256(
                    session_id.encode("utf-8", "surrogatepass")
                ).hexdigest()[:24] + ".count"
                state_file = os.path.join(state_dir, expected)
                with open(state_file, "w") as fd:
                    fd.write("not-a-number")

                rc1, out1, err1 = run_gate(
                    {"tool_name": "Read", "session_id": session_id, "tool_input": {"file_path": "/tmp/x"}},
                    env_overrides=env,
                    variant=variant,
                    state_dir=plugin_state_dir,
                )
                try:
                    with open(state_file, "r") as fd:
                        healed_value = fd.read().strip()
                except Exception:
                    healed_value = None

                rc2, out2, err2 = run_gate(
                    {"tool_name": "Grep", "session_id": session_id, "tool_input": {"pattern": "x"}},
                    env_overrides=env,
                    variant=variant,
                    state_dir=plugin_state_dir,
                )
                rc3, out3, err3 = run_gate(
                    {"tool_name": "Grep", "session_id": session_id, "tool_input": {"pattern": "y"}},
                    env_overrides=env,
                    variant=variant,
                    state_dir=plugin_state_dir,
                )
                rc4, out4, err4 = run_gate(
                    {"tool_name": "Grep", "session_id": session_id, "tool_input": {"pattern": "z"}},
                    env_overrides=env,
                    variant=variant,
                    state_dir=plugin_state_dir,
                )

                check(
                    "10. [{0}] corrupt counter self-heals: rc1==0, healed to \"1\", "
                    "rc2==0, rc3==0, rc4==2 at budget boundary".format(variant),
                    rc1 == 0 and healed_value == "1" and rc2 == 0 and rc3 == 0 and rc4 == 2,
                    "got rc1={0} healed_value={1!r} rc2={2} rc3={3} rc4={4}".format(
                        rc1, healed_value, rc2, rc3, rc4
                    ),
                )


def test_11_concurrent_writes_no_torn_state_file():
    """Regression test for Finding 3: many gate processes racing to
    check-then-increment the SAME session's counter concurrently must never
    leave the state file torn/corrupted. The atomic os.replace() swap means
    the file is always either fully the old content or fully the new
    content, never a partial write - so this test asserts the file stays
    PARSEABLE as an integer after the race, not that the final count is
    exactly precise (true concurrent increments can legitimately race on
    the exact value; only non-corruption is being asserted here)."""
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            env = {"HOME": tmp.name}
            state_tmp, plugin_state_dir = fresh_plugin_state_dir()
            with state_tmp:
                state_dir = (
                    os.path.join(claude_dir, ".token-optimizer-state")
                    if variant == "standalone"
                    else plugin_state_dir
                )
                session_id = "s11-concurrent"
                expected = hashlib.sha256(
                    session_id.encode("utf-8", "surrogatepass")
                ).hexdigest()[:24] + ".count"
                state_file = os.path.join(state_dir, expected)

                def fire_one(i):
                    return run_gate(
                        {
                            "tool_name": "Read",
                            "session_id": session_id,
                            "tool_input": {"file_path": "/tmp/x{0}".format(i)},
                        },
                        env_overrides=env,
                        variant=variant,
                        state_dir=plugin_state_dir,
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                    list(pool.map(fire_one, range(10)))

                parseable = True
                detail = ""
                try:
                    with open(state_file, "r") as fd:
                        int(fd.read().strip())
                except Exception as exc:
                    parseable = False
                    detail = "[{0}] state file not parseable as int after concurrent writes: {1}".format(
                        variant, exc
                    )

                check(
                    "11. [{0}] concurrent writes to shared session -> state file stays parseable (no torn write)".format(
                        variant
                    ),
                    parseable,
                    detail,
                )


STANDALONE_HOOK_PATH = os.path.join(REPO_ROOT, "install", "token_optimizer_gate.py")


def _standalone_hook_entry():
    return {
        "matcher": "Bash|Write|Edit|NotebookEdit|Read|Grep",
        "hooks": [
            {
                "type": "command",
                "command": sys.executable,
                "args": [STANDALONE_HOOK_PATH],
            }
        ],
    }


def _make_plugin_cache_dir(claude_dir, marketplace="some-marketplace"):
    """Create an empty <claude_dir>/plugins/cache/<marketplace>/
    token-optimizer-plugin/ directory - the real on-disk signal a native
    `/plugin install` leaves behind, per Finding 15."""
    path = os.path.join(claude_dir, "plugins", "cache", marketplace, "token-optimizer-plugin")
    os.makedirs(path, exist_ok=True)
    return path


def test_12_merge_warns_when_plugin_enabled_in_settings():
    """Finding 15 fix, signal 1: when settings.json's "enabledPlugins" map
    registers token-optimizer-plugin (the key Claude Code actually writes,
    e.g. "token-optimizer-plugin@some-marketplace"), install/_merge_settings.py
    must print a non-fatal warning to stderr while still proceeding with the
    install (exit code 0). This is a REAL signal, unlike the old (Finding 15)
    hooks.PreToolUse scan, which Claude Code never populates for a plugin."""
    tmp, claude_dir = fresh_home()
    with tmp:
        settings_file = os.path.join(claude_dir, "settings.json")
        settings = {"enabledPlugins": {"token-optimizer-plugin@some-marketplace": True}}
        with open(settings_file, "w") as f:
            json.dump(settings, f)

        merge_script = os.path.join(REPO_ROOT, "install", "_merge_settings.py")
        proc = subprocess.run(
            [sys.executable, merge_script, settings_file, sys.executable],
            capture_output=True,
            text=True,
            timeout=10,
        )

        check(
            "12. _merge_settings.py warns on stderr + still installs (rc=0) when "
            "enabledPlugins registers the plugin",
            proc.returncode == 0
            and "token-optimizer-plugin" in proc.stderr
            and "also appears to be installed" in proc.stderr,
            "rc={0} stderr={1!r}".format(proc.returncode, proc.stderr),
        )


def test_13_merge_warns_when_plugin_in_cache():
    """Finding 15 fix, signal 2: when a plugins-cache directory for
    token-optimizer-plugin exists on disk (settings.json otherwise has
    neither signal - may not even exist), install/_merge_settings.py must
    still print the warning and still install successfully."""
    tmp, claude_dir = fresh_home()
    with tmp:
        _make_plugin_cache_dir(claude_dir)
        settings_file = os.path.join(claude_dir, "settings.json")  # does not exist yet

        merge_script = os.path.join(REPO_ROOT, "install", "_merge_settings.py")
        proc = subprocess.run(
            [sys.executable, merge_script, settings_file, sys.executable],
            capture_output=True,
            text=True,
            timeout=10,
        )

        check(
            "13. _merge_settings.py warns on stderr + still installs (rc=0) when "
            "a plugins-cache directory for the plugin exists",
            proc.returncode == 0
            and "token-optimizer-plugin" in proc.stderr
            and "also appears to be installed" in proc.stderr,
            "rc={0} stderr={1!r}".format(proc.returncode, proc.stderr),
        )


def test_14_merge_silent_when_plugin_absent():
    """THE critical regression guard for Finding 15: with NEITHER signal
    present - no settings.json at all, so certainly no "enabledPlugins"
    entry, and no plugins-cache directory for this plugin - this is the
    real-world "plugin genuinely not installed" case, and
    _merge_settings.py must NOT print the plugin-coexistence warning. The
    OLD code also passed this exact case, but for the wrong reason (it
    scanned hooks.PreToolUse, which a plugin install never populates), which
    is exactly how Finding 15 shipped silently broken behind 4 passing
    tests. This test only means something because tests 12/13 above prove
    the warning CAN fire on the real signals."""
    tmp, claude_dir = fresh_home()
    with tmp:
        settings_file = os.path.join(claude_dir, "settings.json")  # does not exist
        merge_script = os.path.join(REPO_ROOT, "install", "_merge_settings.py")
        proc = subprocess.run(
            [sys.executable, merge_script, settings_file, sys.executable],
            capture_output=True,
            text=True,
            timeout=10,
        )

        check(
            "14. _merge_settings.py stays silent (no plugin-coexistence warning) "
            "when genuinely neither real signal is present",
            proc.returncode == 0 and "token-optimizer-plugin" not in proc.stderr,
            "rc={0} stderr={1!r}".format(proc.returncode, proc.stderr),
        )


def test_15_unmerge_notes_when_plugin_enabled_in_settings():
    """Uninstall-side counterpart to test 12: settings.json has both the
    standalone gate's hook entry (the thing being removed) and an
    "enabledPlugins" entry registering token-optimizer-plugin. After
    removing the standalone entry, _unmerge_settings.py must still succeed
    (rc=0) and must note that the plugin still appears to be installed -
    that note is printed with a plain print(), i.e. to stdout, unlike
    _merge_settings.py's stderr warning (checked against the real output
    below rather than assumed, per the prior round's lesson that the two
    sides are not symmetric)."""
    tmp, claude_dir = fresh_home()
    with tmp:
        settings_file = os.path.join(claude_dir, "settings.json")
        settings = {
            "hooks": {"PreToolUse": [_standalone_hook_entry()]},
            "enabledPlugins": {"token-optimizer-plugin@some-marketplace": True},
        }
        with open(settings_file, "w") as f:
            json.dump(settings, f)

        unmerge_script = os.path.join(REPO_ROOT, "install", "_unmerge_settings.py")
        proc = subprocess.run(
            [sys.executable, unmerge_script, settings_file],
            capture_output=True,
            text=True,
            timeout=10,
        )

        with open(settings_file) as f:
            after = json.load(f)
        remaining = after.get("hooks", {}).get("PreToolUse", [])
        standalone_removed = not any(
            "token_optimizer_gate.py" in str(h.get("args", []))
            for entry in remaining
            for h in entry.get("hooks", [])
        )

        check(
            "15. _unmerge_settings.py removes the standalone entry (rc=0) and "
            "notes on stdout that the plugin still appears to be installed "
            "(enabledPlugins signal)",
            proc.returncode == 0
            and standalone_removed
            and "token-optimizer-plugin" in proc.stdout
            and "still appears to be installed" in proc.stdout,
            "rc={0} stdout={1!r} stderr={2!r}".format(
                proc.returncode, proc.stdout, proc.stderr
            ),
        )


def test_16_unmerge_notes_when_plugin_in_cache():
    """Uninstall-side counterpart to test 13: settings.json has only the
    standalone gate's hook entry (no enabledPlugins signal), but a
    plugins-cache directory for the plugin exists on disk. After removing
    the standalone entry, _unmerge_settings.py must still note that the
    plugin still appears to be installed."""
    tmp, claude_dir = fresh_home()
    with tmp:
        _make_plugin_cache_dir(claude_dir)
        settings_file = os.path.join(claude_dir, "settings.json")
        settings = {"hooks": {"PreToolUse": [_standalone_hook_entry()]}}
        with open(settings_file, "w") as f:
            json.dump(settings, f)

        unmerge_script = os.path.join(REPO_ROOT, "install", "_unmerge_settings.py")
        proc = subprocess.run(
            [sys.executable, unmerge_script, settings_file],
            capture_output=True,
            text=True,
            timeout=10,
        )

        check(
            "16. _unmerge_settings.py notes on stdout that the plugin still "
            "appears to be installed (plugins-cache signal)",
            proc.returncode == 0
            and "token-optimizer-plugin" in proc.stdout
            and "still appears to be installed" in proc.stdout,
            "rc={0} stdout={1!r} stderr={2!r}".format(
                proc.returncode, proc.stdout, proc.stderr
            ),
        )


def test_17_unmerge_silent_when_plugin_absent():
    """Critical regression guard for the uninstall side, mirroring test 14:
    only the standalone entry is present, no enabledPlugins entry, no
    plugins-cache directory for this plugin - _unmerge_settings.py must stay
    silent about plugin coexistence."""
    tmp, claude_dir = fresh_home()
    with tmp:
        settings_file = os.path.join(claude_dir, "settings.json")
        settings = {"hooks": {"PreToolUse": [_standalone_hook_entry()]}}
        with open(settings_file, "w") as f:
            json.dump(settings, f)

        unmerge_script = os.path.join(REPO_ROOT, "install", "_unmerge_settings.py")
        proc = subprocess.run(
            [sys.executable, unmerge_script, settings_file],
            capture_output=True,
            text=True,
            timeout=10,
        )

        check(
            "17. _unmerge_settings.py stays silent (no plugin-still-installed "
            "note) when genuinely neither real signal is present",
            proc.returncode == 0 and "token-optimizer-plugin" not in proc.stdout,
            "rc={0} stdout={1!r}".format(proc.returncode, proc.stdout),
        )


def test_18_uninstall_sh_noninteractive_stdin_exits_0():
    """Finding 16 regression: under `set -euo pipefail`, uninstall.sh's old
    `read -r -p ...` prompt died on EOF when stdin wasn't a terminal (e.g.
    piped from /dev/null, as in CI or any non-interactive invocation),
    aborting before its closing "uninstalled" message ever printed and
    leaving the state directory behind. Build a fixture that looks like a
    completed standalone install (gate script + hook entry + state dir all
    present) under a fake HOME, run uninstall.sh with stdin from /dev/null,
    and confirm it exits 0, prints the final "uninstalled" line, explains
    that it left the state dir in place (since it couldn't prompt), and
    that the state dir is in fact still there."""
    tmp, claude_dir = fresh_home()
    with tmp:
        hooks_dir = os.path.join(claude_dir, "hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        gate_dest = os.path.join(hooks_dir, "token_optimizer_gate.py")
        with open(STANDALONE_HOOK_PATH) as src, open(gate_dest, "w") as dst:
            dst.write(src.read())

        settings_file = os.path.join(claude_dir, "settings.json")
        settings = {"hooks": {"PreToolUse": [_standalone_hook_entry()]}}
        with open(settings_file, "w") as f:
            json.dump(settings, f)

        state_dir = os.path.join(claude_dir, ".token-optimizer-state")
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "dummy.txt"), "w") as f:
            f.write("counter data")

        uninstall_script = os.path.join(REPO_ROOT, "install", "uninstall.sh")
        env = os.environ.copy()
        env["HOME"] = tmp.name
        with open(os.devnull, "r") as devnull:
            proc = subprocess.run(
                ["bash", uninstall_script],
                stdin=devnull,
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )

        state_dir_preserved = os.path.isdir(state_dir)

        check(
            "18. uninstall.sh exits 0 and prints its final line when stdin isn't "
            "a terminal, instead of dying at the interactive prompt",
            proc.returncode == 0
            and "token-optimizer uninstalled" in proc.stdout
            and "leaving the state directory" in proc.stdout
            and state_dir_preserved,
            "rc={0} stdout={1!r} stderr={2!r}".format(
                proc.returncode, proc.stdout, proc.stderr
            ),
        )


def test_19_unmerge_restores_missing_hooks_key():
    """Finding 17 regression: if settings.json had NO "hooks" key before the
    standalone hook was installed (so after install, hooks.PreToolUse holds
    exactly the one entry we added and nothing else), uninstalling it should
    restore settings.json to its exact pre-install shape - no "hooks" key at
    all - rather than leaving behind an empty
    {"hooks": {"PreToolUse": []}}."""
    tmp, claude_dir = fresh_home()
    with tmp:
        settings_file = os.path.join(claude_dir, "settings.json")
        settings = {
            "hooks": {"PreToolUse": [_standalone_hook_entry()]},
            "someOtherKey": "untouched",
        }
        with open(settings_file, "w") as f:
            json.dump(settings, f)

        unmerge_script = os.path.join(REPO_ROOT, "install", "_unmerge_settings.py")
        proc = subprocess.run(
            [sys.executable, unmerge_script, settings_file],
            capture_output=True,
            text=True,
            timeout=10,
        )

        with open(settings_file) as f:
            after = json.load(f)

        check(
            "19. _unmerge_settings.py drops the 'hooks' key entirely (not "
            "present-and-empty) when removing the only entry it ever held, "
            "and leaves other keys untouched",
            proc.returncode == 0
            and "hooks" not in after
            and after.get("someOtherKey") == "untouched",
            "rc={0} after={1!r} stderr={2!r}".format(
                proc.returncode, after, proc.stderr
            ),
        )


def main():
    test_1_top_level_bash_denied()
    test_2_subagent_bash_allowed()
    test_3_free_budget_reads_allowed()
    test_4_read_beyond_budget_denied()
    test_5_write_real_settings_allowed()
    test_6_malformed_json_fails_open()
    test_7_session_id_path_traversal()
    test_8_fake_settings_path_denied()
    test_9_plugin_denies_write_to_real_settings()
    test_10_corrupt_counter_self_heals()
    test_11_concurrent_writes_no_torn_state_file()
    test_12_merge_warns_when_plugin_enabled_in_settings()
    test_13_merge_warns_when_plugin_in_cache()
    test_14_merge_silent_when_plugin_absent()
    test_15_unmerge_notes_when_plugin_enabled_in_settings()
    test_16_unmerge_notes_when_plugin_in_cache()
    test_17_unmerge_silent_when_plugin_absent()
    test_18_uninstall_sh_noninteractive_stdin_exits_0()
    test_19_unmerge_restores_missing_hooks_key()

    print("")
    passed = sum(1 for _, status, _ in results if status == "PASS")
    failed = sum(1 for _, status, _ in results if status == "FAIL")
    print("=" * 50)
    print("SUMMARY: {0} passed, {1} failed, {2} total".format(passed, failed, len(results)))
    print("=" * 50)

    if failed:
        print("\nFAILED TESTS:")
        for name, status, detail in results:
            if status == "FAIL":
                print("  - {0}{1}".format(name, (" (" + detail + ")") if detail else ""))
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
