#!/usr/bin/env python3
"""Self-contained regression tests for install/token_optimizer_gate.py.

Runs the gate script as a real subprocess with crafted stdin JSON and checks
exit codes, exactly mirroring how Claude Code's PreToolUse hook invokes it.
No external dependencies beyond the stdlib. Run with:

    python3 tests/test_gate.py

Exits 0 if all tests pass, non-zero if any fail (CI-friendly).
"""
import json
import os
import subprocess
import sys
import tempfile

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
GATE_SCRIPT = os.path.join(REPO_ROOT, "install", "token_optimizer_gate.py")

results = []


def run_gate(payload, env_overrides=None):
    """Run the gate script with the given payload dict as stdin JSON.
    Returns (returncode, stdout, stderr)."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, GATE_SCRIPT],
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


def test_1_top_level_bash_denied():
    tmp, claude_dir = fresh_home()
    with tmp:
        rc, out, err = run_gate(
            {"tool_name": "Bash", "session_id": "s1", "tool_input": {"command": "ls"}},
            env_overrides={"HOME": tmp.name},
        )
        check("1. top-level Bash (no agent_type) -> exit 2", rc == 2, "got rc={0}".format(rc))


def test_2_subagent_bash_allowed():
    tmp, claude_dir = fresh_home()
    with tmp:
        rc, out, err = run_gate(
            {
                "tool_name": "Bash",
                "agent_type": "runner",
                "session_id": "s2",
                "tool_input": {"command": "ls"},
            },
            env_overrides={"HOME": tmp.name},
        )
        check("2. subagent Bash (agent_type=runner) -> exit 0", rc == 0, "got rc={0}".format(rc))


def test_3_first_read_allowed():
    tmp, claude_dir = fresh_home()
    with tmp:
        rc, out, err = run_gate(
            {"tool_name": "Read", "session_id": "s3-fresh", "tool_input": {"file_path": "/tmp/x"}},
            env_overrides={"HOME": tmp.name},
        )
        check("3. first top-level Read in a fresh session -> exit 0", rc == 0, "got rc={0}".format(rc))


def test_4_second_read_denied():
    tmp, claude_dir = fresh_home()
    with tmp:
        env = {"HOME": tmp.name}
        session_id = "s4-repeat"
        rc1, _, _ = run_gate(
            {"tool_name": "Read", "session_id": session_id, "tool_input": {"file_path": "/tmp/x"}},
            env_overrides=env,
        )
        rc2, out2, err2 = run_gate(
            {"tool_name": "Read", "session_id": session_id, "tool_input": {"file_path": "/tmp/y"}},
            env_overrides=env,
        )
        check(
            "4. second top-level Read, same session -> exit 2",
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
    tmp, claude_dir = fresh_home()
    with tmp:
        env = os.environ.copy()
        env["HOME"] = tmp.name
        proc = subprocess.run(
            [sys.executable, GATE_SCRIPT],
            input="{not valid json!!",
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        check("6. malformed JSON input -> exit 0 (fail open)", proc.returncode == 0, "got rc={0}".format(proc.returncode))


def test_7_session_id_path_traversal():
    tmp, claude_dir = fresh_home()
    with tmp:
        env = {"HOME": tmp.name}
        malicious_ids = ["../../evil", "/etc/passwd", "../../../../tmp/evil"]
        state_dir = os.path.join(claude_dir, ".token-optimizer-state")
        all_ok = True
        detail = ""
        for sid in malicious_ids:
            rc, out, err = run_gate(
                {"tool_name": "Read", "session_id": sid, "tool_input": {"file_path": "/tmp/x"}},
                env_overrides=env,
            )
            # Confirm no file was created outside the HOME-scoped state dir.
            if os.path.isdir(state_dir):
                for root, dirs, files in os.walk(state_dir):
                    for fn in files:
                        full = os.path.join(root, fn)
                        if not os.path.realpath(full).startswith(os.path.realpath(state_dir)):
                            all_ok = False
                            detail = "escaped file: {0}".format(full)
            # Also confirm nothing was written directly outside tmp.name entirely
            # (e.g. a literal "/etc/passwd.count" at filesystem root).
            suspicious = "/etc/passwd.count"
            if os.path.exists(suspicious):
                all_ok = False
                detail = "created {0} outside state dir".format(suspicious)
        check(
            "7. session_id path traversal -> no file created outside state dir",
            all_ok,
            detail,
        )


def test_8_fake_settings_path_denied():
    tmp, claude_dir = fresh_home()
    with tmp:
        fake_settings = "/tmp/fake_settings.json"
        rc, out, err = run_gate(
            {
                "tool_name": "Write",
                "session_id": "s8",
                "tool_input": {"file_path": fake_settings},
            },
            env_overrides={"HOME": tmp.name},
        )
        check(
            "8. Write to a path merely ending in settings.json (not real) -> exit 2",
            rc == 2,
            "got rc={0}".format(rc),
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
