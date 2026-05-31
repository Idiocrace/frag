import sqlite3
import sys
from pathlib import Path

DB_PATH = str(Path(__file__).parent / "tokens.db")

action = sys.argv[1] if len(sys.argv) > 1 else None


def add_token(token, lifetime):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, lifetime INTEGER)"
    )
    c.execute("INSERT INTO tokens (token, lifetime) VALUES (?, ?)", (token, lifetime))
    conn.commit()
    conn.close()


def remove_token(token):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def list_tokens():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT token, lifetime FROM tokens")
    tokens = c.fetchall()
    conn.close()
    return tokens


def validate_token(token):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, lifetime INTEGER)"
    )
    c.execute("SELECT lifetime FROM tokens WHERE token = ?", (token,))
    result = c.fetchone()
    conn.close()
    if result:
        return True
    return False


def generate_token():
    import secrets

    return secrets.token_hex(16)


def action_add():
    token = generate_token()
    lifetime = int(input("Enter token lifetime in seconds: "))
    add_token(token, lifetime)
    print(f"Token added: {token} with lifetime {lifetime} seconds")


def action_remove():
    token = input("Enter token to remove: ")
    remove_token(token)
    print(f"Token removed: {token}")


def action_list():
    tokens = list_tokens()
    for token, lifetime in tokens:
        print(f"Token: {token}, Lifetime: {lifetime} seconds")


def action_validate():
    token = sys.argv[2] if len(sys.argv) > 2 else input("Enter token to validate: ")
    if validate_token(token):
        print("valid")
    else:
        print("invalid")


actions = {
    "add": action_add,
    "remove": action_remove,
    "list": action_list,
    "validate": action_validate,
}

if action in actions:
    actions[action]()
