"""
Lambda: medicine-reminder
Handler: reminder.handler
Triggered by EventBridge every 5 minutes.
Environment variable: TOPIC_ARN
"""

import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Attr

logs = boto3.resource("dynamodb").Table("DoseLogs")
sns = boto3.client("sns")

TOPIC = os.environ["TOPIC_ARN"]
IST = timezone(timedelta(hours=5, minutes=30))


def handler(event, ctx):
    now = datetime.now(IST).replace(tzinfo=None)

    scan_kwargs = {
        "FilterExpression": Attr("status").eq("UPCOMING")
    }

    sent = 0
    missed = 0

    while True:
        response = logs.scan(**scan_kwargs)

        for item in response["Items"]:

            # DoseLogs sk format:
            # YYYY-MM-DD#HH:MM#medicineId
            date_str, time_str, _ = item["sk"].split("#")

            due = datetime.strptime(
                f"{date_str} {time_str}",
                "%Y-%m-%d %H:%M"
            )

            key = {
                "userId": item["userId"],
                "sk": item["sk"]
            }

            # ---------------------------------------------
            # More than 60 minutes overdue → MISSED
            # ---------------------------------------------
            if now - due > timedelta(minutes=60):

                logs.update_item(
                    Key=key,
                    UpdateExpression="SET #s=:m",
                    ExpressionAttributeNames={
                        "#s": "status"
                    },
                    ExpressionAttributeValues={
                        ":m": "MISSED"
                    }
                )

                missed += 1

            # ---------------------------------------------
            # Dose is due but still within 60 minutes
            # → send reminder
            # ---------------------------------------------
            elif due <= now:

                sns.publish(
                    TopicArn=TOPIC,
                    Subject="Medicine reminder",
                    Message=(
                        f"Time to take your {item['name']} "
                        f"({item.get('dosage', '')}) "
                        f"- scheduled for {time_str}."
                    )
                )

                # Record that at least one reminder was sent
                logs.update_item(
                    Key=key,
                    UpdateExpression="SET reminded=:t",
                    ExpressionAttributeValues={
                        ":t": True
                    }
                )

                sent += 1

        # Continue scanning if more DynamoDB items exist
        if "LastEvaluatedKey" not in response:
            break

        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    return {
        "reminders": sent,
        "markedMissed": missed
    }