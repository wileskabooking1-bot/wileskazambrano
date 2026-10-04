import os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DESK_SECRET", "test-secret")
os.environ.setdefault("TYPESAFE_API_KEY", "ts-test")
os.environ.setdefault("JUDGE_URL", "http://judge.invalid/judge")
os.environ["DESK_DB"] = os.path.join(tempfile.mkdtemp(), "desk.db")
