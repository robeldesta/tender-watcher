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
