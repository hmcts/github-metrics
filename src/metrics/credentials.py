"""Resolve the credential every GitHub call in a run is made with.

Two things can authenticate a run, and they are not equivalent. A GitHub App installation is granted
its permissions by the organisation rather than intersected with a user's, which is what makes branch
protection, the three alert families and the GraphQL pull-request searches readable at all: a
user-intersected fine-grained token is refused on every one of them. A personal access token stays
supported because it is what a developer already has in their shell, and a run that reads less is
better than a run nobody can start.

App auth is selected only when the whole set — an App id, an installation id and a private key — is
configured, so a half-set left over from an experiment falls back to the token rather than failing
the run with a partial configuration nobody meant to use. Selected on whether the key was NAMED,
though, not on whether it read back: a key that was pointed at and turns out to be missing or empty
is a broken App configuration, and fails the run rather than quietly authenticating as something
with fewer permissions than the one that was asked for.

Nothing here returns a credential that has not been proven usable: `resolve_credentials` builds the
object, and the caller mints once before collection starts so a wrong key stops the run at the point
a human is still watching it.
"""

import logging
import os
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from time import sleep
from typing import Protocol

import jwt
from requests import RequestException, Response, Session

API_URL = "https://api.github.com"
REQUEST_TIMEOUT = 30

APP_IDENTIFIER_VARIABLE = "GH_APP_ID"
INSTALLATION_IDENTIFIER_VARIABLE = "GH_APP_INSTALLATION_ID"
PRIVATE_KEY_PATH_VARIABLE = "GH_APP_PRIVATE_KEY_PATH"
PRIVATE_KEY_VARIABLE = "GH_APP_PRIVATE_KEY"
ACCESS_TOKEN_VARIABLE = "GH_TOKEN"  # noqa: S105 — the NAME of an environment variable, not a secret

# GitHub refuses a JWT whose lifetime exceeds ten minutes, and refuses one whose `iat` is in its own
# future — which a clock a few seconds fast will produce. Both numbers are GitHub's own guidance.
JWT_LIFETIME = timedelta(minutes=10)
JWT_BACKDATE = timedelta(seconds=60)

RENEWAL_MARGIN = timedelta(minutes=5)
"""How long before its stated expiry a held installation token is replaced.

GitHub issues installation tokens with an hour's life, and a collection of 1850 repositories runs
past that, so the token WILL expire mid-run. Replacing it early is what stops a request being sent
with a token that expires while it is in flight — the failure the 401 retry catches and should
never have to.
"""

MAXIMUM_EXCHANGE_ATTEMPTS = 3
"""How many times the token exchange is attempted before the run is failed.

The same bound `GitHubClient` gives an evidence call, for the same reason: a run started overnight
should not die because GitHub had a bad second, and it should not hang for an hour either.
"""

REDACTION = "[redacted]"

MINIMUM_SECRET_LENGTH = 12
"""How long a fragment has to be before blanking it is worth making a message less readable.

A PEM's `-----END-----` line is not a secret, and a rule that blanked every short string would turn
GitHub's account of a refusal into a row of markers. Everything actually worth hiding is longer than
this: a PEM's body is 64-character base64 lines, and a token or a JWT is longer again.
"""


class CredentialsError(RuntimeError):
    """Report that a run cannot authenticate at all.

    Deliberately not a `GitHubError`. That exception carries an `AvailabilityReason`, which grades ONE
    repository's evidence and lets a collection continue past it; a credentials failure grades the
    whole run, and there is nothing for the next repository to try.
    """


class GitHubCredentials(Protocol):
    """Supply the bearer token one GitHub request is made with, and replace it when it is refused."""

    def token(self) -> str:
        """Return the token to send on the next request, minting or renewing one if that is needed."""
        ...

    def refresh(self) -> bool:
        """Replace the current token, reporting whether a genuinely new one was obtained.

        False is the answer that matters: it tells a caller holding a 401 that retrying would send
        the same refused credential again, so the failure is classified where it was raised.
        """
        ...


class PersonalAccessToken:
    """Hold one long-lived token exactly as the environment gave it."""

    def __init__(self, value: str) -> None:
        self.value = value

    def token(self) -> str:
        """Return the configured token."""
        return self.value

    def refresh(self) -> bool:
        """Report that there is nothing to refresh, because a PAT is the only token there will be."""
        return False


