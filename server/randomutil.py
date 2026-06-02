"""Random identifiers, plausible-looking decoy values, and shape detection.

Used by :mod:`obfconfigutil` to produce camouflaged config blobs, but kept
generic so it can be reused for fuzzing, test fixtures, etc.

Decoys mimic common real-world secret/value shapes so that a real entry
dropped into a sea of them is not visually distinguishable.  ``shape_of``
classifies a string into the same shape vocabulary, which lets callers
guarantee per-entry camouflage (every real value gets N decoy siblings of
the same shape).
"""

from __future__ import annotations

import base64
import random
import re
import string
from typing import Callable


# ---------------------------------------------------------------------------
# Primitive character pools
# ---------------------------------------------------------------------------

HEX_LOWER = "0123456789abcdef"
HEX_UPPER = "0123456789ABCDEF"
ALNUM = string.ascii_letters + string.digits
ALNUM_LOWER = string.ascii_lowercase + string.digits
URLSAFE = ALNUM + "-_"
B64_STD = ALNUM + "+/"


def hex_str(n: int, *, upper: bool = False) -> str:
    return "".join(random.choices(HEX_UPPER if upper else HEX_LOWER, k=n))


def alnum(n: int, *, lower: bool = False) -> str:
    return "".join(random.choices(ALNUM_LOWER if lower else ALNUM, k=n))


def urlsafe(n: int) -> str:
    return "".join(random.choices(URLSAFE, k=n))


def b64(n: int, *, padded: bool = True) -> str:
    raw = "".join(random.choices(B64_STD, k=n))
    if not padded:
        return raw
    return raw + "=" * random.randint(0, 2)


def python_identifier(k: int = 16) -> str:
    """Random valid Python identifier of exactly *k* characters."""
    first = random.choice(string.ascii_letters + "_")
    rest = "".join(random.choices(ALNUM + "_", k=k - 1))
    return first + rest


def unique_identifiers(count: int, *, length: int = 16, exclude: set[str] | None = None) -> list[str]:
    """Return *count* unique identifiers, avoiding any in *exclude*."""
    used = set(exclude or ())
    out: list[str] = []
    while len(out) < count:
        cand = python_identifier(length)
        if cand in used:
            continue
        used.add(cand)
        out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Real-looking fixtures for URLs / DBs / ARNs
# ---------------------------------------------------------------------------

API_HOSTS = (
    "api.github.com",
    "api.stripe.com",
    "api.openai.com",
    "api.anthropic.com",
    "api.twilio.com",
    "api.sendgrid.com",
    "api.cloudflare.com",
    "api.digitalocean.com",
    "api.linear.app",
    "api.notion.com",
    "api.slack.com",
    "hooks.slack.com",
    "discord.com/api",
    "api.dropboxapi.com",
    "graph.microsoft.com",
    "graph.facebook.com",
    "www.googleapis.com",
    "oauth2.googleapis.com",
    "sts.amazonaws.com",
    "s3.amazonaws.com",
    "dynamodb.us-east-1.amazonaws.com",
    "secretsmanager.us-west-2.amazonaws.com",
    "vault.hashicorp.cloud",
    "pixelateddream.net",
    "api.pixelateddream.net",
    "auth.pixelateddream.net",
    "registry.npmjs.org",
    "pypi.org",
    "hub.docker.com",
    "gitlab.com/api/v4",
    "bitbucket.org/api/2.0",
)

URL_PATH_WORDS = (
    "v1", "v2", "v3", "api", "users", "accounts", "orgs", "teams",
    "projects", "repos", "events", "webhooks", "tokens", "sessions",
    "billing", "subscriptions", "messages", "channels", "files",
    "uploads", "search", "query", "items", "entities", "records",
)

DB_HOSTS = (
    "db.internal",
    "postgres.internal",
    "primary.db.internal",
    "replica.db.internal",
    "rds-prod.cluster-abc123.us-east-1.rds.amazonaws.com",
    "cache.internal",
    "redis.internal",
    "redis-master.prod.svc.cluster.local",
    "mongo.internal",
    "mongo-0.mongo.prod.svc.cluster.local",
    "kafka-0.kafka.prod.svc.cluster.local",
    "elasticsearch.internal",
)

