"""Lambda: medicine-api (handler: api.handler)
Behind API Gateway HTTP API (route ANY /{proxy+}).
Env var: TOPIC_ARN
"""

import json
import os
import uuid
import hashlib
import secrets
import random

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr


# ---------- AWS resources ----------
ddb = boto3.resource("dynamodb")

users = ddb.Table("Users")
meds = ddb.Table("Medicines")
logs = ddb.Table("DoseLogs")

sns = boto3.client("sns")

TOPIC = os.environ.get("TOPIC_ARN")

IST = timezone(timedelta(hours=5, minutes=30))


# ---------- CORS ----------
CORS_HEADERS = {
    "Access-Control-Allow-Origin":
        "http://medicine-reminder-abhay-2026.s3-website-us-east-1.amazonaws.com",
    "Access-Control-Allow-Headers":
        "content-type, authorization",
    "Access-Control-Allow-Methods":
        "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Max-Age":
        "86400"
}


# ---------- helpers ----------
def now():
    return datetime.now(IST)


def resp(code, body):
    return {
        "statusCode": code,
        "headers": {
            "Content-Type": "application/json",
            **CORS_HEADERS
        },
        "body": json.dumps(
            body,
            default=lambda o:
                int(o) if isinstance(o, Decimal) else str(o)
        )
    }


def hash_pw(pw, salt):
    return hashlib.sha256((salt + pw).encode()).hexdigest()


# ---------- auth ----------
def register(b):
    uid = b["email"].strip().lower()
    salt = secrets.token_hex(8)

    try:
        users.put_item(
            Item={
                "userId": uid,
                "name": b.get("name", ""),
                "email": uid,
                "salt": salt,
                "passwordHash": hash_pw(b["password"], salt)
            },
            ConditionExpression="attribute_not_exists(userId)"
        )

    except ddb.meta.client.exceptions.ConditionalCheckFailedException:
        return resp(409, {
            "error": "Email already registered"
        })

    if TOPIC:
        # SNS: subscribe the user's email.
        # User must click the confirmation email.
        sns.subscribe(
            TopicArn=TOPIC,
            Protocol="email",
            Endpoint=uid
        )

    return resp(201, {
        "ok": True
    })


def login(b):
    uid = b["email"].strip().lower()

    u = users.get_item(
        Key={"userId": uid}
    ).get("Item")

    if not u or u["passwordHash"] != hash_pw(
        b["password"],
        u["salt"]
    ):
        return resp(401, {
            "error": "Wrong email or password"
        })

    token = secrets.token_hex(16)

    # "token" is a DynamoDB reserved word,
    # so use an expression attribute name.
    users.update_item(
        Key={"userId": uid},
        UpdateExpression="SET #tk=:t",
        ExpressionAttributeNames={
            "#tk": "token"
        },
        ExpressionAttributeValues={
            ":t": token
        }
    )

    return resp(200, {
        "token": f"{uid}:{token}",
        "name": u.get("name", "")
    })


def auth(event):
    headers = event.get("headers") or {}

    h = headers.get("authorization", "")

    if ":" not in h:
        return None

    uid, token = h.rsplit(":", 1)

    u = users.get_item(
        Key={"userId": uid}
    ).get("Item")

    return uid if u and u.get("token") == token else None


# ---------- medicines ----------
def make_doses(uid, m):
    start = datetime.strptime(
        m["startDate"],
        "%Y-%m-%d"
    ).date()

    end = min(
        datetime.strptime(
            m["endDate"],
            "%Y-%m-%d"
        ).date(),
        start + timedelta(days=30)
    )

    with logs.batch_writer() as bw:
        d = start

        while d <= end:
            for t in m["times"]:
                bw.put_item(
                    Item={
                        "userId": uid,
                        "sk": f"{d}#{t}#{m['medicineId']}",
                        "medicineId": m["medicineId"],
                        "name": m["name"],
                        "dosage": m.get("dosage", ""),
                        "status": "UPCOMING",
                        "reminded": False
                    }
                )

            d += timedelta(days=1)


def clear_upcoming(uid, mid):
    items = logs.query(
        KeyConditionExpression=Key("userId").eq(uid),
        FilterExpression=
            Attr("medicineId").eq(mid) &
            Attr("status").eq("UPCOMING")
    )["Items"]

    with logs.batch_writer() as bw:
        for i in items:
            bw.delete_item(
                Key={
                    "userId": uid,
                    "sk": i["sk"]
                }
            )


