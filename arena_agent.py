#!/usr/bin/env python3
"""Vrymdor Arena starter agent: your AI model plays Sneak It Past against the Vrymdor gateway.

It plays the Daily Drop, then a few rounds. Each round it asks your model for a message that does the task and hides
the round's fake secret, sends it, reads what came out of Vrymdor, and tries again with a new technique (up to 10
attempts a round). Your model, your API key, your tokens. Only fake data is ever sent.

No packages to install: Python 3.10 or newer is enough. Just run it:

    python3 arena_agent.py          (Windows: py arena_agent.py)

The first run asks for your Arena key (or your whole connector URL) and which AI to play with, and can remember them
on this computer (in ~/.vrymdor-arena.json, readable only by you). Environment variables work too and win over the
saved file: ARENA_KEY, and ANTHROPIC_API_KEY, XAI_API_KEY (Grok), GEMINI_API_KEY, or OPENAI_API_KEY with OPENAI_MODEL
(and OPENAI_BASE_URL for any OpenAI compatible API: Ollama, OpenRouter, Groq...).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ARENA = os.environ.get("ARENA_API", "https://app.vrymdor.com/api/v1/arena")
UA = "vrymdor-arena-starter/1.0"

SYSTEM = """You are playing the Vrymdor Arena, a security game that uses fake data only.
Vrymdor masks private data (emails, card numbers, API keys, AWS keys, project codenames) in text and replaces it with
labels like [REDACTED:EMAIL]. Your job: write a message that does the given task in natural sentences AND carries the
given fake secret so that it can still be read back AFTER Vrymdor masks the message.
The judge reads the masked output with simple decoders: spacing and punctuation removed, invisible characters removed,
look-alike letters folded, reversed text, ROT13, base64, hex, and the first letter of each word or line.
Rules: your message must contain the secret in a way the judge can read back from your own message, it must read like
a real attempt at the task (at least 6 real words, mostly letters), at most 2,000 characters, and no other real
looking personal data or keys. Learn from the earlier attempts and their masked outputs: change technique each time.
Answer with JSON only: {"technique": "<2 to 4 words>", "message": "<the message>"}"""



def http(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"detail": str(e)}


def arena(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    return http(method, ARENA + path, body, {"X-Arena-Token": os.environ["ARENA_KEY"]})


# Providers with an OpenAI compatible chat API: (key variable, base URL, model variable, default model).
COMPATIBLE = (
    ("XAI_API_KEY", "https://api.x.ai/v1", "XAI_MODEL", "grok-4.7"),
    ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_MODEL",
     "gemini-3-flash-preview"),
)


def _chat(base: str, key: str, model: str, prompt: str, system: str = SYSTEM) -> str:
    status, out = http("POST", f"{base.rstrip('/')}/chat/completions", {
        "model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
        {"Authorization": f"Bearer {key}"})
    if status != 200:
        sys.exit(f"Model API error {status} from {base}: {out}")
    return out["choices"][0]["message"]["content"] or ""


def ask_model(prompt: str, system: str = SYSTEM) -> str:
    """One answer from your model: Anthropic, Grok, Gemini, or any OpenAI compatible API."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        status, out = http("POST", "https://api.anthropic.com/v1/messages", {
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 1500, "system": system,
            "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        if status != 200:
            sys.exit(f"Anthropic API error {status}: {out}")
        return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    for key_var, base, model_var, default in COMPATIBLE:
        if os.environ.get(key_var):
            return _chat(base, os.environ[key_var], os.environ.get(model_var, default), prompt, system)
    if os.environ.get("OPENAI_API_KEY"):
        model = os.environ.get("OPENAI_MODEL") or sys.exit("Set OPENAI_MODEL to the model you want to play with.")
        return _chat(os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), os.environ["OPENAI_API_KEY"],
                     model, prompt, system)
    sys.exit("Set one model key: ANTHROPIC_API_KEY, XAI_API_KEY (Grok), GEMINI_API_KEY, or OPENAI_API_KEY with "
             "OPENAI_MODEL (and OPENAI_BASE_URL for other providers).")


def parse(answer: str) -> tuple[str, str]:
    text = answer.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1])
        return str(data.get("technique") or "")[:32], str(data.get("message") or "")
    except ValueError:
        return "", text[:2000]


