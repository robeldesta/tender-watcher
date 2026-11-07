# Deploying the tender board

Live at https://tenders.sonador.et on the ghion droplet (nginx site `tenders`, files in `/opt/tenders`).

The board is a static page (`web/index.html`) that reads `tenders.json` from the
same folder. If the JSON can't be loaded it falls back to the copy embedded in
the page, so it never shows an empty list.

On the server (ghion):

```
git clone https://github.com/robeldesta/tender-watcher /opt/tenders/repo
mkdir -p /opt/tenders/site
cp /opt/tenders/repo/web/index.html /opt/tenders/repo/web/tenders.json /opt/tenders/site/
cp /opt/tenders/repo/deploy/nginx-tenders.conf /etc/nginx/sites-available/tenders
ln -s /etc/nginx/sites-available/tenders /etc/nginx/sites-enabled/tenders
nginx -t && systemctl reload nginx
certbot --nginx -d tenders.sonador.et   # and tenders.et / www.tenders.et once they resolve
cp /opt/tenders/repo/deploy/refresh.py /opt/tenders/refresh.py
python3 -I /opt/tenders/refresh.py      # first fill of tenders.json
```

Daily refresh (server clock is UTC, so 02:30 UTC is 05:30 in Addis), in root's crontab:

```
30 2 * * * /usr/bin/python3 -I /opt/tenders/refresh.py >> /opt/tenders/refresh.log 2>&1
```

`refresh.py` reads only the public listing pages and public tender teasers on
Modern Tenders (allowed by its robots.txt), waits 2 seconds between requests,
and keeps construction works only.

To update the list, replace `/opt/tenders/site/tenders.json`. The page drops
tenders whose closing date has passed on its own, so a missed refresh never
shows closed tenders.

`tenders.json` format: `{"updated": "YYYY-MM-DD", "tenders": [{id, buyer, title,
region, place, grade, closes, time, type, firstSeen, url, source}]}` where
`type` is gov | ngo | donor | company, `grade` is like "GC-5" or "" and
`closes` is a date or "" when not published.

## Entry bot for field collectors

`entry_bot.py` is a Telegram bot. A registered collector sends a photo of a
tender notice; Claude reads it (Amharic or English, Ethiopian calendar dates are
converted in code); each tender goes to the reviewers with Approve / Reject
buttons. Approved tenders are added to `tenders.json` and the photo is published
under `/notices/` as the "See the notice" link. Nothing goes live without a
reviewer's approval. It uses long polling, so it opens no port.

Reviewers can fix a field before approving by replying to the review message,
for example `closes: 2026-11-20` or `grade: GC-5`. `/stats` shows approved
entries per collector this month (for paying collectors); the full record is
`/opt/tenders/bot/ledger.csv`. `/start` tells anyone their Telegram ID.

`refresh.py` and the bot share the lock `/opt/tenders/tenders.lock`, so neither
overwrites the other's entries.

Install on ghion:

```
cd /opt/tenders/repo && git pull
python3 -m venv /opt/tenders/venv
/opt/tenders/venv/bin/pip install anthropic
cp /opt/tenders/repo/deploy/entry_bot.py /opt/tenders/entry_bot.py
cp /opt/tenders/repo/deploy/refresh.py /opt/tenders/refresh.py
cp /opt/tenders/repo/web/index.html /opt/tenders/site/index.html
cp /opt/tenders/repo/deploy/entry-bot.env.example /opt/tenders/entry-bot.env
chmod 600 /opt/tenders/entry-bot.env
nano /opt/tenders/entry-bot.env          # token, API key, reviewer IDs
cp /opt/tenders/repo/deploy/tenders-entry-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now tenders-entry-bot
journalctl -u tenders-entry-bot -f
```

To add a collector: they send `/start` to the bot, you add the number to
`COLLECTOR_IDS` in `/opt/tenders/entry-bot.env`, then
`systemctl restart tenders-entry-bot`.