def add_med(uid, b):
    m = {
        "userId": uid,
        "medicineId": str(uuid.uuid4())[:8],
        "name": b["name"],
        "dosage": b.get("dosage", ""),
        "times": sorted(b["times"]),
        "startDate": b["startDate"],
        "endDate": b["endDate"]
    }

    meds.put_item(Item=m)

    make_doses(uid, m)

    return resp(201, m)


def edit_med(uid, mid, b):
    m = {
        "userId": uid,
        "medicineId": mid,
        "name": b["name"],
        "dosage": b.get("dosage", ""),
        "times": sorted(b["times"]),
        "startDate": b["startDate"],
        "endDate": b["endDate"]
    }

    meds.put_item(Item=m)

    clear_upcoming(uid, mid)

    make_doses(uid, m)

    return resp(200, m)


def del_med(uid, mid):
    meds.delete_item(
        Key={
            "userId": uid,
            "medicineId": mid
        }
    )

    clear_upcoming(uid, mid)

    return resp(200, {
        "ok": True
    })


# ---------- doses ----------
def today(uid):
    d = str(now().date())

    items = logs.query(
        KeyConditionExpression=
            Key("userId").eq(uid) &
            Key("sk").begins_with(d + "#")
    )["Items"]

    return resp(
        200,
        [
            {
                "key": i["sk"],
                "time": i["sk"].split("#")[1],
                "name": i["name"],
                "dosage": i.get("dosage", ""),
                "status": i["status"]
            }
            for i in items
        ]
    )


def taken(uid, b):
    logs.update_item(
        Key={
            "userId": uid,
            "sk": b["key"]
        },
        UpdateExpression="SET #s=:s, takenAt=:t",
        ExpressionAttributeNames={
            "#s": "status"
        },
        ExpressionAttributeValues={
            ":s": "TAKEN",
            ":t": now().isoformat()
        }
    )

    return resp(200, {
        "ok": True
    })


# ---------- analytics (enhancement) ----------
def analytics(uid):
    t = now().date()
    s = t - timedelta(days=6)

    items = logs.query(
        KeyConditionExpression=
            Key("userId").eq(uid) &
            Key("sk").between(
                str(s),
                f"{t}#~"
            )
    )["Items"]

    # ---------- Weekly summary ----------

    days = {
        str(s + timedelta(days=i)): [0, 0]
        for i in range(7)
    }

    slots = {}
    tk = 0
    ms = 0

    # ---------- Medicine-wise summary ----------

    medicine_stats = {}

    # ---------- Detailed dose history ----------

    history = []

    for i in items:
        d, tm, mid = i["sk"].split("#")
        status = i.get("status", "UPCOMING")

        # Ignore doses that are still upcoming
        # because they haven't become taken/missed yet.
        if status == "UPCOMING":
            continue

                # Create medicine entry if it doesn't exist
        if mid not in medicine_stats:
            medicine_stats[mid] = {
                "medicineId": mid,
                "name": i.get("name", "Unknown"),
                "taken": 0,
                "missed": 0,
                "total": 0
            }

        medicine_stats[mid]["total"] += 1

        # Every completed dose counts toward the day's total
        days[d][1] += 1

        # ---------- Taken ----------
        if status == "TAKEN":
            days[d][0] += 1
            tk += 1
            medicine_stats[mid]["taken"] += 1

        # ---------- Missed ----------
        else:
            ms += 1
            medicine_stats[mid]["missed"] += 1
            slots[tm] = slots.get(tm, 0) + 1

        # ---------- Detailed history ----------
        history.append({
            "date": d,
            "time": tm,
            "name": i.get("name", "Unknown"),
            "dosage": i.get("dosage", ""),
            "status": status
        })

    total = tk + ms

    # Add adherence percentage for each medicine
    for m in medicine_stats.values():
        m["adherence"] = round(
            m["taken"] / m["total"] * 100,
            1
        ) if m["total"] else 0

    # Sort history: newest date/time first
    history.sort(
        key=lambda x: (x["date"], x["time"]),
        reverse=True
    )

    return resp(
        200,
        {
            # Existing analytics — KEEPING THESE
            "taken": tk,
            "missed": ms,
            "total": total,
            "adherence":
                round(tk / total * 100, 1)
                if total else 0,

            "days": [
                {
                    "date": d,
                    "day": datetime.strptime(
                        d,
                        "%Y-%m-%d"
                    ).strftime("%a"),
                    "pct":
                        round(v[0] / v[1] * 100)
                        if v[1] else None
                }
                for d, v in days.items()
            ],

            "mostMissed":
                max(slots, key=slots.get)
                if slots else None,

            # NEW: medicine-wise analytics
            "medicines": list(medicine_stats.values()),

            # NEW: detailed dose history
            "history": history
        }
    )


