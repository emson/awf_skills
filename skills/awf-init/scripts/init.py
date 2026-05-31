#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""awf-init — idempotent onboarding.

Brings ~/.config/awf/.env into existence + populated, ensures AWF_HOME
is exported in the user's shell rc, and warns if `install.sh` hasn't
linked the skills into ~/.claude/skills/.

Standard library only. Re-running on a healthy environment is a no-op.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# ── Bootstrap ───────────────────────────────────────────────────────────────
AWF_HOME = Path(
    os.environ.get("AWF_HOME") or Path(__file__).resolve().parents[3]
).resolve()
sys.path.insert(0, str(AWF_HOME / "lib"))

from awf_home import find_awf_home, AwfHomeNotFound  # noqa: E402

USER_ENV = Path("~/.config/awf/.env").expanduser()
SKILLS_LINK_DIR = Path("~/.claude/skills").expanduser()

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[0m"
)


def _color(s: str, c: str) -> str:
    return f"{c}{s}{RESET}" if sys.stdout.isatty() else s


def info(msg: str) -> None:    print(_color(f"  {msg}", DIM))
def ok(msg: str) -> None:      print(_color(f"  ✓ {msg}", GREEN))
def warn(msg: str) -> None:    print(_color(f"  ! {msg}", YELLOW))
def fail(msg: str) -> None:    print(_color(f"  ✗ {msg}", RED), file=sys.stderr)


# ── .env template parsing ───────────────────────────────────────────────────


@dataclass
class TemplateEntry:
    key: str
    default: str
    leading_comment: str  # the comment block immediately preceding this key


def parse_template(path: Path) -> list[TemplateEntry]:
    """Parse .env.example, capturing the comment block before each key.

    The comment block is everything from the previous blank line / previous
    key up to the line declaring this key. Used to give the user context
    at the prompt.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[TemplateEntry] = []
    buffer: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if not line:
            buffer = []
            continue
        if line.lstrip().startswith("#"):
            buffer.append(line)
            continue
        if "=" in line:
            key, _, default = line.partition("=")
            entries.append(TemplateEntry(
                key=key.strip(),
                default=default.strip(),
                leading_comment="\n".join(buffer),
            ))
            buffer = []
    return entries


# ── .env file read/write (preserve comments and order) ──────────────────────


def parse_env_pairs(path: Path) -> dict[str, str]:
    """Return {key: value} for non-blank, non-comment lines."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        out[key.strip()] = val
    return out


def write_value(path: Path, key: str, value: str) -> None:
    """Set `key=value` in `path`. Replaces existing line; appends if absent.

    Preserves all surrounding comments and order.
    """
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_missing_keys(env_path: Path, template: list[TemplateEntry]) -> list[str]:
    """Append any template keys absent from env_path. Returns the keys added."""
    existing = parse_env_pairs(env_path)
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    added: list[str] = []
    new_lines: list[str] = []
    for entry in template:
        if entry.key in existing:
            continue
        added.append(entry.key)
        if entry.leading_comment:
            new_lines.extend(["", entry.leading_comment])
        new_lines.append(f"{entry.key}={entry.default}")
    if not added:
        return []
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n# ── added by awf-init ───────────────────────────────────────────\n"
    text += "\n".join(new_lines).lstrip("\n") + "\n"
    env_path.write_text(text, encoding="utf-8")
    return added


# ── prompts ─────────────────────────────────────────────────────────────────


def prompt_value(entry: TemplateEntry) -> str | None:
    """Prompt for a value. Empty answer → None (skip). Returns the string."""
    print()
    if entry.leading_comment:
        for line in entry.leading_comment.splitlines():
            info(line)
    raw = input(f"  {entry.key}= ").strip()
    return raw or None