class AppInstallation:
    """Mint and hold an installation access token for one GitHub App installation.

    The token exchange is the one GitHub call in this project that does not go through `GitHubClient`,
    because it is what mints the credential that client sends. It is also deliberately absent from
    `call_outcomes`: that counter measures evidence collection, and an authentication call is not
    evidence.

    One token is minted and held rather than one per request, and it is replaced early — see
    `RENEWAL_MARGIN` — so that a collection lasting longer than GitHub's hour never sends an expired
    one. Both entry points take `self.lock`, which is DEFENSIVE AND DOCUMENTED AS SUCH: there is no
    `threading`, `asyncio` or `concurrent.futures` anywhere else in this codebase and collection is a
    sequential loop, so the lock is never contended today. It is here because the thing it guards is
    a check-then-act on shared state, which is exactly what breaks first the day anything collects in
    parallel, and because a re-mint storm is invisible in a log that only ever shows the successful
    exchange.

    NOTHING ON THESE PATHS LOGS THE KEY, THE JWT OR THE TOKEN. The assertion goes into a header
    dictionary handed straight to `requests`, every message a failure carries goes through
    `redacted`, and the one DEBUG line a success writes says when the token expires rather than what
    it is. That guarantee covers this project's own logging and nothing else: setting
    `http.client.HTTPConnection.debuglevel`, as some HTTP tracing recipes do, prints every request
    header to stdout with `Authorization` among them and defeats all of it in one line. Nothing in
    this project enables it, and nothing should.
    """

    def __init__(
        self,
        app_identifier: int,
        installation_identifier: int,
        private_key: str,
        session: Session,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        pause: Callable[[float], None] = sleep,
    ) -> None:
        self.app_identifier = app_identifier
        self.installation_identifier = installation_identifier
        self.private_key = private_key
        self.session = session
        self.clock = clock
        self.pause = pause
        self.lock = threading.Lock()
        self.value: str | None = None
        self.expires_at: datetime | None = None

    def token(self) -> str:
        """Return the held installation token, minting one when there is none or it is nearly spent."""
        with self.lock:
            if self.value is None:
                return self.mint()
            if self.expiring():
                return self.renew(self.value)
            return self.value

    def renew(self, held: str) -> str:
        """Replace a token nearing expiry, keeping the one in hand if the exchange fails while it lives.

        THE MARGIN IS THE RETRY BUDGET, and this is what spends it. `RENEWAL_MARGIN` exists so a token
        is replaced BEFORE it stops working, which means the credential still holds a usable one at the
        moment the exchange is attempted. Failing the run there discarded a working token — and with it
        a collection that may be hours in, its inventory unwritten and its alert observations never
        appended — because GitHub had a bad second five minutes early. Every later request attempts the
        exchange again, so the margin buys minutes of retries rather than the three seconds `mint` backs
        off for; only once the held token has genuinely expired is there nothing left to fall back on,
        and the failure becomes the run's.

        A 401 does not come through here. `refresh` mints unconditionally, because there the held token
        was refused rather than merely old, and falling back to it would send the same refused string.
        """
        try:
            return self.mint()
        except CredentialsError as exception:
            if self.expired():
                raise
            # At WARNING, because a renewal that failed once is invisible otherwise: the run carries on
            # with a token that still works, and the only sign the exchange is broken would be the run
            # dying at the margin's end with no account of the attempts that led there.
            logging.warning(
                "the GitHub App installation token could not be renewed early, continuing with the held "
                "token until it expires at %s: %s",
                self.expires_at,
                exception,
            )
            return held

    def refresh(self) -> bool:
        """Mint a replacement token unconditionally, and report that one was obtained."""
        with self.lock:
            self.mint()
        return True

    def expired(self) -> bool:
        """Report whether the held token is PAST the expiry GitHub stated, rather than merely near it.

        The distinction `renew` turns on: `expiring` is true for the whole margin, during which the
        token still authenticates, and only this says there is nothing usable left to fall back on.
        """
        return self.expires_at is not None and self.clock() >= self.expires_at

    def expiring(self) -> bool:
        """Report whether the held token is inside the margin at which it is replaced early.

        An expiry nobody could read is not treated as expiring. The token GitHub just issued works
        now, and re-minting on every request because its expiry was unparseable would turn a cosmetic
        problem into an authentication call per evidence call.
        """
        return self.expires_at is not None and self.clock() + RENEWAL_MARGIN >= self.expires_at

    def assertion(self) -> str:
        """Sign the short-lived JWT that proves this process holds the App's private key."""
        issued_at = self.clock() - JWT_BACKDATE
        payload = {
            "iat": int(issued_at.timestamp()),
            "exp": int((issued_at + JWT_BACKDATE + JWT_LIFETIME).timestamp()),
            # A string because PyJWT refuses a numeric `iss`, though GitHub's own documentation writes
            # the App id as the number it is.
            "iss": str(self.app_identifier),
        }
        try:
            return jwt.encode(payload, self.private_key, algorithm="RS256")
        # FOUR TYPES BECAUSE PyJWT RAISES FOUR. A malformed PEM arrives as `InvalidKeyError`, but the
        # two likeliest wrong keys do not: a passphrase-protected key reaches `cryptography` with no
        # password and comes back as `TypeError`, and a PUBLIC key parses, then fails as
        # `AttributeError` when something with no `sign` is asked to sign. Both are ordinary
        # mistakes — the wrong `.pem` in the directory — and neither is a `PyJWTError`, so a
        # narrower clause ends the run in a traceback naming `cryptography` internals instead of in
        # the one line saying the key cannot be used. It also loses the redaction: an escaped
        # exception carries `self.private_key` in its frame locals.
        except (jwt.PyJWTError, ValueError, TypeError, AttributeError) as exception:
            detail = redacted(str(exception), self.private_key)
            message = f"the GitHub App private key could not be used to sign a request: {detail}"
            raise CredentialsError(message) from exception

    def mint(self) -> str:
        """Exchange the App's JWT for an installation access token, and hold it with its expiry.

        Called with `self.lock` ALREADY HELD, and it does not take the lock itself: a
        `threading.Lock` is not reentrant, so an inner acquisition would deadlock rather than fail.

        The JWT is signed once for the whole attempt sequence. It outlives every retry by minutes,
        and re-signing would put another RSA operation between GitHub and the run for no gain. It is
        also held in a local rather than only in the header, so that every message this method builds
        can be checked for it on the way out.
        """
        assertion = self.assertion()
        url = f"{API_URL}/app/installations/{self.installation_identifier}/access_tokens"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {assertion}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.session.post(url, headers=headers, timeout=REQUEST_TIMEOUT)
            except RequestException as exception:
                detail = redacted(str(exception), assertion, self.private_key)
                if attempt == MAXIMUM_EXCHANGE_ATTEMPTS:
                    message = (
                        f"the GitHub App installation token could not be obtained after "
                        f"{MAXIMUM_EXCHANGE_ATTEMPTS} attempts: {detail}"
                    )
                    raise CredentialsError(message) from exception
                self.wait(attempt, detail)
                continue
            if response.ok:
                return self.hold(response)
            detail = f"HTTP {response.status_code}: {refusal(response, assertion, self.private_key)}"
            if transient(response.status_code) and attempt < MAXIMUM_EXCHANGE_ATTEMPTS:
                self.wait(attempt, detail)
                continue
            message = f"GitHub refused to issue an installation token for app {self.app_identifier} ({detail})"
            raise CredentialsError(message)

    def wait(self, attempt: int, detail: str) -> None:
        """Back off between exchange attempts, saying which attempt failed and what GitHub said.

        At WARNING, because a run that eventually authenticated still spent time not collecting, and
        an exchange that needs two attempts every night is a problem worth seeing before it needs
        four. `detail` is GitHub's own account of the failure and nothing else — never the JWT that
        was refused, the key that signed it, or a token an earlier exchange returned. `mint` puts it
        through `redacted` before calling this, so a refusal that quotes the request back at us is
        blanked rather than logged.
        """
        backoff = float(2 ** (attempt - 1))
        logging.warning(
            "the GitHub App installation token exchange failed (attempt %s of %s), retrying in %.0fs: %s",
            attempt,
            MAXIMUM_EXCHANGE_ATTEMPTS,
            backoff,
            detail,
        )
        self.pause(backoff)

    def hold(self, response: Response) -> str:
        """Read one successful exchange and keep the token it issued, with the expiry it stated."""
        try:
            payload = response.json()
        except ValueError as exception:
            message = f"GitHub's installation token exchange returned an unreadable body: {exception}"
            raise CredentialsError(message) from exception
        self.value = minted_token(payload)
        self.expires_at = minted_expiry(payload)
        logging.debug("minted a GitHub App installation token expiring at %s", self.expires_at)
        return self.value