DB_NAMES = (
    "users", "sessions", "billing", "events", "analytics", "metrics",
    "auth", "prod", "staging", "main", "app", "core", "ledger", "audit",
)

DB_USERS = ("app", "service", "readonly", "writer", "admin", "worker")

AWS_REGIONS = (
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-central-1", "ap-southeast-1", "ap-northeast-1",
)

EMAIL_DOMAINS = (
    "gmail.com", "outlook.com", "yahoo.com", "protonmail.com",
    "pixelateddream.net", "example.org", "company.io", "hey.com",
)

FIRST_NAMES = (
    "alex", "sam", "jordan", "taylor", "morgan", "casey", "riley",
    "drew", "quinn", "avery", "blake", "rowan", "jess", "noah",
)


# ---------------------------------------------------------------------------
# Decoy value generators (each returns one plausible-looking string)
# ---------------------------------------------------------------------------


def decoy_pd_key() -> str:
    return "pd_" + hex_str(40)


def decoy_sk_live() -> str:
    return "sk_live_" + alnum(32)


def decoy_pk_live() -> str:
    return "pk_live_" + alnum(32)


def decoy_github_pat() -> str:
    return "ghp_" + alnum(36)


def decoy_github_oauth() -> str:
    return "gho_" + alnum(36)


def decoy_aws_access_key() -> str:
    return "AKIA" + "".join(random.choices(string.ascii_uppercase + string.digits, k=16))


def decoy_aws_secret() -> str:
    return b64(40)


def decoy_aws_arn() -> str:
    region = random.choice(AWS_REGIONS)
    account = "".join(random.choices(string.digits, k=12))
    service, resource = random.choice((
        ("iam", f"role/{alnum(random.randint(8, 16))}"),
        ("s3", f"bucket/{alnum(random.randint(6, 12), lower=True)}"),
        ("sqs", f"queue-{alnum(8, lower=True)}"),
        ("lambda", f"function:{alnum(random.randint(8, 16), lower=True)}"),
        ("secretsmanager", f"secret:{alnum(8, lower=True)}-{alnum(6)}"),
        ("dynamodb", f"table/{alnum(random.randint(6, 12), lower=True)}"),
    ))
    return f"arn:aws:{service}:{region}:{account}:{resource}"


def decoy_jwt() -> str:
    # Real-looking JWT header: {"alg":"HS256","typ":"JWT"} base64url-encoded
    headers = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9",
        "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9",
    )
    header = random.choice(headers)
    payload = urlsafe(random.randint(80, 200))
    sig = urlsafe(43)
    return f"{header}.{payload}.{sig}"


def decoy_hex_hash() -> str:
    return hex_str(random.choice((32, 40, 64)))


def decoy_uuid() -> str:
    return f"{hex_str(8)}-{hex_str(4)}-{hex_str(4)}-{hex_str(4)}-{hex_str(12)}"


def decoy_url() -> str:
    host = random.choice(API_HOSTS)
    segments = [random.choice(URL_PATH_WORDS) for _ in range(random.randint(1, 3))]
    if random.random() < 0.5:
        segments.append(hex_str(random.choice((8, 16, 24))))
    return f"https://{host}/" + "/".join(segments)


def decoy_db_url() -> str:
    scheme = random.choice(("postgres", "postgresql", "mysql", "redis", "mongodb+srv"))
    port = {
        "postgres": 5432, "postgresql": 5432, "mysql": 3306,
        "redis": 6379, "mongodb+srv": 27017,
    }[scheme]
    return (
        f"{scheme}://{random.choice(DB_USERS)}:{alnum(24)}@"
        f"{random.choice(DB_HOSTS)}:{port}/{random.choice(DB_NAMES)}"
    )


def decoy_bearer_token() -> str:
    return "Bearer " + urlsafe(random.randint(32, 64))


def decoy_generic_token() -> str:
    return "tok_" + urlsafe(random.randint(28, 48))


def decoy_dotted() -> str:
    return "_".join(alnum(random.randint(4, 10)) for _ in range(random.randint(2, 4)))


