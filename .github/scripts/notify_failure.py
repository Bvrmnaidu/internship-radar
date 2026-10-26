"""Send a short 'FAILED' email so a broken run is never silent. Uses the same Gmail secrets."""
import os, smtplib, datetime
from email.mime.text import MIMEText

user, pw = os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"]
to = os.environ.get("RECIPIENT_EMAIL") or user
url = os.environ.get("RUN_URL", "")
msg = MIMEText(
    "Hi Arjun,\n\nToday's job scrape FAILED, so the normal email did not go out.\n\n"
    f"Open the run to see which step failed:\n{url}\n\n"
    "You can start it again from GitHub: Actions -> Daily Job Scrape -> Run workflow.\n\n-- Your daily job bot")
msg["Subject"] = f"FAILED: Daily job scrape - {datetime.date.today():%d %b %Y}"
msg["From"], msg["To"] = user, to
with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
    s.login(user, pw)
    s.sendmail(user, to, msg.as_string())
print("Failure email sent")
