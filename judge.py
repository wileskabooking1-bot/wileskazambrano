import logging
import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typesafe_sdk import (AsyncTypeSafeClient, TypeSafeAPIConnectionError, TypeSafeAPIError,
                          TypeSafeBadRequestError, TypeSafeError, TypeSafeRateLimitError,
                          TypeSafeUnprocessableEntityError)
from questions import SETS

DESK_SECRET = os.environ["DESK_SECRET"]     # for the bots. NOT the TypeSafe key.
client = AsyncTypeSafeClient()              # reads TYPESAFE_API_KEY itself
app = FastAPI()
log = logging.getLogger("judge")


class Ask(BaseModel):
    question_set: str
    state: dict


@app.post("/judge")
async def judge(ask: Ask, authorization: str = Header("")):
    if authorization != f"Bearer {DESK_SECRET}":
        raise HTTPException(401, "bad desk secret")
    if ask.question_set not in SETS:
        raise HTTPException(422, f"unknown question set {ask.question_set}")

    qs = SETS[ask.question_set]
    try:
        qs = qs(ask.state) if callable(qs) else qs    # pick builds options at call time
    except (KeyError, TypeError) as e:
        raise HTTPException(422, f"state does not fit {ask.question_set}: {e}")

    try:
        r = await client.system_one(state=ask.state, questions=qs)
    except (TypeSafeUnprocessableEntityError, TypeSafeBadRequestError) as e:
        raise HTTPException(422, f"jev rejected the question: {e}")     # never retried
    except TypeSafeRateLimitError as e:
        raise HTTPException(429, f"jev rate limit: {e}")
    except (TypeSafeAPIConnectionError, TypeSafeAPIError) as e:
        raise HTTPException(502, f"jev unavailable: {e}")
    except TypeSafeError as e:                                          # local validation
        raise HTTPException(422, f"bad question set: {e}")

    # log the model id, not the alias. Aliases move when a release ships.
    log.info("set=%s model=%s usage=%s", ask.question_set, r.model, r.usage.model_dump())

    # raw answers out. never flattened, never thresholded here.
    return {"model": r.model,
            "answers": {k: v.model_dump() for k, v in r.answers.items()},
            "usage": r.usage.model_dump()}
