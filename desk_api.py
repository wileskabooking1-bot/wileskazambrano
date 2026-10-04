"""judge.py plus the two book routes RISK needs, behind the same tunnel and secret.

    uvicorn desk_api:app --host 0.0.0.0 --port 8080

judge.py stays a pure judge. The book routes make no decision either: RISK closed
the position, this only records that it did.
"""
from fastapi import Header, HTTPException
from judge import app, DESK_SECRET
import book


def _auth(authorization: str):
    if authorization != f"Bearer {DESK_SECRET}":
        raise HTTPException(401, "bad desk secret")


@app.get("/book/held")
def held(authorization: str = Header("")):
    _auth(authorization)
    return {"held": book.held()}


@app.post("/book/release")
def release(authorization: str = Header("")):
    """RISK calls this the moment a close is filled. Nobody else does."""
    _auth(authorization)
    was = book.held()
    book.release()
    return {"released": was}
