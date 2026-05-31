"""Google OAuth + service factories for GSC and Site Verification.

The legacy code wrote `token.json` next to the cwd, which only worked
when `cd`'d into the monorepo root. This port resolves the token path
explicitly using the project / AWF_HOME / user-config layering (A6).

Required client-secrets file is at the path stored in
`$GOOGLE_APPLICATION_CREDENTIALS` — a desktop-client OAuth JSON
downloaded from GCP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES: list[str] = [
    "https://www.googleapis.com/auth/webmasters",
    "https://www.googleapis.com/auth/siteverification",
]


class GscError(RuntimeError):
    pass


# ── Token resolution ───────────────────────────────────────────────────


def _candidate_token_paths(
    project_root: Optional[Path],
    awf_home: Optional[Path],
) -> list[Path]:
    paths: list[Path] = []
    if project_root is not None:
        paths.append(project_root / "token.json")
    if awf_home is not None:
        paths.append(awf_home / "token.json")
    paths.append(Path("~/.config/awf/token.json").expanduser())
    return paths


def find_token_path(
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
) -> Optional[Path]:
    """Return the first existing `token.json` in the resolution chain,
    or `None`."""
    for p in _candidate_token_paths(project_root, awf_home):
        if p.is_file():
            return p
    return None


def _default_token_write_path(
    project_root: Optional[Path],
    awf_home: Optional[Path],
) -> Path:
    """Where to *write* a freshly-minted token. Prefer project root when
    one is in scope (most discoverable next to the user's work);
    otherwise AWF_HOME; otherwise the user-global location."""
    if project_root is not None:
        return project_root / "token.json"
    if awf_home is not None:
        return awf_home / "token.json"
    p = Path("~/.config/awf/token.json").expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── Credentials ────────────────────────────────────────────────────────


def load_or_authenticate(
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
    interactive: bool = True,
) -> Credentials:
    """Return live OAuth `Credentials`. Refreshes silently if possible,
    runs `InstalledAppFlow.run_local_server` in interactive mode if not.

    `interactive=False` raises `GscError` rather than launching a
    browser — useful when this is being called from a non-TTY context
    (e.g. CI).
    """
    # Lazy import — keeps `lib/config.py` out of this module's import
    # graph at definition time and matches the pattern used in cf/,
    # fathom/, namecheap/.
    from config import Config

    cfg = Config.layered(project_root=project_root, awf_home=awf_home)
    client_secrets_str = (cfg.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if not client_secrets_str:
        raise GscError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set in any layer "
            "(env, ./.env, $AWF_HOME/.env, ~/.config/awf/.env). "
            "Run awf-init to populate it, then retry."
        )
    client_secrets = Path(client_secrets_str).expanduser()
    if not client_secrets.is_file():
        raise GscError(
            f"GOOGLE_APPLICATION_CREDENTIALS={client_secrets_str!r} does not exist. "
            "Download the OAuth desktop-client JSON from GCP and update the path."
        )

    token_path = find_token_path(project_root, awf_home)
    creds: Optional[Credentials] = None
    if token_path is not None:
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            print(f"- WARN: could not read token at {token_path}: {e}")
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            assert token_path is not None
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except RefreshError:
            print(
                f"- token at {token_path} could not be refreshed; "
                "re-authenticating."
            )
            try:
                if token_path is not None:
                    token_path.unlink()
            except OSError:
                pass
            creds = None

    if not interactive:
        raise GscError(
            "Google OAuth requires interactive authentication, but "
            "interactive=False. Run an awf-* skill from a terminal first "
            "to populate token.json, then re-run."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0)

    write_path = _default_token_write_path(project_root, awf_home)
    write_path.parent.mkdir(parents=True, exist_ok=True)
    write_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"- token saved to {write_path}")
    return creds


# ── Service factories ──────────────────────────────────────────────────


def get_gsc_service(
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
    interactive: bool = True,
):
    creds = load_or_authenticate(
        project_root=project_root, awf_home=awf_home, interactive=interactive,
    )
    return build("webmasters", "v3", credentials=creds, cache_discovery=False)


def get_site_verification_service(
    *,
    project_root: Optional[Path] = None,
    awf_home: Optional[Path] = None,
    interactive: bool = True,
):
    creds = load_or_authenticate(
        project_root=project_root, awf_home=awf_home, interactive=interactive,
    )
    return build("siteVerification", "v1", credentials=creds, cache_discovery=False)
