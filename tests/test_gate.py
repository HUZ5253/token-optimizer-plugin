#!/usr/bin/env python3
"""Self-contained regression tests for install/token_optimizer_gate.py.

Runs the gate script as a real subprocess with crafted stdin JSON and checks
exit codes, exactly mirroring how Claude Code's PreToolUse hook invokes it.
No external dependencies beyond the stdlib. Run with:

    python3 tests/test_gate.py

Exits 0 if all tests pass, non-zero if any fail (CI-friendly).
"""
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


def test_3_first_read_allowed():
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            state_tmp, state_dir = fresh_plugin_state_dir()
            with state_tmp:
                rc, out, err = run_gate(
                    {"tool_name": "Read", "session_id": "s3-fresh", "tool_input": {"file_path": "/tmp/x"}},
                    env_overrides={"HOME": tmp.name},
                    variant=variant,
                    state_dir=state_dir,
                )
                check(
                    "3. [{0}] first top-level Read in a fresh session -> exit 0".format(variant),
                    rc == 0,
                    "got rc={0}".format(rc),
                )


def test_4_second_read_denied():
    for variant in ("standalone", "plugin"):
        tmp, claude_dir = fresh_home()
        with tmp:
            state_tmp, state_dir = fresh_plugin_state_dir()
            with state_tmp:
                env = {"HOME": tmp.name}
                session_id = "s4-repeat"
                rc1, _, _ = run_gate(
                    {"tool_name": "Read", "session_id": session_id, "tool_input": {"file_path": "/tmp/x"}},
                    env_overrides=env,
                    variant=variant,
                    state_dir=state_dir,
                )
                rc2, out2, err2 = run_gate(
                    {"tool_name": "Read", "session_id": session_id, "tool_input": {"file_path": "/tmp/y"}},
                    env_overrides=env,
                    variant=variant,
                    state_dir=state_dir,
                )
                check(
                    "4. [{0}] second top-level Read, same session -> exit 2".format(variant),
                    rc1 == 0 and rc2 == 2,
                    "got rc1={0} rc2={1}".format(rc1, rc2),
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


def main():
    test_1_top_level_bash_denied()
    test_2_subagent_bash_allowed()
    test_3_first_read_allowed()
    test_4_second_read_denied()
    test_5_write_real_settings_allowed()
    test_6_malformed_json_fails_open()
    test_7_session_id_path_traversal()
    test_8_fake_settings_path_denied()
    test_9_plugin_denies_write_to_real_settings()

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