def decoy_ipv4() -> str:
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


def decoy_ipv6() -> str:
    return ":".join(hex_str(4) for _ in range(8))


def decoy_email() -> str:
    name = random.choice(FIRST_NAMES) + str(random.randint(1, 9999))
    return f"{name}@{random.choice(EMAIL_DOMAINS)}"


def decoy_pem_block() -> str:
    body_lines = [b64(64, padded=False) for _ in range(random.randint(8, 24))]
    body_lines.append(b64(random.randint(20, 60)))
    body = "\n".join(body_lines)
    return f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"


def decoy_basic_auth() -> str:
    raw = f"{random.choice(DB_USERS)}:{alnum(16)}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def decoy_port() -> int:
    return random.choice((80, 443, 3000, 5432, 6379, 8080, 8443, 27017, 9200))


def decoy_bool():
    return random.choice((True, False))


# Catalog of all generators with their nominal shape name.
DECOY_GENERATORS: dict[str, Callable[[], object]] = {
    "pd_key": decoy_pd_key,
    "sk_live": decoy_sk_live,
    "pk_live": decoy_pk_live,
    "github_pat": decoy_github_pat,
    "github_oauth": decoy_github_oauth,
    "aws_access_key": decoy_aws_access_key,
    "aws_secret": decoy_aws_secret,
    "aws_arn": decoy_aws_arn,
    "jwt": decoy_jwt,
    "hex_hash": decoy_hex_hash,
    "uuid": decoy_uuid,
    "url": decoy_url,
    "db_url": decoy_db_url,
    "bearer": decoy_bearer_token,
    "generic_token": decoy_generic_token,
    "dotted": decoy_dotted,
    "ipv4": decoy_ipv4,
    "ipv6": decoy_ipv6,
    "email": decoy_email,
    "pem": decoy_pem_block,
    "basic_auth": decoy_basic_auth,
    "port": decoy_port,
    "bool": decoy_bool,
}


def random_decoy() -> object:
    """Return one decoy of a randomly-chosen shape."""
    return random.choice(list(DECOY_GENERATORS.values()))()


# ---------------------------------------------------------------------------
# Shape detection — classify a value so real entries can be camouflaged
# with siblings of the same shape.
# ---------------------------------------------------------------------------


_SHAPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pd_key", re.compile(r"^pd_[0-9a-f]{40}$")),
    ("sk_live", re.compile(r"^sk_live_[A-Za-z0-9]{32}$")),
    ("pk_live", re.compile(r"^pk_live_[A-Za-z0-9]{32}$")),
    ("github_pat", re.compile(r"^ghp_[A-Za-z0-9]{36}$")),
    ("github_oauth", re.compile(r"^gho_[A-Za-z0-9]{36}$")),
    ("aws_access_key", re.compile(r"^AKIA[A-Z0-9]{16}$")),
    ("aws_arn", re.compile(r"^arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:")),
    ("jwt", re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")),
    ("uuid", re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")),
    ("db_url", re.compile(r"^(postgres|postgresql|mysql|redis|mongodb\+srv)://")),
    ("url", re.compile(r"^https?://")),
    ("bearer", re.compile(r"^Bearer ")),
    ("basic_auth", re.compile(r"^Basic ")),
    ("pem", re.compile(r"^-----BEGIN ")),
    ("email", re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")),
    ("ipv4", re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")),
    ("ipv6", re.compile(r"^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$")),
    ("hex_hash", re.compile(r"^[0-9a-f]{32}$|^[0-9a-f]{40}$|^[0-9a-f]{64}$")),
    ("generic_token", re.compile(r"^tok_[A-Za-z0-9_-]{28,}$")),
)


def shape_of(value: object) -> str | None:
    """Classify *value* into one of the known decoy shapes, if any.

    Returns the shape name (a key of :data:`DECOY_GENERATORS`) or ``None``
    when the value doesn't match any known pattern.
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "port"
    if not isinstance(value, str):
        return None
    for name, pattern in _SHAPE_PATTERNS:
        if pattern.match(value):
            return name
    return None
