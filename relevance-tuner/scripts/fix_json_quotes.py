#!/usr/bin/env python3
"""
Fix German curly quotes in JSONL files that break JSON parsing.

Problem: Claude agents sometimes output German quotation marks („ " ")
inside JSON string values, which breaks JSON parsing because they appear
as unescaped quotes within already-quoted strings.

Example broken line:
{"title": "Anwälte wittern „Skandal" um Bar", ...}

The „ and " characters need to be escaped as \" when inside JSON strings.

Usage:
    python scripts/fix_json_quotes.py data/reviewed/agent_results/*.jsonl
"""

import sys
import json
import re


def fix_line(line: str) -> str:
    """
    Fix German quotes inside JSON string values.

    Strategy: Parse the line character by character, tracking whether
    we're inside a JSON string value or not. When inside a string,
    replace German quotes with escaped regular quotes.
    """
    result = []
    in_string = False
    i = 0

    while i < len(line):
        char = line[i]

        # Check for escaped characters
        if char == '\\' and i + 1 < len(line):
            result.append(char)
            result.append(line[i + 1])
            i += 2
            continue

        # Toggle string state on regular quotes
        if char == '"':
            in_string = not in_string
            result.append(char)
            i += 1
            continue

        # Replace German quotes when inside a string
        if in_string and char in '„""\u201E\u201C\u201D':
            result.append('\\"')
            i += 1
            continue

        # Also handle single German quotes
        if in_string and char in '‚''\u201A\u2018\u2019':
            result.append("\\'")
            i += 1
            continue

        result.append(char)
        i += 1

    return ''.join(result)


def process_file(filename: str) -> tuple[int, int, int]:
    """Process a JSONL file and fix JSON errors. Returns (total, fixed, errors)."""
    fixed_lines = []
    total = 0
    fixed_count = 0
    error_count = 0

    with open(filename, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            total += 1

            try:
                # Try parsing as-is
                obj = json.loads(line)
                fixed_lines.append(json.dumps(obj, ensure_ascii=False))
            except json.JSONDecodeError:
                # Try fixing German quotes
                fixed = fix_line(line)
                try:
                    obj = json.loads(fixed)
                    fixed_lines.append(json.dumps(obj, ensure_ascii=False))
                    fixed_count += 1
                except json.JSONDecodeError as e:
                    # Keep original and report error
                    print(f"  Line {line_num}: {line[:60]}...")
                    fixed_lines.append(line)
                    error_count += 1

    with open(filename, 'w', encoding='utf-8') as f:
        f.write('\n'.join(fixed_lines) + '\n')

    return total, fixed_count, error_count


def main():
    if len(sys.argv) < 2:
        print("Usage: python fix_json_quotes.py <file1.jsonl> [file2.jsonl ...]")
        sys.exit(1)

    total_files = 0
    total_fixed = 0
    total_errors = 0

    for filename in sys.argv[1:]:
        total, fixed, errors = process_file(filename)
        if errors > 0:
            status = f"{fixed} fixed, {errors} ERRORS"
        elif fixed > 0:
            status = f"{fixed} fixed"
        else:
            status = "OK"
        print(f"{filename}: {total} lines, {status}")
        total_files += 1
        total_fixed += fixed
        total_errors += errors

    print(f"\nTotal: {total_files} files, {total_fixed} lines fixed, {total_errors} errors")

    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