def confirm(question: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        ans = input(f"  {question} {suffix} ").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


# ── shell rc handling ───────────────────────────────────────────────────────


def detect_rc_path() -> Path | None:
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    if shell.endswith("/zsh"):
        return home / ".zshrc"
    if shell.endswith("/bash"):
        for cand in (home / ".bashrc", home / ".bash_profile", home / ".profile"):
            if cand.exists():
                return cand
        return home / ".bashrc"
    return None


AWF_HOME_EXPORT_RE = re.compile(r"^\s*export\s+AWF_HOME\s*=", re.MULTILINE)


def rc_has_awf_home(rc: Path) -> bool:
    if not rc.is_file():
        return False
    return bool(AWF_HOME_EXPORT_RE.search(rc.read_text(encoding="utf-8")))


def append_awf_home_to_rc(rc: Path, awf_home: Path) -> None:
    block = (
        "\n# awf-skills\n"
        f"export AWF_HOME={awf_home}\n"
    )
    with rc.open("a", encoding="utf-8") as f:
        f.write(block)


# ── orchestration ───────────────────────────────────────────────────────────


def step_resolve_home() -> Path:
    print(_color("\n[1/4] Resolve AWF_HOME", DIM))
    try:
        home = find_awf_home()
    except AwfHomeNotFound as e:
        fail(str(e))
        print(
            "  → Set AWF_HOME, or clone the suite to ~/.claude/awf-skills/, "
            "or run this from inside the repo.",
            file=sys.stderr,
        )
        sys.exit(1)
    ok(f"AWF_HOME = {home}")
    return home


def step_env_file(home: Path, *, interactive: bool) -> None:
    print(_color("\n[2/4] User credentials (~/.config/awf/.env)", DIM))
    template_path = home / ".env.example"
    if not template_path.is_file():
        fail(f"template not found at {template_path}")
        sys.exit(1)
    template = parse_template(template_path)

    USER_ENV.parent.mkdir(parents=True, exist_ok=True)

    if not USER_ENV.exists():
        shutil.copy2(template_path, USER_ENV)
        ok(f"created {USER_ENV} from template")
    else:
        ok(f"{USER_ENV} already exists")
        added = append_missing_keys(USER_ENV, template)
        if added:
            warn(f"appended {len(added)} new key(s) from template: {', '.join(added)}")

    existing = parse_env_pairs(USER_ENV)
    empty_keys = [
        e for e in template
        if not (existing.get(e.key, "") or "").strip()
        and not (e.default or "").strip()
    ]

    if not empty_keys:
        ok("all credentials populated")
        return

    if not interactive:
        warn(f"{len(empty_keys)} credential(s) unset; re-run without --non-interactive to fill")
        for e in empty_keys:
            info(f"  unset: {e.key}")
        return

    print()
    info(f"{len(empty_keys)} credential(s) to fill. Press enter to skip any.")
    for entry in empty_keys:
        try:
            val = prompt_value(entry)
        except (EOFError, KeyboardInterrupt):
            print()
            warn("input ended; remaining values left blank")
            return
        if val is None:
            info(f"  skipped {entry.key}")
            continue
        write_value(USER_ENV, entry.key, val)
        ok(f"set {entry.key} = {'*' * min(len(val), 8)} ({len(val)} chars)")


def step_shell_rc(home: Path, *, interactive: bool, no_rc: bool) -> None:
    print(_color("\n[3/4] Shell rc — export AWF_HOME", DIM))
    if no_rc:
        info("--no-rc; skipping")
        return
    rc = detect_rc_path()
    if rc is None:
        warn(f"could not detect rc file from $SHELL={os.environ.get('SHELL', '')!r}; skipping")
        return
    if not rc.exists():
        warn(f"{rc} does not exist; not creating it (your territory)")
        return
    if rc_has_awf_home(rc):
        ok(f"AWF_HOME already exported in {rc}")
        return
    if not interactive:
        warn(f"AWF_HOME not exported in {rc}; re-run interactively to add it")
        return
    if confirm(f"append `export AWF_HOME={home}` to {rc}?", default=True):
        append_awf_home_to_rc(rc, home)
        ok(f"appended to {rc}")
        info(f"open a new shell or run: source {rc}")
    else:
        info("skipped")


def step_check_symlinks(home: Path) -> None:
    print(_color("\n[4/4] Skill symlinks (~/.claude/skills/)", DIM))
    if not SKILLS_LINK_DIR.is_dir():
        warn(f"{SKILLS_LINK_DIR} does not exist; have you run ./install.sh?")
        return
    skills = sorted((home / "skills").glob("awf-*"))
    missing: list[str] = []
    for skill in skills:
        link = SKILLS_LINK_DIR / skill.name
        if link.is_symlink() and link.resolve() == skill.resolve():
            continue
        missing.append(skill.name)
    if missing:
        warn(f"{len(missing)} skill(s) not linked: {', '.join(missing)}")
        info(f"run: {home}/install.sh")
        return
    ok(f"all {len(skills)} skill(s) linked")


# ── main ────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    args = argv[1:]
    bad = [a for a in args if a not in ("--non-interactive", "--no-rc")]
    if bad:
        print(f"unknown argument(s): {' '.join(bad)}", file=sys.stderr)
        print("usage: init.py [--non-interactive] [--no-rc]", file=sys.stderr)
        return 2

    interactive = "--non-interactive" not in args and sys.stdin.isatty()
    no_rc = "--no-rc" in args

    print(_color("awf-init — onboarding", DIM))

    home = step_resolve_home()
    step_env_file(home, interactive=interactive)
    step_shell_rc(home, interactive=interactive, no_rc=no_rc)
    step_check_symlinks(home)

    print()
    print(_color("Next: run awf-doctor.", GREEN))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
