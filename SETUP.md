# AWS setup: Smart Medicine Reminder

Use one region for everything (e.g. ap-south-1 Mumbai). Do the steps in order.

## 1. DynamoDB: 3 tables (DynamoDB > Create table, on-demand capacity)
| Table name | Partition key | Sort key |
|---|---|---|
| Users | userId (String) | none |
| Medicines | userId (String) | medicineId (String) |
| DoseLogs | userId (String) | sk (String) |

Names are case-sensitive and must match exactly.

## 2. SNS topic
1. SNS > Topics > Create topic > type **Standard**, name `MedicineReminders`.
2. Copy the **Topic ARN**.
3. (Optional test) Create subscription: protocol Email, your address, then click the confirm link in the mail.
   Registering on the website also subscribes the user's email automatically.

## 3. Lambda: medicine-api
1. Lambda > Create function > name `medicine-api`, runtime **Python 3.12**.
2. Paste `backend/api.py` into the code editor, rename the file to `api.py` (or keep lambda_function.py and set handler to match). Handler must be `api.handler`.
3. Configuration > Environment variables: `TOPIC_ARN` = your topic ARN.
4. Configuration > General: set timeout to 15 seconds.
5. Configuration > Permissions > click the role name > Add permissions > Create inline policy (JSON):
```json
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["dynamodb:*Item","dynamodb:Query","dynamodb:Scan","dynamodb:BatchWriteItem"],
  "Resource":["arn:aws:dynamodb:*:*:table/Users","arn:aws:dynamodb:*:*:table/Medicines","arn:aws:dynamodb:*:*:table/DoseLogs"]},
 {"Effect":"Allow","Action":["sns:Subscribe","sns:Publish"],"Resource":"YOUR_TOPIC_ARN"}]}
```

## 4. Lambda: medicine-reminder
1. Create function `medicine-reminder`, Python 3.12, paste `backend/reminder.py`, handler `reminder.handler`.
2. Environment variable `TOPIC_ARN` as above. Timeout 30 seconds.
3. Add the same inline policy to its role.

## 5. API Gateway (HTTP API)
1. API Gateway > Create API > **HTTP API** > Build.
2. Add integration: Lambda > `medicine-api`. Name it `medicine-api`.
3. Routes: create **ANY /{proxy+}** pointing to that integration (Delete any auto-created route if it conflicts).
4. CORS (left menu): Allowed origins `*` (or your S3 URL), Allowed methods `*`, Allowed headers `content-type, authorization`. Save.
5. Stage `$default` with auto-deploy. Copy the **Invoke URL**.

## 6. EventBridge schedule
1. EventBridge > Rules > Create rule > type **Schedule**, rate expression `rate(5 minutes)`.
2. Target: Lambda function `medicine-reminder`. Create.

## 7. Frontend on S3
1. Open `frontend/index.html` and set `API` to your Invoke URL.
2. S3 > Create bucket (uncheck "Block all public access").
3. Upload `index.html`. Properties > Static website hosting > Enable, index document `index.html`.
4. Permissions > Bucket policy:
```json
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::YOUR-BUCKET/*"}]}
```
5. Open the bucket website endpoint from the Properties tab.

## 8. Test checklist (and demo script)
1. Register, confirm the SNS subscription email, log in.
2. My medicines > add a medicine with a time 6 minutes from now (IST, 24h), dates covering today.
3. DynamoDB > DoseLogs > Explore items: show the new UPCOMING rows.
4. Wait for the EventBridge run: the reminder email arrives. (Lambda > Monitor > Logs shows each run.)
5. Dashboard > Mark as taken, then refresh DynamoDB to show status = TAKEN.
6. Analytics > Load demo data, to show adherence %, daily bars, and most-missed slot.

## Troubleshooting
- "Cannot reach the server": wrong API URL or CORS not saved.
- 500 error: check CloudWatch logs for `medicine-api`; usually a missing IAM permission or table-name typo.
- No email: the subscription must be confirmed; check spam.
- Times are handled in IST (UTC+5:30) inside both Lambdas.

## Known demo shortcuts (mention to faculty)
- All users share one SNS topic, so every subscriber gets every reminder. Per-user routing would use SNS filter policies or SES.
- Custom login with salted SHA-256 is simplified; production would use Cognito.
- The reminder Lambda uses a Scan; production would index doses by due time.