# ---------- demo seed ----------
def seed(uid):
    """Creates a demo medicine with 7 days of fake history."""

    mid = "demo" + str(uuid.uuid4())[:4]

    t = now().date()

    times = [
        "08:00",
        "13:00",
        "20:00"
    ]

    meds.put_item(
        Item={
            "userId": uid,
            "medicineId": mid,
            "name": "Demo Vitamin",
            "dosage": "1 tablet",
            "times": times,
            "startDate": str(t - timedelta(days=7)),
            "endDate": str(t)
        }
    )

    with logs.batch_writer() as bw:
        for back in range(7, 0, -1):
            d = t - timedelta(days=back)

            for tm in times:
                miss_chance = (
                    0.45
                    if tm == "20:00"
                    else 0.1
                )

                bw.put_item(
                    Item={
                        "userId": uid,
                        "sk": f"{d}#{tm}#{mid}",
                        "medicineId": mid,
                        "name": "Demo Vitamin",
                        "dosage": "1 tablet",
                        "reminded": True,
                        "status":
                            "MISSED"
                            if random.random() < miss_chance
                            else "TAKEN"
                    }
                )

    return resp(200, {
        "ok": True
    })


# ---------- main Lambda handler ----------
def handler(event, ctx):

    request_context = event.get(
        "requestContext",
        {}
    )

    http = request_context.get(
        "http",
        {}
    )

    method = http.get(
        "method",
        ""
    )

    path = event.get(
        "rawPath",
        ""
    )


    # =========================================================
    # CORS PREFLIGHT
    # =========================================================
    # IMPORTANT:
    # This must happen BEFORE body parsing or authentication.
    # Browser sends OPTIONS before POST/PUT/DELETE requests.
    # =========================================================

    if method.upper() == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": CORS_HEADERS,
            "body": ""
        }


    try:

        # Parse request body only after OPTIONS has been handled.
        b = json.loads(
            event.get("body") or "{}"
        )


        # ---------- public routes ----------

        if path == "/register" and method == "POST":
            return register(b)

        if path == "/login" and method == "POST":
            return login(b)


        # ---------- authentication ----------

        uid = auth(event)

        if not uid:
            return resp(
                401,
                {
                    "error": "Please log in again"
                }
            )


        # ---------- medicine routes ----------

        if path == "/medicines" and method == "GET":

            return resp(
                200,
                meds.query(
                    KeyConditionExpression=
                        Key("userId").eq(uid)
                )["Items"]
            )


        if path == "/medicines" and method == "POST":
            return add_med(uid, b)


        if (
            path.startswith("/medicines/")
            and method == "PUT"
        ):
            return edit_med(
                uid,
                path.split("/")[2],
                b
            )


        if (
            path.startswith("/medicines/")
            and method == "DELETE"
        ):
            return del_med(
                uid,
                path.split("/")[2]
            )


        # ---------- dose routes ----------

        if path == "/doses/today" and method == "GET":
            return today(uid)


        if path == "/doses/taken" and method == "POST":
            return taken(uid, b)


        # ---------- analytics ----------

        if path == "/analytics" and method == "GET":
            return analytics(uid)


        # ---------- demo seed ----------

        if path == "/seed" and method == "POST":
            return seed(uid)


        # ---------- unknown route ----------

        return resp(
            404,
            {
                "error": "Not found"
            }
        )


    except KeyError as e:

        return resp(
            400,
            {
                "error": f"Missing field {e}"
            }
        )


    except Exception as e:

        # Keep CORS headers even when an unexpected
        # backend error occurs, so the browser shows
        # the real API error instead of a misleading CORS error.
        print(
            f"Unhandled error: {type(e).__name__}: {e}"
        )

        return resp(
            500,
            {
                "error": "Internal server error"
            }
        )