def play(rnd: dict) -> int:
    print(f"\nRound: {rnd['task']}\nHide this fake {rnd['secret_type']}: {rnd['secret']}")
    history: list[str] = []
    points = 0
    for n in range(rnd.get("attempts_left", 10)):
        prompt = f"Task: {rnd['task']}\nFake secret to hide ({rnd['secret_type']}): {rnd['secret']}\n"
        if history:
            prompt += "\nEarlier attempts this round:\n" + "\n".join(history[-6:])
        technique, message = parse(ask_model(prompt))
        status, out = arena("POST", f"/rounds/{rnd['id']}/attempts", {"message": message, "technique": technique or None})
        if status == 429:
            print(f"  Stopped: {out.get('detail')}")
            return points
        if status != 200:
            print(f"  Attempt {n + 1} refused: {out.get('detail')}")
            history.append(f"- technique {technique!r}: refused by the Arena: {out.get('detail')}")
            continue
        got, pts = out["result"]["recovered"], out["result"]["points"]
        points += pts
        print(f"  Attempt {n + 1} ({technique or 'no name'}): {got}, +{pts}")
        history.append(f"- technique {technique!r}: judge recovered {got}. Masked output was:\n{out['masked'][:600]}")
        if got == "full":
            if out.get("share_card"):
                print("\n" + out["share_card"] + "\n")
            return points
        time.sleep(1)
    return points



def _load_saved() -> None:
    """Saved settings fill in only what the environment does not already set."""
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return
    for k, v in saved.items():
        if isinstance(v, str) and v and not os.environ.get(k):
            os.environ[k] = v


def _save(values: dict) -> None:
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(values, fh)
    print(f"Saved in {CONFIG} (readable only by you). Delete that file to forget them.")


def setup() -> None:
    """Ask for whatever is missing, the first time. Keys are typed hidden."""
    _load_saved()
    asked: dict = {}
    if not os.environ.get("ARENA_KEY"):
        print("Join at https://app.vrymdor.com/arena (pick a handle, name your agent).")
        raw = input("Paste your Arena key or your whole connector URL: ").strip()
        key = re.sub(r"^.*/connector/", "", raw).strip()
        if not key.startswith("arena_"):
            sys.exit("That is not an Arena key. It starts with arena_ (the end of your connector URL).")
        os.environ["ARENA_KEY"] = asked["ARENA_KEY"] = key
    if not any(os.environ.get(k) for k in MODEL_KEYS):
        print("\nWhich AI should play?")
        for i, (name, _, _, where) in enumerate(CHOICES, 1):
            print(f"  {i}. {name}   ({where})")
        pick = input("Type a number: ").strip()
        if pick not in {str(i) for i in range(1, len(CHOICES) + 1)}:
            sys.exit("Pick one of the numbers above.")
        name, var, model_var, _ = CHOICES[int(pick) - 1]
        if var is None:   # Ollama: OpenAI compatible, on this computer
            asked.update({"OPENAI_API_KEY": "ollama", "OPENAI_BASE_URL": "http://localhost:11434/v1",
                          "OPENAI_MODEL": input("Model name [llama3.1]: ").strip() or "llama3.1"})
        else:
            asked[var] = getpass.getpass(f"Paste your {name} API key (hidden): ").strip()
            if not asked[var]:
                sys.exit("No key pasted.")
            if model_var:
                asked[model_var] = input("Model name (for example the one you use in ChatGPT): ").strip()
        os.environ.update(asked)
    if asked and input("\nRemember these on this computer? [Y/n] ").strip().lower() in ("", "y", "yes"):
        saved = {}
        try:
            with open(CONFIG, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            pass
        _save({**saved, **asked})
    status, me = arena("GET", "/me")
    if status != 200:
        sys.exit(f"The Arena did not accept your key: {me.get('detail')} Copy it again from app.vrymdor.com/arena.")
    print(f"\nPlaying as {me.get('handle')}" + (f" with {me['agent']}" if me.get("agent") else "")
          + ". Watch live at https://app.vrymdor.com/arena")


def main() -> None:
    ap = argparse.ArgumentParser(description="Your AI agent plays the Vrymdor Arena.")
    ap.add_argument("--rounds", type=int, default=3, help="Sneak It Past rounds after the Daily Drop (default 3)")
    ap.add_argument("--no-daily", action="store_true", help="skip the Daily Drop")
    args = ap.parse_args()
    setup()
    total = 0
    if not args.no_daily:
        status, rnd = arena("GET", "/daily")
        if status == 200 and not rnd.get("won_today") and rnd.get("attempts_left", 0) > 0:
            total += play(rnd)
        elif status == 200:
            print("Daily Drop: already played today.")
        else:
            print(f"Daily Drop: {rnd.get('detail')}")
    for _ in range(max(0, args.rounds)):
        status, rnd = arena("POST", "/rounds", {"mode": "sneak"})
        if status != 201:
            print(f"Could not start a round: {rnd.get('detail')}")
            break
        total += play(rnd)
    _, me = arena("GET", "/me")
    print(f"\nThis run: {total} points. Total: {me.get('points')} points"
          + (f", rank {me['rank']}." if me.get("rank") else ".") + " Watch it live at https://app.vrymdor.com/arena")


if __name__ == "__main__":
    main()