def redacted(text: str, *secrets: str) -> str:
    """Blank secret material out of a string on its way to a log line or an exception message.

    BELT AND BRACES, AND DELIBERATELY SO. Nothing in this module puts a key, a JWT or a token into a
    message: what these messages carry is GitHub's own account of a refusal, or `cryptography`'s
    account of a key it could not parse. But both come out of libraries this project does not
    control, and a parser that quotes the input it was handed — which is ordinary, helpful behaviour
    almost everywhere else — would publish the App's private key into every log the run writes. One
    pass over a short string closes that off permanently, in the one place every such message goes.

    Each secret is matched WHOLE AND LINE BY LINE, because a message quoting a single base64 line of
    a PEM has still published part of the key. Longest first, so a whole key becomes one marker
    rather than one per line.
    """
    fragments = {fragment.strip() for secret in secrets for fragment in (secret, *secret.splitlines())}
    for fragment in sorted(fragments, key=len, reverse=True):
        if len(fragment) >= MINIMUM_SECRET_LENGTH:
            text = text.replace(fragment, REDACTION)
    return text


def transient(status: int) -> bool:
    """Report whether a refused exchange is worth attempting again.

    A 401 or a 404 is a statement about the key, the App or the installation, and the second identical
    request gets the identical answer — retrying it only delays the message a human needs. A 5xx or a
    429 is GitHub having a bad minute, which is the one failure here a retry actually fixes.
    """
    return status >= HTTPStatus.INTERNAL_SERVER_ERROR or status == HTTPStatus.TOO_MANY_REQUESTS


