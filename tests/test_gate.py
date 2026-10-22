#!/usr/bin/env python3
"""Self-contained regression tests for install/token_optimizer_gate.py.

Runs the gate script as a real subprocess with crafted stdin JSON and checks
exit codes, exactly mirroring how Claude Code's PreToolUse hook invokes it.
No external dependencies beyond the stdlib. Run with:

    python3 tests/test_gate.py

The suite also covers install/_merge_settings.py and
install/_unmerge_settings.py's dual-install-path coexistence detection:
tests 12/13 check that _merge_settings.py warns on stderr but still installs
successfully when a plugin-shaped hook entry is already present in
settings.json (and stays silent when nothing plugin-shaped is present), and
test 14 checks the matching uninstall-side warning in _unmerge_settings.py.

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


def test_12_merge_warns_when_plugin_also_installed():
    """Regression test for the Option A + Option B double-install detection:
    when settings.json already has a PreToolUse hook entry shaped like the
    token-optimizer-plugin Claude Code plugin's own hook (a plugins-cache
    path ending in gate.py), install/_merge_settings.py must print a
    non-fatal warning to stderr while still proceeding with the install
    (exit code 0)."""
    tmp, claude_dir = fresh_home()
    with tmp:
        settings_file = os.path.join(claude_dir, "settings.json")
        plugin_hook_path = os.path.join(
            claude_dir,
            "plugins",
            "cache",
            "some-marketplace",
            "token-optimizer-plugin",
            "1.0.0",
            "hooks",
            "gate.py",
        )
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash|Write|Edit|NotebookEdit|Read|Grep",
                        "hooks": [
                            {
                                "type": "command",
                                "command": sys.executable,
                                "args": [plugin_hook_path],
                            }
                        ],
                    }
                ]
            }
        }
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
            "12. _merge_settings.py warns on stderr + still installs (rc=0) when a "
            "plugin-shaped hook entry is already present",
            proc.returncode == 0
            and "token-optimizer-plugin" in proc.stderr
            and "also appears to be installed" in proc.stderr,
            "rc={0} stderr={1!r}".format(proc.returncode, proc.stderr),
        )


def test_13_merge_silent_when_plugin_absent():
    """Negative counterpart to test 12: with no settings.json at all (a
    plain fresh install, nothing plugin-shaped present), _merge_settings.py
    must NOT print the plugin-coexistence warning."""
    tmp, claude_dir = fresh_home()
    with tmp:
        settings_file = os.path.join(claude_dir, "settings.json")
        merge_script = os.path.join(REPO_ROOT, "install", "_merge_settings.py")
        proc = subprocess.run(
            [sys.executable, merge_script, settings_file, sys.executable],
            capture_output=True,
            text=True,
            timeout=10,
        )

        check(
            "13. _merge_settings.py stays silent (no plugin-coexistence warning) "
            "when nothing plugin-shaped is present",
            proc.returncode == 0 and "token-optimizer-plugin" not in proc.stderr,
            "rc={0} stderr={1!r}".format(proc.returncode, proc.stderr),
        )


def test_14_unmerge_warns_when_plugin_still_installed():
    """Uninstall-side counterpart to tests 12/13: install/_unmerge_settings.py
    has its own warn_if_plugin_still_installed/references_plugin_install
    helpers, exercised here for the first time. Positive case: settings.json
    has both the standalone gate's hook entry (the thing being removed) and a
    plugin-shaped hook entry (same pattern test 12 uses to simulate the
    plugin also being installed); after removing the standalone entry,
    _unmerge_settings.py must still succeed (rc=0) and must note that the
    plugin still appears to be installed (that note is printed with a plain
    print(), i.e. to stdout, unlike _merge_settings.py's stderr warning -
    checked against the real output below rather than assumed). Negative
    case: with no plugin-shaped entry present, no such note is printed."""
    unmerge_script = os.path.join(REPO_ROOT, "install", "_unmerge_settings.py")

    # Positive case: standalone entry + plugin-shaped entry both present.
    tmp, claude_dir = fresh_home()
    with tmp:
        settings_file = os.path.join(claude_dir, "settings.json")
        plugin_hook_path = os.path.join(
            claude_dir,
            "plugins",
            "cache",
            "some-marketplace",
            "token-optimizer-plugin",
            "1.0.0",
            "hooks",
            "gate.py",
        )
        standalone_hook_path = os.path.join(
            REPO_ROOT, "install", "token_optimizer_gate.py"
        )
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash|Write|Edit|NotebookEdit|Read|Grep",
                        "hooks": [
                            {
                                "type": "command",
                                "command": sys.executable,
                                "args": [standalone_hook_path],
                            }
                        ],
                    },
                    {
                        "matcher": "Bash|Write|Edit|NotebookEdit|Read|Grep",
                        "hooks": [
                            {
                                "type": "command",
                                "command": sys.executable,
                                "args": [plugin_hook_path],
                            }
                        ],
                    },
                ]
            }
        }
        with open(settings_file, "w") as f:
            json.dump(settings, f)

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
            "14. _unmerge_settings.py removes the standalone entry (rc=0) and "
            "notes on stdout that the plugin still appears to be installed",
            proc.returncode == 0
            and standalone_removed
            and "token-optimizer-plugin" in proc.stdout
            and "still appears to be installed" in proc.stdout,
            "rc={0} stdout={1!r} stderr={2!r}".format(
                proc.returncode, proc.stdout, proc.stderr
            ),
        )

    # Negative case: only the standalone entry is present, no plugin-shaped
    # entry - mirrors test 13's structure for the merge side.
    tmp2, claude_dir2 = fresh_home()
    with tmp2:
        settings_file2 = os.path.join(claude_dir2, "settings.json")
        standalone_hook_path2 = os.path.join(
            REPO_ROOT, "install", "token_optimizer_gate.py"
        )
        settings2 = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash|Write|Edit|NotebookEdit|Read|Grep",
                        "hooks": [
                            {
                                "type": "command",
                                "command": sys.executable,
                                "args": [standalone_hook_path2],
                            }
                        ],
                    }
                ]
            }
        }
        with open(settings_file2, "w") as f:
            json.dump(settings2, f)

        proc2 = subprocess.run(
            [sys.executable, unmerge_script, settings_file2],
            capture_output=True,
            text=True,
            timeout=10,
        )

        check(
            "14. _unmerge_settings.py stays silent (no plugin-still-installed "
            "note) when no plugin-shaped entry is present",
            proc2.returncode == 0 and "token-optimizer-plugin" not in proc2.stdout,
            "rc={0} stdout={1!r}".format(proc2.returncode, proc2.stdout),
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
    test_12_merge_warns_when_plugin_also_installed()
    test_13_merge_silent_when_plugin_absent()
    test_14_unmerge_warns_when_plugin_still_installed()

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
