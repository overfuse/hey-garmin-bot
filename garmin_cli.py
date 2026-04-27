#!/usr/bin/env python3

import os
import sys
import asyncio
import argparse
import getpass
from dotenv import load_dotenv
from garmin import login_to_garmin, upload_workout_to_garmin, token_from_session, upload_garmin_payload, workout_url
from chatgpt import plan_to_json
from garmin_convert import convert
from validate_garmin import validate_garmin_workout


def read_plan_from_args_or_stdin(file_path: str | None) -> str:
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    if sys.stdin.isatty():
        print("Paste/type your workout. Press Ctrl-D (Unix) or Ctrl-Z (Windows) then Enter to finish:", file=sys.stderr)
    return sys.stdin.read()


def run_login(email: str | None, password: str | None, out_path: str | None) -> None:
    email = email or os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = password or os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")
    if not email or not password:
        print("Error: email and password are required.", file=sys.stderr)
        sys.exit(1)

    print(f"Logging in as {email} (method={os.getenv('GARMIN_LOGIN_METHOD', 'garth')})...", file=sys.stderr)
    token = asyncio.run(login_to_garmin(email, password))
    print(f"Login OK. Token length: {len(token)}, preview: {token[:32]}...", file=sys.stderr)

    if out_path:
        with open(os.path.expanduser(out_path), "w", encoding="utf-8") as f:
            f.write(token)
        print(f"Token written to {out_path}", file=sys.stderr)
    else:
        print(token)


def chat_loop() -> None:
    print("Chat mode. Type workout lines. Commands: /upload, /preview, /convert, /validate, /clear, /help, /quit")
    buffer: list[str] = []
    token: str | None = None
    session_path = os.getenv("GARTH_SESSION_PATH", "~/.garth")
    while True:
        try:
            line = input("> ")
        except EOFError:
            print()
            break
        line = line.rstrip("\n")
        if not line:
            # empty line just separates paragraphs; keep collecting
            buffer.append("")
            continue
        if line.startswith("/"):
            cmd = line.strip().lower()
            if cmd in ("/quit", "/q"):
                break
            if cmd in ("/clear", "/x"):
                buffer.clear()
                print("Cleared.")
                continue
            if cmd in ("/help", "/h"):
                print("Commands: /upload (/u), /preview (/p), /convert (/c), /clear (/x), /help (/h), /quit (/q)")
                continue
            if cmd in ("/preview", "/p"):
                text = "\n".join(buffer).strip()
                if not text:
                    print("Nothing to preview. Type workout lines first.")
                    continue
                try:
                    wj = plan_to_json(text)
                    import json as _json
                    print(_json.dumps(wj, indent=2))
                except Exception as e:
                    print(f"Preview failed: {e}")
                continue
            if cmd in ("/convert", "/c"):
                text = "\n".join(buffer).strip()
                if not text:
                    print("Nothing to convert. Type workout lines first.")
                    continue
                try:
                    wj = plan_to_json(text)
                    gj = convert(wj)
                    import json as _json
                    print(_json.dumps(gj, indent=2))
                except Exception as e:
                    print(f"Convert failed: {e}")
                continue
            if cmd in ("/validate", "/v"):
                text = "\n".join(buffer).strip()
                if not text:
                    print("Nothing to validate. Type workout lines first.")
                    continue
                try:
                    wj = plan_to_json(text)
                    gj = convert(wj)
                    import json as _json
                    print("Workout JSON:")
                    print(_json.dumps(wj, indent=2, ensure_ascii=False))
                    print("Garmin JSON:")
                    print(_json.dumps(gj, indent=2, ensure_ascii=False))
                    errs, warns = validate_garmin_workout(gj)
                    if errs:
                        print("Errors:")
                        for e in errs:
                            print(f" - {e}")
                    if warns:
                        print("Warnings:")
                        for w in warns:
                            print(f" - {w}")
                    if not errs and not warns:
                        print("No issues found.")
                except Exception as e:
                    print(f"Validate failed: {e}")
                continue
            if cmd in ("/upload", "/u"):
                text = "\n".join(buffer).strip()
                if not text:
                    print("Nothing to upload. Type workout lines first.")
                    continue
                try:
                    if token is None:
                        token = token_from_session(session_path)
                    workout_id = upload_workout_to_garmin(token, text)
                    print(f"Uploaded: {workout_url(workout_id)}")
                    buffer.clear()
                except Exception as e:
                    print(f"Upload failed: {e}")
                continue
            print("Unknown command. Type /help.")
            continue
        # Regular text line
        buffer.append(line)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Upload a workout plan to Garmin Connect using credentials from .env",
    )
    parser.add_argument(
        "-f",
        "--file",
        dest="file",
        help="Path to a text file with workout description. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate and exit without uploading.",
    )
    parser.add_argument(
        "--print-garmin-json",
        action="store_true",
        help="Print the Garmin JSON payload that would be uploaded.",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Start interactive chat-like mode.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Test SSO login via login_to_garmin and print the token.",
    )
    parser.add_argument("--email", help="Garmin email (or set GARMIN_EMAIL).")
    parser.add_argument("--password", help="Garmin password (or set GARMIN_PASSWORD).")
    parser.add_argument(
        "--out",
        help="When used with --login, write the token to this file instead of stdout.",
    )
    args = parser.parse_args()

    session_path = os.getenv("GARTH_SESSION_PATH", "~/.garth")

    if args.login:
        run_login(args.email, args.password, args.out)
        return

    if args.chat:
        chat_loop()
        return

    plan_text = read_plan_from_args_or_stdin(args.file)
    if not plan_text.strip():
        print("Error: Empty workout plan. Provide --file or pipe text via stdin.", file=sys.stderr)
        sys.exit(1)

    try:
        # Pre-validate before uploading
        wj = plan_to_json(plan_text)
        gj = convert(wj)
        errs, warns = validate_garmin_workout(gj)
        if args.print_garmin_json:
            import json as _json
            print(_json.dumps(gj, indent=2))
        if errs:
            print("Validation errors:", file=sys.stderr)
            for e in errs:
                print(f" - {e}", file=sys.stderr)
            sys.exit(2)
        if warns:
            print("Validation warnings:", file=sys.stderr)
            for w in warns:
                print(f" - {w}", file=sys.stderr)
        if args.validate_only:
            sys.exit(0)
        token = token_from_session(session_path)
        # Upload exactly the validated payload
        result_id = upload_garmin_payload(token, gj)
        print(workout_url(result_id))
    except Exception as e:
        print(f"Failed to upload workout: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()