def refusal(response: Response, *secrets: str) -> str:
    """Summarise a refused exchange in one line, preferring GitHub's own `message` to its whole body.

    One line and 200 characters at most, because the alternative is a multi-kilobyte HTML error page
    from a proxy in the path. GitHub's `message` is the part worth reading — `Integration not found`
    and `A JSON web token could not be decoded` are different problems with different fixes, and both
    arrive as a bare 404 or 401 otherwise. Nothing of the REQUEST is copied out.

    REDACTION HAPPENS HERE, BEFORE THE TRUNCATION, and that order is the whole point of doing it in
    this function rather than in the caller. A body that quotes the request back is longer than the
    limit, so cutting first leaves the first 200 characters of a JWT in the message — which no later
    pass can match against the whole one, and which is a leak whatever the marker says. Blanking
    first replaces the secret entirely, and what the limit then cuts is the summary.
    """
    try:
        payload: object = response.json()
    except ValueError:
        payload = None
    message = payload.get("message") if isinstance(payload, Mapping) else None
    text = message if isinstance(message, str) and message else response.text
    return " ".join(redacted(text, *secrets).split())[:200]


def minted_token(payload: object) -> str:
    """Read the token out of an exchange GitHub called a success, refusing a body without one."""
    token = payload.get("token") if isinstance(payload, Mapping) else None
    if not isinstance(token, str) or not token:
        message = "GitHub returned no installation token in a successful exchange"
        raise CredentialsError(message)
    return token


