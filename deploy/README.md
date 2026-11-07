# Deploying the tender board

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
certbot --nginx -d tenders.et -d www.tenders.et
```

To update the list, replace `/opt/tenders/site/tenders.json`. The page drops
tenders whose closing date has passed on its own, so a missed refresh never
shows closed tenders.

`tenders.json` format: `{"updated": "YYYY-MM-DD", "tenders": [{id, buyer, title,
region, place, grade, closes, time, type, firstSeen, url, source}]}` where
`type` is gov | ngo | donor | company, `grade` is like "GC-5" or "" and
`closes` is a date or "" when not published.
