import sys

from . import plan_to_json

if __name__ == "__main__":
    content = sys.stdin.read()
    print(plan_to_json(content))