def minted_expiry(payload: object) -> datetime | None:
    """Read `expires_at` as a timezone-aware UTC instant, treating anything unreadable as unknown.

    An unknown expiry is not a failure. The token GitHub just issued works now, and a caller that
    cannot see when it stops working simply falls back to the 401 retry — which is the path a
    revoked token takes anyway.
    """
    raw = payload.get("expires_at") if isinstance(payload, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        logging.warning("GitHub reported an unreadable installation token expiry: %s", raw)
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def key_configured(environment: Mapping[str, str]) -> bool:
    """Report whether a key SOURCE is named, without reading what it holds.

    Selecting App auth on the source rather than on the key's contents is what keeps two different
    failures apart. A key nobody named is a half-set, and the run falls back to the token as
    documented; a key that WAS named and turns out to be unreadable or empty is a broken
    configuration, and `private_key` fails the run saying which one it is.

    Deciding after the read collapsed both into the fallback and got each of them wrong: a stale
    `GH_APP_PRIVATE_KEY_PATH` left over from an experiment ended a perfectly good token-authenticated
    run, and an empty PEM at a path a fully configured App named silently downgraded that run to the
    token's much smaller permissions — reporting branch protection and all three alert families as
    unavailable, with nothing in the log pointing at the App configuration that was ignored.
    """
    return bool(environment.get(PRIVATE_KEY_PATH_VARIABLE) or environment.get(PRIVATE_KEY_VARIABLE))


def private_key(environment: Mapping[str, str]) -> str:
    r"""Return the GitHub App's private key, preferring a path on disk to the key in a variable.

    Called only when `key_configured` has already said one of the two is set, so every way out of
    here is a usable key or a `CredentialsError` naming the source that failed — never a quiet None
    that sends the run off to authenticate as something else.

    The path wins because it is the safer of the two: a PEM in an environment variable is visible to
    every child process and to anything that dumps the environment. A key set inline is accepted
    anyway — CI secret stores commonly cannot hold newlines — and its escaped `\\n` sequences are
    converted back, since a PEM with literal backslash-n in it will not parse.

    A leading `~` is expanded, because the variable does not always arrive from a shell that did it.
    `export GH_APP_PRIVATE_KEY_PATH=~/app.pem` is expanded by bash before this ever sees it, but the
    same line in a CI `env:` block, a compose file or a systemd unit is passed through literally —
    and those are the runs App auth exists for.
    """
    path = environment.get(PRIVATE_KEY_PATH_VARIABLE)
    if path:
        try:
            key = Path(path).expanduser().read_text(encoding="utf-8")
        # RuntimeError beside OSError because `expanduser` raises it, not an OSError, when a `~` has
        # no home directory to expand to — which is the container with no HOME and no passwd entry
        # that a `~` path is most likely to be handed to in the first place. ValueError beside both
        # because a file that is not UTF-8 fails the decode as `UnicodeDecodeError`, which is neither
        # of the other two: a DER key, or a `.pem` that is really a keystore, is an ordinary mistake
        # and belongs in the one line naming the path rather than in a traceback out of `main`.
        except (OSError, RuntimeError, ValueError) as exception:
            message = f"the GitHub App private key could not be read from {path}: {exception}"
            raise CredentialsError(message) from exception
        source = f"read from {path}"
    else:
        key = environment.get(PRIVATE_KEY_VARIABLE, "").replace("\\n", "\n")
        source = f"in {PRIVATE_KEY_VARIABLE}"
    # Checked here rather than left to the signing failure, because what the signer says about an
    # empty string is `Could not parse the provided public key` — true, and no help at all to someone
    # whose real problem is a file that was never written to.
    if not key.strip():
        message = f"the GitHub App private key {source} is empty"
        raise CredentialsError(message)
    return key


def identifier(value: str, name: str) -> int:
    """Read one numeric GitHub identifier out of the environment, naming the variable that is wrong."""
    try:
        return int(value)
    except ValueError as exception:
        message = f"{name} must be a number, and is {value!r}"
        raise CredentialsError(message) from exception


def resolve_credentials(session: Session, environment: Mapping[str, str] | None = None) -> GitHubCredentials:
    """Build the credential this run authenticates with, preferring an App installation to a token."""
    values = os.environ if environment is None else environment
    app = values.get(APP_IDENTIFIER_VARIABLE)
    installation = values.get(INSTALLATION_IDENTIFIER_VARIABLE)
    if app and installation and key_configured(values):
        return AppInstallation(
            identifier(app, APP_IDENTIFIER_VARIABLE),
            identifier(installation, INSTALLATION_IDENTIFIER_VARIABLE),
            private_key(values),
            session,
        )
    access_token = values.get(ACCESS_TOKEN_VARIABLE)
    if access_token:
        return PersonalAccessToken(access_token)
    message = (
        f"no GitHub credentials are configured: set {APP_IDENTIFIER_VARIABLE}, "
        f"{INSTALLATION_IDENTIFIER_VARIABLE} and {PRIVATE_KEY_PATH_VARIABLE} (or {PRIVATE_KEY_VARIABLE}) "
        f"to authenticate as a GitHub App, or {ACCESS_TOKEN_VARIABLE} to use a personal access token"
    )
    raise CredentialsError(message)
