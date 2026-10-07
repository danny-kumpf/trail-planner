"""Strava's API: authorizing this site to read your activities, and reading them.

Setup, once: create an API application at https://www.strava.com/settings/api with its
Authorization Callback Domain set to `localhost`, and put its id and secret in strava_app.json:

    {"client_id": "12345", "client_secret": "..."}

The first sync sends you to Strava to approve access. The tokens Strava returns are kept in
strava_tokens.json and refreshed as they expire, so later syncs need no approval.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trail_planner.trails import PROJECT_ROOT

DEFAULT_APP_PATH = PROJECT_ROOT / "strava_app.json"
DEFAULT_TOKENS_PATH = PROJECT_ROOT / "strava_tokens.json"

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"
# Every activity, including ones only you can see.
SCOPE = "activity:read_all"
TIMEOUT_S = 30.0
# The most activities Strava lists per request.
MAX_ACTIVITIES_PER_PAGE = 200
# Refresh a little early, so a token can't expire partway through a sync.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
# The recorded channels a sync downloads; `time` is seconds since the activity's start.
STREAM_KEYS = ("time", "latlng", "altitude", "distance", "cadence", "heartrate")

OpenUrl = Callable[..., Any]


class StravaError(RuntimeError):
    """A failure talking to Strava, worded to show to the person syncing."""


class NotSetUp(StravaError):
    pass


class NotAuthorized(StravaError):
    """No tokens yet, or Strava no longer accepts them: you need to approve access (again)."""


class RateLimited(StravaError):
    pass


class NotFound(StravaError):
    pass


@dataclass(frozen=True)
class StravaApp:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: datetime


def load_app(path: Path = DEFAULT_APP_PATH) -> StravaApp:
    if not path.exists():
        raise NotSetUp(
            f"Strava isn't set up: create {path.name} with your Strava API application's "
            "client_id and client_secret (see the README)."
        )
    fields = json.loads(path.read_text(encoding="utf-8"))
    return StravaApp(client_id=str(fields["client_id"]), client_secret=str(fields["client_secret"]))


def load_tokens(path: Path = DEFAULT_TOKENS_PATH) -> Tokens | None:
    """The saved tokens, or None if access hasn't been approved yet."""
    if not path.exists():
        return None
    fields = json.loads(path.read_text(encoding="utf-8"))
    return Tokens(
        access_token=fields["access_token"],
        refresh_token=fields["refresh_token"],
        expires_at=datetime.fromisoformat(fields["expires_at"]),
    )


def save_tokens(tokens: Tokens, path: Path = DEFAULT_TOKENS_PATH) -> None:
    fields = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at.isoformat(),
    }
    path.write_text(json.dumps(fields, indent=4) + "\n", encoding="utf-8")


def authorize_url(app: StravaApp, redirect_uri: str, state: str) -> str:
    """Where to send the browser to approve access; Strava then redirects to `redirect_uri`."""
    query = urllib.parse.urlencode(
        {
            "client_id": app.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": SCOPE,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def granted_scopes(scope_parameter: str) -> set[str]:
    """The scopes in the `scope` parameter Strava adds to the redirect, e.g. "read,activity:read_all"."""
    return {s for s in scope_parameter.split(",") if s}


def read_json(request: urllib.request.Request, open_url: OpenUrl = urllib.request.urlopen) -> Any:
    try:
        with open_url(request, timeout=TIMEOUT_S) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise NotAuthorized("Strava no longer accepts this site's access; approve it again.") from error
        if error.code == 404:
            raise NotFound(f"Strava has nothing at {request.full_url}") from error
        if error.code == 429:
            raise RateLimited("Strava's rate limit was reached; sync again in 15 minutes.") from error
        raise StravaError(f"Strava returned HTTP {error.code} for {request.full_url}") from error
    except urllib.error.URLError as error:
        raise StravaError(f"Couldn't reach Strava: {error.reason}") from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise StravaError("Strava returned a response that wasn't JSON") from error


def tokens_from_response(response: dict) -> Tokens:
    return Tokens(
        access_token=response["access_token"],
        refresh_token=response["refresh_token"],
        expires_at=datetime.fromtimestamp(response["expires_at"], tz=timezone.utc),
    )


def token_request(app: StravaApp, fields: dict[str, str], open_url: OpenUrl) -> Tokens:
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(
            {"client_id": app.client_id, "client_secret": app.client_secret, **fields}
        ).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return tokens_from_response(read_json(request, open_url))


def exchange_code(app: StravaApp, code: str, open_url: OpenUrl = urllib.request.urlopen) -> Tokens:
    """The tokens for the code Strava gave the redirect after access was approved."""
    return token_request(app, {"code": code, "grant_type": "authorization_code"}, open_url)


def current_tokens(
    app: StravaApp, tokens: Tokens, now: datetime, open_url: OpenUrl = urllib.request.urlopen
) -> Tokens:
    """`tokens`, or new ones if they expire soon. Strava may issue a new refresh token too."""
    if tokens.expires_at - now > TOKEN_REFRESH_MARGIN:
        return tokens
    return token_request(app, {"refresh_token": tokens.refresh_token, "grant_type": "refresh_token"}, open_url)


class StravaClient:
    """Reads the authorized athlete's activities."""

    def __init__(self, access_token: str, open_url: OpenUrl = urllib.request.urlopen):
        self.access_token = access_token
        self.open_url = open_url

    def get(self, path: str, query: dict[str, str | int] | None = None) -> Any:
        url = f"{API_URL}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        )
        return read_json(request, self.open_url)

    def list_activities(self) -> list[dict]:
        """Every activity's summary, newest first."""
        activities: list[dict] = []
        page = 1
        while True:
            batch = self.get("/athlete/activities", {"page": page, "per_page": MAX_ACTIVITIES_PER_PAGE})
            activities.extend(batch)
            if len(batch) < MAX_ACTIVITIES_PER_PAGE:
                return activities
            page += 1

    def activity_details(self, activity_id: str) -> dict:
        """The activity's full description, which unlike its summary has the private note."""
        return self.get(f"/activities/{activity_id}")

    def activity_streams(self, activity_id: str) -> dict:
        """The recorded channels Strava has for the activity, keyed by channel name. NotFound if it
        has none, e.g. an activity entered by hand."""
        return self.get(
            f"/activities/{activity_id}/streams", {"keys": ",".join(STREAM_KEYS), "key_by_type": "true"}
        )
