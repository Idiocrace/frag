"""Generate supporter tokens.

Importable::

    from gen_supporter_token import generate
    tokens = generate(count=5, note="batch issued 2026-06-02")
    for t in tokens:
        print(t.token)

Or from the command line::

    py gen_supporter_token.py            # one token, no note
    py gen_supporter_token.py 5          # five tokens
    py gen_supporter_token.py 3 "patreon batch June"

Tokens are persisted to the same file the running frag server reads, so
they're immediately claimable.  The default location is
``userdata/.supporter_tokens.json`` next to the other server state; set
``FRAG_SUPPORTER_TOKENS`` to override.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

from libs.supporter_tokens import SupporterToken, SupporterTokenStore


def _default_store_path() -> Path:
    env = os.environ.get("FRAG_SUPPORTER_TOKENS")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    return here / "userdata" / ".supporter_tokens.json"


def generate(
    count: int = 1,
    note: str = "",
    store_path: Path | None = None,
) -> list[SupporterToken]:
    """Generate *count* fresh tokens and persist them to the store.

    Returns the list of created :class:`SupporterToken` instances so the
    caller can print / email / paste them.  The tokens are unclaimed.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    store = SupporterTokenStore(store_path or _default_store_path())
    return store.issue_many(count, note=note)


def _print_tokens(tokens: Iterable[SupporterToken]) -> None:
    for t in tokens:
        print(t.token)


def main(argv: list[str]) -> int:
    args = list(argv)
    count = 1
    note = ""
    if args:
        try:
            count = int(args[0])
        except ValueError:
            print(
                f"first arg must be an integer count, got {args[0]!r}", file=sys.stderr
            )
            return 2
    if len(args) >= 2:
        note = args[1]
    if len(args) > 2:
        print("too many arguments; expected: COUNT [NOTE]", file=sys.stderr)
        return 2

    tokens = generate(count=count, note=note)
    _print_tokens(tokens)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
