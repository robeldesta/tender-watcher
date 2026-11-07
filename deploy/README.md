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

## Buyer website watcher

The entry bot also checks the tender pages of the buyers in `sources.json`
(government bodies, utilities, banks, donors and NGOs) once a day, after
`WATCH_HOUR` Addis time. It follows only links that look like tender notices,
respects each site's robots.txt, waits 3 seconds between requests, and sends
each new notice to the reviewers in Telegram, the same way as a collector's
photo. Nothing goes on the board until a reviewer presses Approve.

```sh
cd /opt/tenders
sudo cp ~/tender-watcher/deploy/sources.json /opt/tenders/sources.json
# see which sources load, which robots.txt blocks, and how many tender links each has
sudo bash -c 'set -a; . /opt/tenders/entry-bot.env; /opt/tenders/venv/bin/python /opt/tenders/entry_bot.py --check-sources'
# run the watcher once now instead of waiting for the morning
sudo bash -c 'set -a; . /opt/tenders/entry-bot.env; /opt/tenders/venv/bin/python /opt/tenders/entry_bot.py --watch-now'
```

Set `"enabled": false` on any source that fails or is blocked, and replace a
home page `url` with the site's own tender page once it is known. The first
run of a new source sends at most 3 notices; later runs at most 10.

## Subscriber alerts (trial and paid)

Until the web app has billing, subscribers get their matches from the same
bot. Each morning after `ALERT_HOUR` (Addis time) every active subscriber gets
the open tenders on the board that match their categories or keywords and that
they have not been sent before. A new subscriber has a 14-day free trial; when
it ends they get one message with the price (ETB 299/month or ETB 2,400/year)
and then nothing until a reviewer records the payment.

The subscriber sends `/start` to the bot and gives you the number it replies
with. Then, as a reviewer, send the bot:

```
/subscribe 123456789 vehicles name=Myagta_Trading
/subscribe 123456789 consultancy,services kw=survey,evaluation,electronics name=Delta
/paid 123456789 2027-11-22
/subs
/unsubscribe 123456789
```

`/subscribe` starts the trial and sends them every matching open tender right
away. Without `kw=`, sensible words for the category are used (for vehicles:
bus, truck, pickup, spare parts, ተሽከርካሪ, ...). Categories: construction,
goods, services, consultancy, vehicles, it, electrical, medical, sale, rental,
other.
